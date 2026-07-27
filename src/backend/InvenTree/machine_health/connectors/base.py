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

#: Trend reads are bounded so one request cannot pull a historian dry.
MAX_TREND_SAMPLES = 2000
MAX_TREND_WINDOW_SECONDS = 30 * 24 * 3600


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


_REGISTRY: dict[str, type[HealthConnector]] = {}


def register(connector_class: type[HealthConnector]) -> type[HealthConnector]:
    """Register a connector implementation under its ``key``."""
    if not connector_class.key:
        raise ValueError('A connector must declare a key')
    _REGISTRY[connector_class.key] = connector_class
    return connector_class


def get_connector(source):
    """Return the adapter configured for a source, or None when it has none.

    A source with an unregistered connector returns None rather than falling back
    to some default: silently reading a machine through the wrong adapter would
    be worse than showing the source as unconfigured.
    """
    connector_class = _REGISTRY.get(source.connector_type)
    return connector_class(source) if connector_class else None


def bounded_window(start, end, *, max_samples=None) -> tuple[datetime, datetime, int]:
    """Clamp a requested trend window and sample count to the service limits."""
    if end < start:
        raise ValueError('Trend window end must not precede its start')

    span = (end - start).total_seconds()
    if span > MAX_TREND_WINDOW_SECONDS:
        raise ValueError(
            f'Trend window may not exceed {MAX_TREND_WINDOW_SECONDS // 3600} hours'
        )

    samples = min(int(max_samples or MAX_TREND_SAMPLES), MAX_TREND_SAMPLES)
    return start, end, max(samples, 1)
