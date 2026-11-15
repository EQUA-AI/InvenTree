"""Source-authorized immutable fixture history, never a live historian adapter.

The snapshot, mapping and generation are synthetic evidence only. Real-source
identity, clock, mapping effective time and capacity remain separately gated.
No SDK, credential, database, source discovery or operational writer is used.
"""

import hashlib
import time
from dataclasses import dataclass, field
from itertools import pairwise

from machine_health.integration_contracts import (
    TelemetrySourceIdentity,
    datetime_to_unix_ms_exact,
)
from machine_health.services.integration_history import (
    MAX_HISTORY_READS,
    MAX_HISTORY_SAMPLES_PER_READ,
    MAX_HISTORY_SAMPLES_TOTAL,
    HistoryAuthorizationError,
    HistoryGap,
    HistoryPage,
    HistoryProtocolError,
    HistoryReadContext,
    HistoryResult,
    HistorySample,
    HistorySourceUnavailableError,
    HistoryWindow,
    traverse_scoped_history,
)
from machine_health.services.integration_s6_wire import (
    MAX_HISTORY_BYTES,
    bounded_json,
    decode_observation,
    encode_observation,
)

HOUR_MS = 60 * 60 * 1000
MAX_BUCKETS_PER_FIXTURE_READ = 7
MAX_FIXTURE_OBSERVATIONS = 20000
_GAP_REASONS = frozenset({
    'SOURCE_UNAVAILABLE',
    'SOURCE_TIMEOUT',
    'HISTORY_UNSUPPORTED',
    'RETENTION_UNAVAILABLE',
    'READ_BUDGET_EXHAUSTED',
    'SAMPLE_BUDGET_EXHAUSTED',
    'UNRESOLVED_TIMESTAMP_TIE',
    'UNKNOWN',
})


class HistoryCapabilityError(Exception):
    """A source cannot qualify this requested lossless history capability."""


def window_key(window):
    """Detach strict source-clock interval coordinates without coercion."""
    if type(window) is not HistoryWindow:
        raise ValueError('A strict source-clock window is required')
    HistoryWindow.__post_init__(window)
    return window.start_ms, window.end_ms


def source_key(source):
    """Detach exact native identity coordinates in their named environment."""
    if type(source) is not TelemetrySourceIdentity:
        raise ValueError('A strict telemetry source is required')
    TelemetrySourceIdentity.__post_init__(source)
    return source.environment, source.machine_pk, source.source_pk, source.binding_pk


def context_key(context):
    """Bind the authenticated actor independently of the source coordinates."""
    if type(context) is not HistoryReadContext:
        raise ValueError('A strict history context is required')
    HistoryReadContext.__post_init__(context)
    return (
        context.environment,
        context.actor_pk,
        context.machine_pk,
        context.source_pk,
        context.binding_pk,
        context.generation,
    )


@dataclass(frozen=True, kw_only=True)
class FixtureMapping:
    """An explicit local fixture association, not real historical attribution."""

    client_pk: int
    station_pk: int
    dictionary_point_pk: int
    station_uuid: str = field(repr=False)
    external_key: str = field(repr=False)
    unit: str | None
    dictionary_hash: str = field(repr=False)
    effective_window: HistoryWindow
    status: str = 'verified'
    evidence_kind: str = field(default='synthetic_fixture', init=False)

    def __post_init__(self):
        """Validate exact coordinates, reviewed meaning and explicit time bounds."""
        for value in (self.client_pk, self.station_pk, self.dictionary_point_pk):
            if type(value) is not int or value < 1:
                raise ValueError('Positive mapping coordinates are required')
        for value in (self.station_uuid, self.external_key, self.dictionary_hash):
            if type(value) is not str or not value.strip():
                raise ValueError('Nonempty fixture mapping coordinates are required')
        if self.unit is not None and type(self.unit) is not str:
            raise ValueError('A literal unit or unknown is required')
        if type(self.status) is not str or self.status not in (
            'verified',
            'unknown',
            'approximate',
        ):
            raise ValueError('An explicit mapping status is required')
        if (
            type(self.evidence_kind) is not str
            or self.evidence_kind != 'synthetic_fixture'
        ):
            raise ValueError('Only synthetic fixture evidence is supported')
        window_key(self.effective_window)

    def as_dict(self):
        """Return detached metadata; this projection is not an authority token."""
        FixtureMapping.__post_init__(self)
        return {
            'client_pk': self.client_pk,
            'station_pk': self.station_pk,
            'dictionary_point_pk': self.dictionary_point_pk,
            'station_uuid': self.station_uuid,
            'external_key': self.external_key,
            'unit': self.unit,
            'dictionary_hash': self.dictionary_hash,
            'effective_window': list(window_key(self.effective_window)),
            'status': self.status,
            'evidence_kind': self.evidence_kind,
        }


