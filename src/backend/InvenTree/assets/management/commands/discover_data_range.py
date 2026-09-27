"""Record which span of history a source actually holds, per station.

    manage.py discover_data_range --source 1 --from 2025-07-01 --to 2025-07-13

Why this is stored rather than asked for
----------------------------------------
The readings container is partitioned on station and hour bucket, and
cross-partition queries are deliberately disabled, so there is no cheap "select
min(time)" to run: the only way to find the edges is to probe hour buckets one
at a time. That is fine once, and far too slow for a page load - which is
exactly the shape of thing to compute ahead of time and cache.

The result lands on the source's own config, keyed by station, and is read back
by the data-range endpoint the chart uses to bound its date picker. A picker
that lets an operator choose a week nobody recorded produces an empty chart and
no explanation; one bounded by the real edges cannot.

Probing is per hour bucket because gaps are real - these stations have hours,
and in one case whole days, where the plant reported nothing. A coarse scan
that sampled one hour per day would silently skip a day whose data sits in the
hours it did not look at, so every bucket in the span is checked.
"""

from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError

from assets.health_models import HealthSource
from machine_health.connectors.base import (
    PUMPHOUSE_CONNECTOR_TYPES,
    pumphouse_connector_class,
)
from machine_health.connectors.cosmos_pumphouse import bucket_of, to_epoch_ms

#: Never probe more than this many buckets in one run, whatever was asked for.
MAX_BUCKETS = 24 * 45


def _instant(value, label):
    """Read a date or datetime argument as an aware UTC instant."""
    try:
        moment = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError as exc:
        raise CommandError(f'{label} is not an ISO-8601 date or instant') from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class Command(BaseCommand):
    """Probe a source for the span of history it holds, and record it."""

    help = "Record each station's earliest and latest reading for a source."

    def add_arguments(self, parser):
        """Take the source and the span to search within."""
        parser.add_argument('--source', type=int, required=True)
        parser.add_argument('--from', dest='start', required=True)
        parser.add_argument('--to', dest='end', required=True)
        parser.add_argument(
            '--station',
            action='append',
            help='Limit to one station uuid; repeatable. Defaults to all.',
        )

    def handle(self, *args, **options):
        """Probe every hour bucket in the span and store the edges found."""
        source = HealthSource.objects.filter(pk=options['source']).first()
        if source is None or source.connector_type not in PUMPHOUSE_CONNECTOR_TYPES:
            raise CommandError('Source must be a registered pumphouse connector.')

        start, end = (
            _instant(options['start'], '--from'),
            _instant(options['end'], '--to'),
        )
        if end <= start:
            raise CommandError('--to must be after --from')
        buckets = int((end - start).total_seconds() // 3600)
        if buckets > MAX_BUCKETS:
            raise CommandError(
                f'That span is {buckets} hours; at most {MAX_BUCKETS} may be probed.'
            )

        stations = options.get('station') or (source.config or {}).get('stations') or []
        if not stations:
            raise CommandError('Source configures no stations.')

        connector_class = pumphouse_connector_class(source.connector_type)
        ranges = dict((source.config or {}).get('data_ranges') or {})

        for station in stations:
            connector = connector_class(source, station_uuid=station)
            first = last = None
            covered = 0
            try:
                for index in range(buckets):
                    bucket = bucket_of(to_epoch_ms(start + timedelta(hours=index)))
                    newest = connector.latest_document(station, bucket)
                    if newest is None:
                        continue
                    covered += 1
                    if last is None or int(newest['sub_time_period']) > last:
                        last = int(newest['sub_time_period'])
                    if first is None:
                        # The oldest sample of the first populated hour; the
                        # bucket's newest is not its earliest.
                        oldest = next(
                            iter(
                                connector.documents_in_bucket(
                                    station, bucket, bucket, bucket + 3_600_000
                                )
                            ),
                            None,
                        )
                        if oldest is not None:
                            first = int(oldest['sub_time_period'])
            finally:
                connector.close()

            if first is None or last is None:
                self.stdout.write(f'{station}: no data in the probed span')
                ranges.pop(station, None)
                continue

            ranges[station] = {
                'from': datetime.fromtimestamp(
                    first / 1000, tz=timezone.utc
                ).isoformat(),
                'to': datetime.fromtimestamp(last / 1000, tz=timezone.utc).isoformat(),
                'hours_with_data': covered,
                'hours_probed': buckets,
                'discovered_at': datetime.now(tz=timezone.utc).isoformat(),
            }
            self.stdout.write(
                f'{station}: {ranges[station]["from"][:19]} -> '
                f'{ranges[station]["to"][:19]} '
                f'({covered} of {buckets} hours hold data)'
            )

        config = dict(source.config or {})
        config['data_ranges'] = ranges
        HealthSource.objects.filter(pk=source.pk).update(config=config)
        self.stdout.write(
            f'Recorded {len(ranges)} station range(s) on source {source.pk}.'
        )
