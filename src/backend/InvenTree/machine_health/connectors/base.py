"""The normalized interface every health connector implements.

Connectors read; they never write. Nothing in this interface can command a PLC,
acknowledge an alarm in SCADA or change a setpoint - a compromised AIMMS
deployment must not be able to touch a control system.

Implementations resolve credentials from the deployment's secret store using
``source.secret_ref`` at call time. A credential must never be stored on, or
returned through, a ``HealthSource``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

#: Trend reads are bounded so one request cannot pull a historian dry.
#:
#: The bound that matters is the *window*; the sample count follows from it.
#: How many samples a window holds depends on how fast the source writes, and
#: PH_3 writes a snapshot every five seconds - verified against the pilot
#: excerpt, whose consecutive ``sub_time_period`` values are exactly 5000 ms
#: apart. Six hours is therefore 4320 samples.
#:
#: The two are derived from one another on purpose. A sample cap lower than the
#: window implies is not a safety margin, it is a broken promise: every
#: full-length window would come back flagged as truncated, and the UI would be
#: offering a range the server can never actually serve. The old pair (30 days,
#: 2000 samples) was exactly that - at this cadence 2000 samples is 2.8 hours,
#: so a "last 30 days" request returned the oldest 2.8 hours and stopped.
EXPECTED_SAMPLE_INTERVAL_SECONDS = 5
MAX_TREND_WINDOW_SECONDS = 6 * 3600
MAX_TREND_SAMPLES = MAX_TREND_WINDOW_SECONDS // EXPECTED_SAMPLE_INTERVAL_SECONDS

#: Bounds for a *sampled* series read, which is a different shape of request.
#:
#: A trend reads every snapshot in its window, so its cost grows with the
#: window and six hours is the most any source can serve. A series asks for a
#: fixed number of points across the window instead, one snapshot each, so its
#: cost is the point count and the window can be a day. The two must not be
#: confused: a day read as a trend would be 17,280 whole-station documents.
MAX_SERIES_WINDOW_SECONDS = 24 * 3600
MAX_SERIES_POINTS = 360
MIN_SERIES_POINTS = 24
DEFAULT_SERIES_POINTS = 240

#: The largest window a series still reads *completely*, in snapshots. Below
#: this every reading is returned and the chart draws the plant's own cadence;
#: above it the window is sampled. Measured against the live account, twenty
#: minutes of 50 KB snapshots returns in about twelve seconds, which is as long
#: as a page should ever wait for one chart.
COMPLETE_READ_MAX_DOCUMENTS = 240


@dataclass(frozen=True)
class Reading:
    """One normalized observation from a source."""

    external_key: str
    value: object
    observed_at: datetime
    quality: str = 'good'
    sequence: int | None = None

    def as_dict(self) -> dict:
        """Shape accepted by ``services.ingestion.ingest_readings``."""
        return {
            'external_key': self.external_key,
            'value': self.value,
            'observed_at': self.observed_at,
            'quality': self.quality,
            'sequence': self.sequence,
        }


@dataclass(frozen=True)
class SampledWindow:
    """What a sampled read returns: one reading per slot per key, and its cost."""

    #: External key to its readings, oldest first, at most one per slot.
    readings: dict[str, list[Reading]]
    #: Distinct snapshots the read fetched. What the source was actually asked
    #: for, so a caller can say how a window of a day became a few hundred points.
    documents_read: int
    #: How many slots the window was cut into.
    slots: int


def slot_edges(
    start: datetime, end: datetime, slots: int
) -> list[tuple[datetime, datetime]]:
    """Cut ``[start, end)`` into ``slots`` equal half-open parts, oldest first.

    The last slot always ends exactly at ``end`` so that rounding cannot leave a
    sliver of the window unread.
    """
    if slots < 1:
        raise ValueError('A sampled window needs at least one slot')
    if end <= start:
        raise ValueError('Trend window end must not precede its start')
    width = (end - start) / slots
    edges = []
    for index in range(slots):
        slot_start = start + width * index
        slot_end = end if index == slots - 1 else start + width * (index + 1)
        edges.append((slot_start, slot_end))
    return edges


class HealthConnector(abc.ABC):
    """Read-only adapter over one industrial data platform."""

    #: Registry key stored on ``HealthSource.connector_type``.
    key: str = ''

    def __init__(self, source):
        """Bind the adapter to its configured source."""
        self.source = source

    @abc.abstractmethod
    def check(self) -> tuple[bool, str]:
        """Probe reachability. Returns ``(ok, redacted_error_code)``."""

    @abc.abstractmethod
    def read_latest(self, external_keys) -> list[Reading]:
        """Return the current value for each requested tag."""

    def read_window(self, external_key: str, start, end, *, max_samples=None):
        """Return bounded historical samples for one tag.

        Optional: a source that cannot serve history raises
        :class:`NotImplementedError` and the UI hides its sparkline rather than
        fabricating a trend.
        """
        raise NotImplementedError(
            f'{type(self).__name__} cannot read historical windows'
        )

    def read_windows(self, external_keys, start, end, *, max_samples=None):
        """Return bounded samples for several tags over one window.

        Defaults to one :meth:`read_window` per tag, which is correct but is the
        thing worth avoiding: a source whose unit of storage is a whole-station
        snapshot pays the full scan once per tag, so a page of thirty sparklines
        reads and parses the same documents thirty times. Such a connector should
        override this and make a single pass.

        Returns:
            A mapping of external key to its readings. A key with no data maps
            to an empty list rather than being absent.
        """
        return {
            key: self.read_window(key, start, end, max_samples=max_samples)
            for key in external_keys
        }

    def sample_windows(self, external_keys, start, end, *, slots: int) -> SampledWindow:
        """Return one reading per slot for several tags across one window.

        The window is cut into ``slots`` equal parts and each part contributes
        the *first* reading at or after its start - a real observation, never an
        average or an interpolation, so a point on the resulting line is always
        something the plant reported at that moment. A slot the source has no
        reading for contributes nothing, and the gap shows.

        This default reads the whole window through :meth:`read_windows` and
        keeps one reading per slot, which is correct for any source but only as
        cheap as a full read. A source whose history is expensive to scan - one
        that stores a whole-station snapshot per sample - should override this
        with a read that fetches one snapshot per slot and nothing else.

        Raises:
            ValueError: the window exceeds what a full read may cover.
        """
        keys = [str(key) for key in external_keys]
        if not keys or slots < 1:
            return SampledWindow({key: [] for key in keys}, documents_read=0, slots=0)

        # The bound is this class's promise, not something to hope the
        # connector's read_windows checks for itself.
        start, end, _samples = bounded_window(start, end)
        readings = self.read_windows(keys, start, end, max_samples=MAX_TREND_SAMPLES)
        edges = slot_edges(start, end, slots)

        sampled: dict[str, list[Reading]] = {}
        stamps: set[datetime] = set()
        for key in keys:
            ordered = sorted(readings.get(key) or [], key=lambda r: r.observed_at)
            chosen: list[Reading] = []
            index = 0
            for slot_start, slot_end in edges:
                while index < len(ordered) and ordered[index].observed_at < slot_start:
                    index += 1
                if index < len(ordered) and ordered[index].observed_at < slot_end:
                    chosen.append(ordered[index])
                    stamps.add(ordered[index].observed_at)
            sampled[key] = chosen

        return SampledWindow(sampled, documents_read=len(stamps), slots=len(edges))

    def subscribe(self, handler):
        """Optional push subscription. Not required for polling sources."""
        raise NotImplementedError(f'{type(self).__name__} does not support subscribe')

    def close(self):
        """Release any resources owned by an adapter after a read."""
        return None


_REGISTRY: dict[str, type[HealthConnector]] = {}
ConnectorType = TypeVar('ConnectorType', bound=HealthConnector)

#: Modules whose import side effect is registering a built-in adapter. They are
#: imported on first lookup rather than from this module, because a connector may
#: import models and this module is imported while Django is still loading apps.
_BUILTIN_MODULES = (
    'machine_health.connectors.cosmos_pumphouse',
    'machine_health.connectors.cosmos_replay',
)
_loaded = False

#: Adapters that read a pumphouse station out of a Cosmos container. They share a
#: constructor signature and an ingestion checkpoint, so every caller that
#: resolves one must accept any of them - naming a single key would silently skip
#: a station whose source is configured for one of the others.
PUMPHOUSE_CONNECTOR_TYPES = frozenset({'cosmos_pumphouse', 'cosmos_pumphouse_replay'})


def pumphouse_connector_class(connector_type: str):
    """Return the registered pumphouse adapter class, or None if unregistered."""
    if connector_type not in PUMPHOUSE_CONNECTOR_TYPES:
        return None
    load_builtin_connectors()
    return _REGISTRY.get(connector_type)


def register(connector_class: type[ConnectorType]) -> type[ConnectorType]:
    """Register a connector implementation under its ``key``."""
    if not connector_class.key:
        raise ValueError('A connector must declare a key')
    _REGISTRY[connector_class.key] = connector_class
    return connector_class


def load_builtin_connectors() -> None:
    """Import the adapters shipped with the application, once."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    import importlib

    for module in _BUILTIN_MODULES:
        importlib.import_module(module)


