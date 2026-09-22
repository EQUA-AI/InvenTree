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