@dataclass(frozen=True)
class FixtureHistoryRead:
    """One bound local observation; bytes are not a cursor or authority grant."""

    context: HistoryReadContext = field(repr=False)
    requested_window: HistoryWindow
    observations: tuple[bytes, ...] = field(repr=False)
    gaps: tuple[HistoryGap, ...]
    fixture_fingerprint: str = field(repr=False)
    queries: tuple = field(repr=False)
    schema_version: str = field(default='aimms.s6.fixture-read.v1', init=False)

    @property
    def complete(self):
        """Completeness is scoped to the adapter-validated fixture invocation."""
        return not self.gaps


_READ_TYPE = FixtureHistoryRead


@dataclass(frozen=True, kw_only=True)
class FixtureHistoryAdapter:
    """A fixed bounded fixture source with independent exact page truth."""

    source: TelemetrySourceIdentity
    mapping: FixtureMapping
    coverage: HistoryWindow
    generation: str = field(repr=False)
    observations: tuple[bytes, ...] = field(repr=False)
    unavailable: tuple[HistoryGap, ...] = ()
    _rows: tuple[HistorySample, ...] = field(init=False, repr=False)
    _seal: str = field(init=False, repr=False)

    @classmethod
    def from_records(cls, *, records, **kwargs):
        """Capture producer-shaped records before any history pages are built."""
        if type(records) is not tuple or len(records) > MAX_FIXTURE_OBSERVATIONS:
            raise ValueError('A bounded tuple of fixture records is required')
        return cls(
            observations=tuple(encode_observation(record) for record in records),
            **kwargs,
        )

    def __post_init__(self):
        """Qualify fixture identity, source clock and immutable snapshot data."""
        coordinates = source_key(self.source)
        window_key(self.coverage)
        if type(self.mapping) is not FixtureMapping:
            raise ValueError('An explicit fixture mapping is required')
        FixtureMapping.__post_init__(self.mapping)
        if type(self.generation) is not str or not self.generation.strip():
            raise HistoryCapabilityError('History generation is unavailable')
        if (
            type(self.observations) is not tuple
            or len(self.observations) > MAX_FIXTURE_OBSERVATIONS
        ):
            raise ValueError('A bounded fixture snapshot is required')
        if (
            sum(len(payload) for payload in self.observations if type(payload) is bytes)
            > MAX_HISTORY_BYTES
        ):
            raise ValueError('Fixture snapshot exceeds the byte budget')
        rows = []
        seen = set()
        for payload in self.observations:
            sample = decode_observation(payload).sample
            if (
                source_key(sample.source) != coordinates
                or sample.source_generation != self.generation
                or sample.time_origin != 'source_reported'
                or sample.source_observation_id is None
                or sample.observation_id_origin == 'unknown'
                or sample.external_key != self.mapping.external_key
                or sample.unit != self.mapping.unit
            ):
                raise HistoryCapabilityError(
                    'Fixture history provenance is unavailable'
                )
            try:
                instant = datetime_to_unix_ms_exact(sample.observed_at)
            except ValueError:
                raise HistoryCapabilityError(
                    'Fixture source precision is unsupported'
                ) from None
            if not self.coverage.start_ms <= instant < self.coverage.end_ms:
                raise HistoryProtocolError(
                    'Fixture observation is outside source coverage'
                )
            if sample.source_observation_id in seen:
                raise HistoryProtocolError('Fixture observation identity repeats')
            seen.add(sample.source_observation_id)
            rows.append(
                HistorySample(
                    sample.source_observation_id,
                    instant,
                    payload,
                    sample.quality,
                    sample.unit,
                )
            )
        rows.sort(key=lambda row: (row.observed_ms, row.source_id))
        object.__setattr__(self, '_rows', tuple(rows))
        self._gap_keys()
        object.__setattr__(self, '_seal', self.fingerprint())

    def _gap_keys(self):
        if type(self.unavailable) is not tuple:
            raise ValueError('Fixture gaps must be a tuple')
        gaps = []
        for gap in self.unavailable:
            if type(gap) is not HistoryGap:
                raise ValueError('An explicit fixture gap is required')
            start, end = window_key(gap.window)
            if not self.coverage.start_ms <= start < end <= self.coverage.end_ms:
                raise ValueError('Fixture gap is outside source coverage')
            HistorySourceUnavailableError(gap.reason)
            gaps.append((start, end, gap.reason))
        gaps.sort()
        if any(left[1] > right[0] for left, right in pairwise(gaps)):
            raise ValueError('Fixture gaps overlap')
        return gaps

    def fingerprint(self):
        """Bind literal snapshot content and mapping, not merely a generation label."""
        if type(self.generation) is not str or not self.generation.strip():
            raise ValueError('A strict fixture generation is required')
        if type(self.observations) is not tuple or type(self._rows) is not tuple:
            raise ValueError('A strict fixture snapshot is required')
        if any(type(payload) is not bytes for payload in self.observations):
            raise ValueError('Canonical fixture bytes are required')
        for row in self._rows:
            if (
                type(row) is not HistorySample
                or type(row.source_id) is not str
                or type(row.observed_ms) is not int
                or type(row.value) is not bytes
            ):
                raise ValueError('A strict fixture observation is required')
            if any(
                value is not None and type(value) is not str
                for value in (row.quality, row.unit)
            ):
                raise ValueError('Strict fixture metadata is required')
        if hasattr(self, '_seal') and type(self._seal) is not str:
            raise ValueError('A strict fixture fingerprint is required')
        body = {
            'source': list(source_key(self.source)),
            'mapping': self.mapping.as_dict(),
            'coverage': list(window_key(self.coverage)),
            'generation': self.generation,
            'observations': [
                hashlib.sha256(payload).hexdigest() for payload in self.observations
            ],
            'rows': [
                (
                    row.source_id,
                    row.observed_ms,
                    hashlib.sha256(row.value).hexdigest(),
                    row.quality,
                    row.unit,
                )
                for row in self._rows
            ],
            'gaps': self._gap_keys(),
        }
        return hashlib.sha256(
            bounded_json(body, max_bytes=MAX_HISTORY_BYTES)
        ).hexdigest()

    def supports(self, context, window):
        """Admit only this fixture and its explicit qualified mapping interval."""
        key = context_key(context)
        start, end = window_key(window)
        source = source_key(self.source)
        return (
            (key[0], key[2], key[3], key[4]) == source
            and type(key[5]) is str
            and key[5] == self.generation
            and self.mapping.status == 'verified'
            and self.coverage.start_ms <= start < end <= self.coverage.end_ms
            and self.mapping.effective_window.start_ms
            <= start
            < end
            <= self.mapping.effective_window.end_ms
            and self.fingerprint() == self._seal
        )

    def read_window(self, context, window, limit, *, queries):
        """Read one bounded synthetic station/hour slice with honest lookahead."""
        if not self.supports(context, window):
            raise HistoryCapabilityError('Fixture history capability is unavailable')
        if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_SAMPLES_PER_READ:
            raise ValueError('A bounded positive page limit is required')
        for start, end, reason in self._gap_keys():
            if start < window.end_ms and window.start_ms < end:
                raise HistorySourceUnavailableError(reason)
        first = window.start_ms // HOUR_MS * HOUR_MS
        last = (window.end_ms - 1) // HOUR_MS * HOUR_MS
        if (last - first) // HOUR_MS + 1 > MAX_BUCKETS_PER_FIXTURE_READ:
            return HistoryPage(self.generation, (), True)
        rows = []
        for bucket in range(first, last + HOUR_MS, HOUR_MS):
            start, end = (
                max(window.start_ms, bucket),
                min(window.end_ms, bucket + HOUR_MS),
            )
            queries.append((self.mapping.station_uuid, str(bucket), start, end, False))
            for row in self._rows:
                if start <= row.observed_ms < end:
                    rows.append(row)
                    if len(rows) > limit:
                        return HistoryPage(self.generation, tuple(rows[:limit]), True)
        return HistoryPage(self.generation, tuple(rows), False)

    def _validate_result(self, result, context, window):
        if (
            type(result) is not HistoryResult
            or context_key(result.context) != context_key(context)
            or window_key(result.requested_window) != window_key(window)
        ):
            raise HistoryProtocolError('History invocation changed')
        if type(result.gaps) is not tuple or type(result.samples) is not tuple:
            raise HistoryProtocolError('History representation changed')
        gaps = []
        for gap in result.gaps:
            if (
                type(gap) is not HistoryGap
                or type(gap.reason) is not str
                or gap.reason not in _GAP_REASONS
            ):
                raise HistoryProtocolError('Invalid history coverage')
            start, end = window_key(gap.window)
            if not window.start_ms <= start < end <= window.end_ms:
                raise HistoryProtocolError('Invalid history coverage')
            gaps.append((start, end))
        gaps.sort()
        if any(left[1] > right[0] for left, right in pairwise(gaps)):
            raise HistoryProtocolError('History gaps overlap')
        expected = tuple(
            row
            for row in self._rows
            if window.start_ms <= row.observed_ms < window.end_ms
            and not any(start <= row.observed_ms < end for start, end in gaps)
        )
        for row in result.samples:
            if (
                type(row) is not HistorySample
                or type(row.source_id) is not str
                or type(row.observed_ms) is not int
                or type(row.value) is not bytes
            ):
                raise HistoryProtocolError('Invalid history observation')
            if any(
                value is not None and type(value) is not str
                for value in (row.quality, row.unit)
            ):
                raise HistoryProtocolError('Invalid history metadata')
        if result.samples != expected:
            raise HistoryProtocolError('Fixture history is missing or changed')

    def validate_read(self, captured, *, expected_context, expected_window):
        """Revalidate emitted content against independent immutable fixture truth."""
        if not self.supports(expected_context, expected_window):
            raise HistoryProtocolError('Captured history source coordinates changed')
        if (
            type(captured) is not _READ_TYPE
            or type(captured.schema_version) is not str
            or captured.schema_version != 'aimms.s6.fixture-read.v1'
        ):
            raise HistoryProtocolError('Invalid captured history')
        if context_key(captured.context) != context_key(expected_context) or window_key(
            captured.requested_window
        ) != window_key(expected_window):
            raise HistoryProtocolError('Captured history invocation changed')
        if (
            type(captured.fixture_fingerprint) is not str
            or captured.fixture_fingerprint != self.fingerprint()
            or captured.fixture_fingerprint != self._seal
        ):
            raise HistoryProtocolError('Captured history snapshot changed')
        if (
            type(captured.observations) is not tuple
            or type(captured.queries) is not tuple
        ):
            raise HistoryProtocolError('Invalid captured history representation')
        rows = []
        for payload in captured.observations:
            sample = decode_observation(payload).sample
            if (
                source_key(sample.source) != source_key(self.source)
                or sample.source_generation != self.generation
            ):
                raise HistoryProtocolError('Captured observation source changed')
            if type(sample.source_observation_id) is not str:
                raise HistoryProtocolError(
                    'Captured observation identity is unavailable'
                )
            rows.append(
                HistorySample(
                    sample.source_observation_id,
                    datetime_to_unix_ms_exact(sample.observed_at),
                    payload,
                    sample.quality,
                    sample.unit,
                )
            )
        self._validate_result(
            HistoryResult(
                tuple(rows), captured.gaps, captured.context, captured.requested_window
            ),
            expected_context,
            expected_window,
        )

    def read_history(
        self,
        context,
        window,
        *,
        authorize,
        max_samples_per_read=MAX_HISTORY_SAMPLES_PER_READ,
        max_samples_total=MAX_HISTORY_SAMPLES_TOTAL,
        max_reads=MAX_HISTORY_READS,
        split_recoverable_errors=False,
        max_elapsed_seconds=30,
    ):
        """Compose bounded traversal with independent fixture content qualification.

        The authority callback is trusted internal code. Only the request reader
        supplies real current actor authority; an always-true callback is a test.
        """
        initial_context, initial_window = context_key(context), window_key(window)
        if not callable(authorize):
            raise ValueError('A trusted current-authority callback is required')
        if type(max_elapsed_seconds) is not int or not 1 <= max_elapsed_seconds <= 30:
            raise ValueError('A bounded fixture deadline is required')
        if not self.supports(context, window):
            raise HistoryCapabilityError('Fixture history capability is unavailable')
        signature = self.fingerprint()
        deadline = time.monotonic() + max_elapsed_seconds
        queries = []

        def current(current_context, requested):
            return (
                context_key(current_context) == initial_context
                and window_key(requested) == initial_window
                and authorize(current_context, requested) is True
                and self.fingerprint() == signature
            )

        def read(current_context, part, limit):
            if time.monotonic() >= deadline:
                raise HistorySourceUnavailableError('SOURCE_TIMEOUT')
            return self.read_window(current_context, part, limit, queries=queries)

        result = traverse_scoped_history(
            context,
            window,
            read_window=read,
            authorize=current,
            max_samples_per_read=max_samples_per_read,
            max_samples_total=max_samples_total,
            max_reads=max_reads,
            split_recoverable_errors=split_recoverable_errors,
        )
        self._validate_result(result, context, window)
        captured = FixtureHistoryRead(
            context=context,
            requested_window=window,
            observations=tuple(row.value for row in result.samples),
            gaps=result.gaps,
            fixture_fingerprint=signature,
            queries=tuple(queries),
        )
        self.validate_read(captured, expected_context=context, expected_window=window)
        if current(context, window) is not True:
            raise HistoryAuthorizationError('Current history authority is unavailable')
        return captured


