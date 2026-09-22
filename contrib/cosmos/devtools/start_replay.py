"""Point one registered station at a replay of its migrated window.

Run inside the dev server container, configured by environment variables:

    docker exec \
      -e STATION_PK=60 \
      -e WINDOW_START=2025-07-07T16:10:41.256+00:00 \
      -e WINDOW_END=2025-07-12T00:00:00+00:00 \
      inventree-inventree-dev-server-1 python3 \
      /home/inventree/src/backend/InvenTree/manage.py shell \
      -c "exec(open('/tmp/start_replay.py').read())"

Variables: STATION_PK, WINDOW_START, WINDOW_END (required); SPEED (default 1.0),
LIVE_SOURCE_PK (default 1), REPLAY_NAME (default derived from the station),
REVERT (set to 1 to put the station back on the live source).

Each station gets its *own* replay source, because the window and the anchor are
per station: they do not share a start instant, and a source carries exactly one.

What it does, and why each step is needed:

1. Creates (or updates) a ``HealthSource`` whose connector is the replay adapter,
   carrying the same endpoint and database as the live source. A separate source
   rather than a flag on the live one, so that "this station is showing a replay"
   is visible on the station's own source rather than hidden in config.

2. Moves the station from whatever source it is on to the replay source, in one
   transaction. The application allows a station exactly one active source, so
   this is a deactivate followed by an activate - and split across two
   transactions, a failure in the second half leaves the station stopped and
   unbound, serving nothing. Approvals are untouched; only bindings are rebuilt.

3. **Re-anchors the ingestion checkpoint into source time.** This is the step
   that is easy to miss. ``activate_station`` seeds a new checkpoint at
   ``now - 5 minutes`` because that is right for a live feed, but the replay
   reads 2025, over a year earlier. Left alone, every poll would ask for samples
   after a cursor that already sits beyond the data and find nothing, forever,
   with no error. Rewinding a forward-only cursor is exactly the administrative
   act that anchoring a replay is, which is why it is done here and not inside
   the adapter.

Pick WINDOW_START as the first *document* in a contiguous stretch, not an hour
boundary: these stations have empty hours, and an hour whose data starts ten
minutes in leaves the dashboard blank for ten minutes of wall time with nothing
to say why.
"""

import os
from datetime import datetime, timezone

from django.db import transaction
from django.utils import timezone as dj_timezone

from assets.activation import activate_station, activation_plan, live_status
from assets.health_models import HealthSource
from assets.ingestion_models import IngestionCheckpoint
from assets.models import AssetMachine
from machine_health.connectors.cosmos_pumphouse import bucket_of, to_epoch_ms


def _instant(name):
    """Read a required ISO-8601 environment variable as an aware UTC instant."""
    raw = os.environ.get(name)
    if not raw:
        raise SystemExit(f'{name} is required')
    moment = datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


STATION_PK = int(os.environ['STATION_PK'])
LIVE_SOURCE_PK = int(os.environ.get('LIVE_SOURCE_PK', '1'))
SPEED = float(os.environ.get('SPEED', '1.0'))
REVERT = os.environ.get('REVERT', '').strip() not in ('', '0', 'false', 'False')

station = AssetMachine.objects.get(pk=STATION_PK)
live = HealthSource.objects.get(pk=LIVE_SOURCE_PK)
station_uuid = str(station.source_entity_uuid)
now = dj_timezone.now()
REPLAY_NAME = os.environ.get('REPLAY_NAME') or f'Cosmos (replay) - {station.name}'


def move_to(source):
    """Move the station onto ``source``, in one transaction.

    The source it moves *from* is whatever it is actually on, which is not
    necessarily the live one - re-running this script starts from the replay
    itself, and activating over an active checkpoint is refused.
    """
    current = live_status(station)
    from_source = (
        HealthSource.objects.get(pk=current['source']['pk'])
        if current['activated'] and current.get('source')
        else None
    )
    with transaction.atomic():
        # Deactivate even when it is already on this source: re-running must
        # rebuild bindings against the refreshed config rather than be refused
        # for having an active checkpoint.
        if from_source is not None:
            plan = activation_plan(station, from_source)
            activate_station(
                station, from_source, expected_hash=plan['source_hash'], deactivate=True
            )
        plan = activation_plan(station, source)
        return activate_station(station, source, expected_hash=plan['source_hash'])


if REVERT:
    replay = HealthSource.objects.get(name=REPLAY_NAME)
    status = move_to(live)
    HealthSource.objects.filter(pk=replay.pk).update(active=False)
    print(f'{station.name}: reverted to {live.name} | bound={status["bound"]}')
else:
    window_start, window_end = _instant('WINDOW_START'), _instant('WINDOW_END')
    config = {
        **live.config,
        'stations': [station_uuid],
        'replay_window_start': window_start.isoformat(),
        'replay_window_end': window_end.isoformat(),
        'replay_anchor': now.isoformat(),
        'replay_speed': SPEED,
    }
    replay, created = HealthSource.objects.get_or_create(
        name=REPLAY_NAME,
        defaults={
            'source_type': live.source_type,
            'connector_type': 'cosmos_pumphouse_replay',
            'secret_ref': live.secret_ref,
            'client': live.client,
            'freshness_threshold_seconds': live.freshness_threshold_seconds,
            'config': config,
            'active': True,
        },
    )
    if not created:
        HealthSource.objects.filter(pk=replay.pk).update(config=config, active=True)
        replay.refresh_from_db()

    status = move_to(replay)

    # Rewind the cursor into source time - see the module docstring.
    start_ms = to_epoch_ms(window_start) - 1
    IngestionCheckpoint.objects.filter(
        source=replay, station=station, station_uuid=station_uuid, active=True
    ).update(
        sub_time_period=start_ms,
        hour_bucket=str(bucket_of(start_ms)),
        scan_until=None,
        continuation_token='',
        last_error_code='',
        lease_until=None,
    )
    hours = (window_end - window_start).total_seconds() / 3600
    print(
        f'{station.name}: source pk={replay.pk} ({"created" if created else "updated"}) '
        f'| bound={status["bound"]} approved={status["approved"]}\n'
        f'   replaying {window_start.isoformat()} -> {window_end.isoformat()} '
        f'({hours:.1f}h) at {SPEED}x, anchored {now.isoformat(timespec="seconds")}'
    )
