"""Replay a past window of pumphouse snapshots as though it were happening now.

The migrated estate holds ten days of real plant history (2025-07-02 to 07-12)
stored at its true observation times. The operator dashboard renders *now*: the
mimic asks for the latest polled value and marks anything older than the source's
freshness threshold as stale, and the trend panels ask for the last hour. Pointed
at a fourteen-month-old window, every panel is therefore empty and correct.

This adapter closes that gap without touching the record. It reads the same
container as :class:`CosmosPumphouseConnector` - the same documents, at the same
true timestamps - and applies one affine map on the way out:

    source_time = window_start + (wall_time - anchor) * speed

Readings are re-stamped to wall time as they leave, so the dashboard sees a live
feed of real plant behaviour instead of a flat line.

Three properties are deliberate:

**Nothing is written and no stored timestamp is rewritten.** The shift exists
only in the objects this adapter returns. The migrated documents keep the
observation times the plant gave them, which is what makes the migration
reconcilable against Cassandra. Delete this adapter and the record is unchanged.

**The checkpoint stays in source time.** ``poll`` hands its parent a *mapped*
``now`` and lets it walk the real hour buckets, so ``sub_time_period`` continues
to mean "the last real sample accepted" and stays forward-only. Only the readings
are re-stamped; the documents that drive checkpoint advancement are not.

**The replay ends rather than loops.** When wall time runs past the window the
map clamps to ``window_end``, the poll finds nothing new and the dashboard goes
stale - the honest outcome. Looping would mean rewinding a forward-only
checkpoint on every lap, which is an administrative act (re-anchor the source),
not something an adapter should do to itself behind an operator's back.

Because what this shows is *not* the true observation time, it is a separate
registered connector rather than a mode of the real one: a source is either
replaying or it is not, and which one is visible on the source itself.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from machine_health.connectors.base import MAX_TREND_WINDOW_SECONDS, Reading, register
from machine_health.connectors.cosmos_pumphouse import (
    HOUR_MS,
    CosmosConfigError,
    CosmosPumphouseConnector,
    bucket_of,
    to_epoch_ms,
)
from machine_health.connectors.pumphouse_payload import flatten_snapshot

logger = logging.getLogger('inventree')


def _parse(value, field):
    """Read one ISO-8601 config value as an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        raise CosmosConfigError(f'Replay source needs {field}.')
    text = value.strip().replace('Z', '+00:00')
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CosmosConfigError(f'Replay {field} is not an ISO-8601 instant.') from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


@register
class CosmosPumphouseReplayConnector(CosmosPumphouseConnector):
    """Serve a historical window of one station as a live feed."""

    key = 'cosmos_pumphouse_replay'

    # ------------------------------------------------------------------
    # The time map

    @property
    def replay(self) -> tuple[datetime, datetime, datetime, float]:
        """Return ``(window_start, window_end, anchor, speed)`` from config."""
        config = self.config
        start = _parse(config.get('replay_window_start'), 'replay_window_start')
        end = _parse(config.get('replay_window_end'), 'replay_window_end')
        anchor = _parse(config.get('replay_anchor'), 'replay_anchor')

        speed = config.get('replay_speed', 1.0)
        try:
            speed = float(speed)
        except (TypeError, ValueError) as exc:
            raise CosmosConfigError('Replay speed must be a number.') from exc

        if end <= start:
            raise CosmosConfigError('Replay window must end after it starts.')
        if not speed > 0:
            raise CosmosConfigError('Replay speed must be greater than zero.')
        return start, end, anchor, speed

    def to_source(self, moment: datetime) -> datetime:
        """Map a wall-clock instant onto the replayed window.

        Clamped at both ends: before the anchor the replay has not begun, and
        after the window it has finished. Clamping is what makes the end of a
        replay look like a source that stopped reporting, rather than one that
        silently jumped back to the beginning.
        """
        start, end, anchor, speed = self.replay
        mapped = start + timedelta(seconds=(moment - anchor).total_seconds() * speed)
        return min(max(mapped, start), end)

    def to_wall(self, moment: datetime) -> datetime:
        """Map an instant inside the replayed window back to wall-clock time."""
        start, _end, anchor, speed = self.replay
        return anchor + timedelta(seconds=(moment - start).total_seconds() / speed)

    def _restamp(self, reading: Reading) -> Reading:
        """Return the reading carrying its wall-clock presentation time."""
        return replace(reading, observed_at=self.to_wall(reading.observed_at))

    # ------------------------------------------------------------------
    # Reads

    def read_window(self, external_key: str, start, end, *, max_samples=None):
        """Return the replayed samples for one tag, stamped in wall time."""
        source_start, source_end = self.to_source(start), self.to_source(end)

        # A sped-up replay compresses a long stretch of source into a short wall
        # window, so a request the caller already bounded can still exceed the
        # service limit once mapped. Say so rather than letting the parent report
        # a window the caller never asked for.
        span = (source_end - source_start).total_seconds()
        if span > MAX_TREND_WINDOW_SECONDS:
            raise ValueError(
                f'At {self.replay[3]}x this window covers '
                f'{span / 3600:.1f} source hours, over the '
                f'{MAX_TREND_WINDOW_SECONDS // 3600}-hour limit'
            )

        readings = super().read_window(
            external_key, source_start, source_end, max_samples=max_samples
        )
        return [self._restamp(reading) for reading in readings]

    def read_windows(self, external_keys, start, end, *, max_samples=None):
        """Return replayed samples for many tags, stamped in wall time."""
        source_start, source_end = self.to_source(start), self.to_source(end)
        span = (source_end - source_start).total_seconds()
        if span > MAX_TREND_WINDOW_SECONDS:
            raise ValueError(
                f'At {self.replay[3]}x this window covers '
                f'{span / 3600:.1f} source hours, over the '
                f'{MAX_TREND_WINDOW_SECONDS // 3600}-hour limit'
            )
        batched = super().read_windows(
            external_keys, source_start, source_end, max_samples=max_samples
        )
        return {
            key: [self._restamp(reading) for reading in readings]
            for key, readings in batched.items()
        }

    def read_latest(self, external_keys=None) -> list[Reading]:
        """Return the newest snapshot at or before the replay's current instant.

        Deliberately not the newest in the bucket, which is what the parent
        returns: that snapshot is in the replay's future, and showing it would
        run the dashboard ahead of the feed it is meant to be following.
        """
        now_ms = to_epoch_ms(self.to_source(datetime.now(tz=timezone.utc)))

        document = None
        for bucket in (bucket_of(now_ms), bucket_of(now_ms) - HOUR_MS):
            for row in self.documents_in_bucket(
                self.station, bucket, bucket, now_ms + 1
            ):
                document = row
            if document is not None:
                break

        if document is None:
            return []

        readings = [self._restamp(r) for r in flatten_snapshot(document)]
        if external_keys is None:
            return readings
        wanted = {str(key) for key in external_keys}
        return [r for r in readings if r.external_key in wanted]

    def poll(self, checkpoint, *, now=None, max_documents=None, on_scanned=None):
        """Yield ``(document, readings)`` up to the replay's current instant.

        The document is handed on untouched so the checkpoint keeps advancing in
        real source time; only the readings carry wall-clock stamps.
        """
        source_now = self.to_source(now or datetime.now(tz=timezone.utc))
        for document, readings in super().poll(
            checkpoint,
            now=source_now,
            max_documents=max_documents,
            on_scanned=on_scanned,
        ):
            yield document, [self._restamp(reading) for reading in readings]