def get_connector(source, *, machine=None):
    """Return the adapter configured for a source, or None when it has none.

    A source with an unregistered connector returns None rather than falling back
    to some default: silently reading a machine through the wrong adapter would
    be worse than showing the source as unconfigured.
    """
    load_builtin_connectors()
    connector_class = _REGISTRY.get(source.connector_type)
    if (
        connector_class
        and source.connector_type in PUMPHOUSE_CONNECTOR_TYPES
        and machine is not None
    ):
        from assets.ingestion_models import IngestionCheckpoint

        station = machine if machine.asset_type == 'pumphouse' else machine.parent
        if (
            station is None
            or station.asset_type != 'pumphouse'
            or station.client_id != machine.client_id
            or not station.active
        ):
            return None
        checkpoint = IngestionCheckpoint.objects.filter(
            source=source,
            station=station,
            station_uuid=str(station.source_entity_uuid),
            active=True,
        ).first()
        if checkpoint is None:
            return None
        return connector_class(source, station_uuid=checkpoint.station_uuid)
    return connector_class(source) if connector_class else None


def bounded_window(
    start, end, *, max_samples=None, ceiling=MAX_TREND_SAMPLES
) -> tuple[datetime, datetime, int]:
    """Clamp a requested trend window and sample count to the service limits.

    ``ceiling`` exists so a caller can deliberately ask for one sample more than
    it intends to return. Without that, a read that stops exactly on the cap is
    indistinguishable from one that happened to contain exactly that many
    samples, and truncation can never be reported - the chart would silently drop
    data and claim it was complete.
    """
    if end < start:
        raise ValueError('Trend window end must not precede its start')

    span = (end - start).total_seconds()
    if span > MAX_TREND_WINDOW_SECONDS:
        raise ValueError(
            f'Trend window may not exceed {MAX_TREND_WINDOW_SECONDS // 3600} hours'
        )

    samples = min(int(max_samples or ceiling), ceiling)
    return start, end, max(samples, 1)