@dataclass(frozen=True, kw_only=True)
class FixtureHistoryChange:
    """Non-actionable source differences, with removal deliberately unproven."""

    state: str
    added: tuple[str, ...] = field(default=(), repr=False)
    changed: tuple[str, ...] = field(default=(), repr=False)
    missing: tuple[str, ...] = field(default=(), repr=False)
    tombstones: tuple = field(default=(), init=False)
    removal_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)
    schema_version: str = field(default='aimms.s6.fixture-change.v1', init=False)

    def __post_init__(self):
        """Reject invalid identity groups or any implied deletion authority."""
        if type(self.state) is not str or self.state not in (
            'unchanged',
            'observed',
            'incomplete',
            'incomparable',
        ):
            raise ValueError('An explicit source comparison state is required')
        for group in (self.added, self.changed, self.missing):
            if type(group) is not tuple or any(
                type(value) is not str or not value.strip() for value in group
            ):
                raise ValueError('Exact source observation identities are required')
            if len(group) != len(set(group)):
                raise ValueError('Source comparison identities repeat')
        if self.state != 'observed' and (self.added or self.changed or self.missing):
            raise ValueError('An incomplete comparison cannot classify changes')
        if type(self.tombstones) is not tuple or self.tombstones:
            raise ValueError('Source comparison cannot create tombstones')
        if (
            type(self.removal_complete) is not bool
            or self.removal_complete is not False
        ):
            raise ValueError('Source comparison cannot prove removal')
        if (
            type(self.deletion_applied) is not bool
            or self.deletion_applied is not False
        ):
            raise ValueError('Source comparison cannot apply deletion')
        if (
            type(self.schema_version) is not str
            or self.schema_version != 'aimms.s6.fixture-change.v1'
        ):
            raise ValueError('An explicit source comparison schema is required')

    @property
    def reconciliation_required(self):
        """Differences require a source-qualified decision, not automatic replay."""
        return self.state != 'unchanged'


