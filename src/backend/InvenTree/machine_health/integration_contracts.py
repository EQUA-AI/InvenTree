"""Internal contracts for recorded telemetry, without source I/O or authority.

These objects are not wired to a reader or an external provider. Their source
coordinates and provenance claims do not authenticate an actor, establish a
binding's ownership, authorize any field, prove a timestamp's source-clock
origin, or prove historical completeness. A producer must establish these
independently and distinguish native and presentation clocks.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from machine_health.services.integration_history import HistoryWindow


def datetime_to_unix_ms_exact(instant):
    """Return exact epoch milliseconds; reject naive or finer-grained instants.

    This conversion does not prove the source uses the Unix epoch or that its
    clock produced the supplied datetime. A caller must establish both facts.
    """
    if not isinstance(instant, datetime) or instant.utcoffset() is None:
        raise ValueError('An aware datetime is required')
    delta = instant.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    millisecond = timedelta(milliseconds=1)
    if delta % millisecond:
        raise ValueError('Datetime is not exactly representable in milliseconds')
    return delta // millisecond


@dataclass(frozen=True, kw_only=True)
class TelemetrySourceIdentity:
    """Native source and binding coordinates in one named environment."""

    environment: str
    machine_pk: int
    source_pk: int
    binding_pk: int

    def __post_init__(self):
        """Reject invalid coordinates without normalizing them."""
        if type(self.environment) is not str or not self.environment.strip():
            raise ValueError('A nonempty source environment is required')
        for pk in (self.machine_pk, self.source_pk, self.binding_pk):
            if type(pk) is not int or pk < 1:
                raise ValueError('Telemetry source IDs must be positive integers')


@dataclass(frozen=True)
class RecordedTelemetrySample:
    """A recorded observation with explicit or unknown time provenance.

    Unknown quality, unit, source event identity and sequence remain ``None``;
    unknown origins are explicitly ``'unknown'``. Origins describe the
    producer's knowledge, not proof of a source's physical correctness.
    ``value`` is not copied or interpreted; callers must not mutate it.
    """

    source: TelemetrySourceIdentity
    observed_at: datetime
    value: object = field(repr=False)
    quality: str | None
    quality_origin: str
    unit: str | None
    unit_origin: str
    value_origin: str
    source_observation_id: str | None
    source_sequence: int | None
    time_origin: str = 'unknown'
    external_key: str | None = field(default=None, repr=False)
    observation_id_origin: str = 'unknown'
    sequence_origin: str = 'unknown'
    source_generation: str | None = field(default=None, repr=False)
    schema_version: str = field(default='aimms.telemetry.recorded.v1', init=False)

    def __post_init__(self):
        """Reject malformed coordinates, time and metadata without coercion."""
        if not isinstance(self.source, TelemetrySourceIdentity):
            raise ValueError('Telemetry source identity is required')
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError('Recorded time must be a timezone-aware datetime')
        if any(
            type(origin) is not str
            for origin in (
                self.time_origin,
                self.quality_origin,
                self.unit_origin,
                self.value_origin,
                self.observation_id_origin,
                self.sequence_origin,
            )
        ):
            raise ValueError('Telemetry origins must be plain text')
        if self.time_origin not in ('unknown', 'source_reported'):
            raise ValueError('Unrecognized recorded time origin')
        if self.external_key is not None and type(self.external_key) is not str:
            raise ValueError('External key must be text or unknown')
        if self.source_generation is not None and (
            type(self.source_generation) is not str
            or not self.source_generation.strip()
        ):
            raise ValueError('Source generation must be nonempty text or unknown')
        for origin in (self.observation_id_origin, self.sequence_origin):
            if origin not in ('unknown', 'source_reported', 'adapter_assigned'):
                raise ValueError('Unrecognized observation identity or sequence origin')
        if self.quality is not None and not isinstance(self.quality, str):
            raise ValueError('Quality must be text or unknown')
        if self.unit is not None and not isinstance(self.unit, str):
            raise ValueError('Unit must be text or unknown')
        if self.quality_origin not in (
            'unknown',
            'source_reported',
            'connector_default',
            'aimms_normalized',
        ):
            raise ValueError('Unrecognized quality origin')
        if self.value_origin not in ('unknown', 'source_reported', 'aimms_normalized'):
            raise ValueError('Unrecognized value origin')
        if self.unit_origin not in (
            'unknown',
            'source_reported',
            'binding',
            'evidence_snapshot',
        ):
            raise ValueError('Unrecognized unit origin')
        if self.source_observation_id is not None and (
            type(self.source_observation_id) is not str
            or not self.source_observation_id.strip()
        ):
            raise ValueError('Source observation ID must be nonempty text or unknown')
        if self.source_sequence is not None and type(self.source_sequence) is not int:
            raise ValueError('Source sequence must be an integer or unknown')
        for value, origin in (
            (self.quality, self.quality_origin),
            (self.unit, self.unit_origin),
            (self.source_observation_id, self.observation_id_origin),
            (self.source_sequence, self.sequence_origin),
        ):
            if value is None and origin != 'unknown':
                raise ValueError('Missing metadata cannot claim a known origin')


@dataclass(frozen=True, kw_only=True)
class RecordedTelemetrySampleV2:
    """A v1 recorded sample with a separately represented optional receipt time.

    Receipt provenance is a producer claim, not proof of the origin, clock or
    completeness of the history. The two clocks may be skewed; neither time is
    inferred from the other. This envelope does not authorize either field.
    """

    sample: RecordedTelemetrySample
    ingested_at: datetime | None = None
    ingest_time_origin: str = 'unknown'
    schema_version: str = field(default='aimms.telemetry.recorded.v2', init=False)

    def __post_init__(self):
        """Reject malformed times and unsupported provenance without coercion."""
        if not isinstance(self.sample, RecordedTelemetrySample):
            raise ValueError('A recorded telemetry sample is required')
        if self.ingested_at is not None and (
            not isinstance(self.ingested_at, datetime)
            or self.ingested_at.utcoffset() is None
        ):
            raise ValueError(
                'Receipt time must be a timezone-aware datetime or unknown'
            )
        if type(self.ingest_time_origin) is not str or self.ingest_time_origin not in (
            'unknown',
            'source_reported',
            'aimms_received',
        ):
            raise ValueError('Unrecognized receipt time origin')
        if self.ingested_at is None and self.ingest_time_origin != 'unknown':
            raise ValueError('Missing receipt time cannot claim a known origin')


@dataclass(frozen=True)
class RecordedTelemetryGap:
    """An uncovered half-open millisecond interval in the source clock.

    The lack of reported gaps does not establish completeness. A caller must
    not round microsecond sample times into a gap or infer source precision.
    """

    source: TelemetrySourceIdentity
    window: HistoryWindow
    reason: str
    source_generation: str | None = field(default=None, repr=False)
    schema_version: str = field(default='aimms.telemetry.gap.v1', init=False)

    def __post_init__(self):
        """Only known, non-sensitive gap classifications are admissible."""
        if not isinstance(self.source, TelemetrySourceIdentity):
            raise ValueError('Telemetry source identity is required')
        if not isinstance(self.window, HistoryWindow):
            raise ValueError('A valid source-clock history window is required')
        if self.source_generation is not None and (
            type(self.source_generation) is not str
            or not self.source_generation.strip()
        ):
            raise ValueError('Source generation must be nonempty text or unknown')
        if type(self.reason) is not str or self.reason not in (
            'SOURCE_UNAVAILABLE',
            'SOURCE_TIMEOUT',
            'HISTORY_UNSUPPORTED',
            'RETENTION_UNAVAILABLE',
            'READ_BUDGET_EXHAUSTED',
            'SAMPLE_BUDGET_EXHAUSTED',
            'UNRESOLVED_TIMESTAMP_TIE',
            'UNKNOWN',
        ):
            raise ValueError('Unrecognized telemetry gap reason')