def compare_fixture_history(
    previous_adapter, previous, current_adapter, current, *, authorize
):
    """Observe scoped snapshot differences without ordering or deletion claims.

    The callback must establish current native actor authority for both retained
    observations. A fixture callback is not an S7 adoption or real-writer census.
    Source time and arbitrary generation labels establish no update ordering.
    """
    if (
        type(previous_adapter) is not FixtureHistoryAdapter
        or type(current_adapter) is not FixtureHistoryAdapter
        or not callable(authorize)
    ):
        raise ValueError('Qualified sources and current authority are required')

    def permitted(captured):
        try:
            allowed = authorize(captured.context, captured.requested_window)
        except MemoryError:
            raise
        except Exception:
            allowed = False
        if allowed is not True:
            raise HistoryAuthorizationError(
                'Current history authority is unavailable'
            ) from None

    def check():
        for adapter, captured in (
            (previous_adapter, previous),
            (current_adapter, current),
        ):
            permitted(captured)
            try:
                adapter.validate_read(
                    captured,
                    expected_context=captured.context,
                    expected_window=captured.requested_window,
                )
            except MemoryError:
                raise
            except Exception:
                permitted(captured)
                raise
            permitted(captured)

    check()
    common = (
        source_key(previous_adapter.source) == source_key(current_adapter.source)
        and previous.context.actor_pk == current.context.actor_pk
        and window_key(previous.requested_window)
        == window_key(current.requested_window)
        and previous_adapter.mapping.as_dict() == current_adapter.mapping.as_dict()
    )
    if not common:
        result = FixtureHistoryChange(state='incomparable')
    elif not previous.complete or not current.complete:
        result = FixtureHistoryChange(state='incomplete')
    else:
        before: dict[str, bytes] = {}
        after: dict[str, bytes] = {}
        for index, captured in ((before, previous), (after, current)):
            for payload in captured.observations:
                identity = decode_observation(payload).sample.source_observation_id
                if not isinstance(identity, str) or type(identity) is not str:
                    raise HistoryProtocolError(
                        'Captured observation identity is unavailable'
                    )
                index[identity] = payload
        added = tuple(sorted(after.keys() - before.keys()))
        changed = tuple(
            sorted(
                key for key in before.keys() & after.keys() if before[key] != after[key]
            )
        )
        missing = tuple(sorted(before.keys() - after.keys()))
        result = FixtureHistoryChange(
            state='observed' if added or changed or missing else 'unchanged',
            added=added,
            changed=changed,
            missing=missing,
        )
    check()
    FixtureHistoryChange.__post_init__(result)
    check()
    return result
