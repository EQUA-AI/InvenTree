"""Bounded private scan decisions, not a complete source reconciliation service.

The caller owns durable checkpoints and source-qualified population/authority.
No source is read, no missing row becomes deletion, and no population-complete
claim is manufactured from a page flag, generation label or callback.
"""

from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

from integration_state.s7_contracts import (
    SourceAspectIdentity,
    exact_epoch,
    literal,
    positive_id,
)

MAX_SCAN_PAGES = 1000
MAX_SCAN_RECORDS = 10000
MAX_PAGE_RECORDS = 1000


def _nonnegative(value):
    if type(value) is not int or value < 0:
        raise ValueError('An exact nonnegative scan coordinate is required')


@dataclass(frozen=True, kw_only=True)
class ScanBinding:
    """Retain exact owner-supplied labels without treating them as proof."""

    environment: str
    epoch: UUID
    entity_kind: str
    aspect: str
    actor_key: str = field(repr=False)
    client_id: int = field(repr=False)
    filter_key: str = field(repr=False)
    generation: str

    def __post_init__(self):
        """Reject aliases before comparing source, actor, population or epoch."""
        for value in (
            self.environment,
            self.actor_key,
            self.filter_key,
            self.generation,
        ):
            literal(value, 128)
        literal(self.entity_kind, 32)
        literal(self.aspect, 32)
        exact_epoch(self.epoch)
        positive_id(self.client_id)


def _binding_key(binding):
    if type(binding) is not ScanBinding:
        raise ValueError('An exact scan binding is required')
    binding.__post_init__()
    return (
        binding.environment,
        binding.epoch,
        binding.entity_kind,
        binding.aspect,
        binding.actor_key,
        binding.client_id,
        binding.filter_key,
        binding.generation,
    )


def _item(item, binding):
    if type(item) is not SourceAspectIdentity:
        raise ValueError('An exact observed source identity is required')
    item.__post_init__()
    if (item.environment, item.epoch, item.entity_kind, item.aspect) != (
        binding.environment,
        binding.epoch,
        binding.entity_kind,
        binding.aspect,
    ):
        raise ValueError('A scan item belongs to another source aspect')


@dataclass(frozen=True, kw_only=True)
class ScanPage:
    """One bounded source-shaped transport observation, not scan authority."""

    binding: ScanBinding
    offset: int
    total: int | None
    items: tuple[SourceAspectIdentity, ...] = field(repr=False)
    next_offset: int | None
    state: str = 'available'

    def __post_init__(self):
        """Keep faults, unknown totals and malformed scalar types distinct."""
        _binding_key(self.binding)
        _nonnegative(self.offset)
        for value in (self.total, self.next_offset):
            if value is not None:
                _nonnegative(value)
        if type(self.state) is not str or self.state not in (
            'available',
            'unavailable',
            'denied',
            'incomplete',
        ):
            raise ValueError('An explicit source page outcome is required')
        if self.state == 'available' and self.total is None:
            raise ValueError('An available page requires its actual transport total')
        if type(self.items) is not tuple or len(self.items) > MAX_PAGE_RECORDS:
            raise ValueError('A bounded exact page tuple is required')
        for item in self.items:
            _item(item, self.binding)


@dataclass(frozen=True, kw_only=True)
class ScanProgress:
    """A bounded in-memory checkpoint; it is not its own durable repository."""

    binding: ScanBinding
    max_pages: int
    max_records: int
    attempts_used: int = 0
    offset: int = 0
    expected_total: int | None = None
    seen: frozenset[SourceAspectIdentity] = field(default_factory=frozenset, repr=False)
    state: str = 'running'
    pending_attempt: UUID | None = field(default=None, repr=False)
    last_attempt: UUID | None = field(default=None, repr=False)
    last_page: ScanPage | None = field(default=None, repr=False)
    population_complete: bool = field(default=False, init=False)
    tombstones: tuple = field(default=(), init=False)
    removal_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)

    def __post_init__(self):
        """Enforce bounded work and truthful no-removal status on every update."""
        _binding_key(self.binding)
        for value, limit in (
            (self.max_pages, MAX_SCAN_PAGES),
            (self.max_records, MAX_SCAN_RECORDS),
        ):
            positive_id(value)
            if value > limit:
                raise ValueError('The bounded local scan budget is exceeded')
        _nonnegative(self.attempts_used)
        _nonnegative(self.offset)
        if self.attempts_used > self.max_pages:
            raise ValueError('A retry cannot reset or exceed its reserved budget')
        if (
            type(self.seen) is not frozenset
            or len(self.seen) != self.offset
            or len(self.seen) > self.max_records
        ):
            raise ValueError(
                'The bounded unique observation checkpoint is inconsistent'
            )
        for item in self.seen:
            _item(item, self.binding)
        if self.expected_total is not None:
            _nonnegative(self.expected_total)
            if self.expected_total < self.offset:
                raise ValueError('Observed records exceed the declared transport total')
        if type(self.state) is not str or self.state not in (
            'running',
            'transport_complete',
            'unavailable',
            'denied',
            'incomplete',
            'budget_exhausted',
        ):
            raise ValueError('An explicit scan progress state is required')
        for attempt in (self.pending_attempt, self.last_attempt):
            if attempt is not None:
                exact_epoch(attempt)
        if self.pending_attempt is not None and (
            self.state != 'running' or self.attempts_used == 0
        ):
            raise ValueError('Only a reserved running scan can hold a pending attempt')
        if self.last_page is not None:
            if type(self.last_page) is not ScanPage:
                raise ValueError('An exact prior page is required')
            self.last_page.__post_init__()
            if self.last_attempt is None or self.last_page.binding != self.binding:
                raise ValueError('The prior attempt belongs to another scan')
        if self.state == 'transport_complete' and (
            self.expected_total is None
            or self.offset != self.expected_total
            or self.attempts_used == 0
            or self.last_attempt is None
            or self.last_page is None
            or self.last_page.state != 'available'
            or self.last_page.next_offset is not None
            or self.last_page.total != self.expected_total
            or self.last_page.offset + len(self.last_page.items) != self.offset
        ):
            raise ValueError(
                'Transport completion requires its entire declared page count'
            )
        if (
            self.population_complete is not False
            or self.removal_complete is not False
            or self.deletion_applied is not False
            or type(self.tombstones) is not tuple
            or self.tombstones
        ):
            raise ValueError(
                'Transport observations do not establish source completeness or deletion'
            )


def _progress(progress):
    if type(progress) is not ScanProgress:
        raise ValueError('An exact bounded scan checkpoint is required')
    progress.__post_init__()


def _permitted(binding, authorize, original):
    if not callable(authorize):
        raise ValueError('A current source-owner authorizer is required')
    if (
        _binding_key(binding) != original
        or authorize(binding) is not True
        or _binding_key(binding) != original
    ):
        raise PermissionError('Current scan authority is unavailable')


def begin_scan(binding, *, max_pages, max_records):
    """Create bounded local progress without claiming a source population."""
    _binding_key(binding)
    return ScanProgress(
        binding=replace(binding), max_pages=max_pages, max_records=max_records
    )


def reserve_page(progress):
    """Reserve the attempt before the caller performs any independent read."""
    _progress(progress)
    if progress.state != 'running' or progress.pending_attempt is not None:
        raise ValueError('This scan cannot reserve another source attempt')
    if progress.attempts_used >= progress.max_pages:
        return replace(progress, state='budget_exhausted')
    return replace(
        progress, attempts_used=progress.attempts_used + 1, pending_attempt=uuid4()
    )


def accept_page(progress, page, *, attempt, authorize):
    """Consume one reserved response with replay and continuity fences.

    Source-owner callbacks are protocol seams, not implemented native grants.
    All error outcomes preserve earlier progress and the consumed attempt.
    """
    _progress(progress)
    original = _binding_key(progress.binding)
    _permitted(progress.binding, authorize, original)
    if type(page) is not ScanPage:
        raise ValueError('An exact source-shaped page is required')
    page.__post_init__()
    exact_epoch(attempt)
    if attempt == progress.last_attempt:
        if page != progress.last_page:
            raise ValueError('A repeated attempt has a conflicting source response')
        result = progress
    else:
        if progress.state != 'running' or attempt != progress.pending_attempt:
            raise ValueError('The response does not own this pending attempt')
        changes = {
            'pending_attempt': None,
            'last_attempt': attempt,
            'last_page': page if page.binding == progress.binding else None,
        }
        if page.binding != progress.binding:
            result = replace(progress, **changes, state='incomplete')
        elif page.state != 'available':
            result = replace(progress, **changes, state=page.state)
        elif page.total is not None and page.total > progress.max_records:
            result = replace(progress, **changes, state='budget_exhausted')
        else:
            end = page.offset + len(page.items)
            valid = (
                page.offset == progress.offset
                and page.total is not None
                and (
                    progress.expected_total is None
                    or page.total == progress.expected_total
                )
                and end <= page.total
                and len(set(page.items)) == len(page.items)
                and not progress.seen.intersection(page.items)
                and (
                    (end == page.total and page.next_offset is None)
                    or (page.offset < end < page.total and page.next_offset == end)
                )
            )
            if not valid:
                result = replace(progress, **changes, state='incomplete')
            else:
                result = replace(
                    progress,
                    **changes,
                    offset=end,
                    expected_total=page.total,
                    seen=progress.seen.union(page.items),
                    state='transport_complete' if end == page.total else 'running',
                )
    _permitted(progress.binding, authorize, original)
    return result


def retry_scan(progress, *, authorize):
    """Resume only a retryable outcome, retaining its exact scope and budgets."""
    _progress(progress)
    original = _binding_key(progress.binding)
    _permitted(progress.binding, authorize, original)
    if (
        progress.state not in ('unavailable', 'denied', 'incomplete')
        or progress.pending_attempt is not None
    ):
        raise ValueError('This scan has no admitted retry transition')
    result = replace(
        progress,
        state='running'
        if progress.attempts_used < progress.max_pages
        else 'budget_exhausted',
    )
    _permitted(progress.binding, authorize, original)
    return result


@dataclass(frozen=True, kw_only=True)
class FixtureReconciliationNotice:
    """Preserve genuine S6 observation differences without inventing context."""

    state: str
    source_schema: str
    added: tuple[str, ...] = field(repr=False)
    changed: tuple[str, ...] = field(repr=False)
    missing: tuple[str, ...] = field(repr=False)
    tombstones: tuple = field(default=(), init=False)
    removal_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)

    def __post_init__(self):
        """Keep exact producer identity groups bounded and non-actionable."""
        if type(self.state) is not str or self.state not in (
            'unchanged',
            'observed',
            'incomplete',
            'incomparable',
        ):
            raise ValueError('An actual fixture comparison state is required')
        if (
            type(self.source_schema) is not str
            or self.source_schema != 'aimms.s6.fixture-change.v1'
        ):
            raise ValueError(
                'The supported actual fixture comparison schema is required'
            )
        seen = set()
        size = 0
        for group in (self.added, self.changed, self.missing):
            if type(group) is not tuple or len(group) + len(seen) > 20000:
                raise ValueError('A bounded exact source identity group is required')
            for value in group:
                if type(value) is not str or not value.strip() or value in seen:
                    raise ValueError(
                        'Compared source identities must remain exact and distinct'
                    )
                size += len(value.encode('utf-8'))
                if size > 8 * 1024 * 1024:
                    raise ValueError('The bounded fixture notice budget is exceeded')
                seen.add(value)
        if self.state != 'observed' and seen:
            raise ValueError(
                'An incomplete comparison cannot classify identity changes'
            )
        if (
            type(self.tombstones) is not tuple
            or self.tombstones
            or self.removal_complete is not False
            or self.deletion_applied is not False
        ):
            raise ValueError('Fixture differences cannot authorize removal or deletion')

    @property
    def reconciliation_required(self):
        """Source qualification is still required before interpreting a difference."""
        return self.state != 'unchanged'


_FIXTURE_NOTICE_TYPE = FixtureReconciliationNotice


def _fixture_value_key(value, schema_name):
    """Compare only exact primitive fields without invoking another validator."""
    state, schema = value.state, getattr(value, schema_name)
    groups = (value.added, value.changed, value.missing)
    if (
        type(state) is not str
        or type(schema) is not str
        or type(value.tombstones) is not tuple
        or value.tombstones
        or value.removal_complete is not False
        or value.deletion_applied is not False
    ):
        raise ValueError('The fixture comparison binding has invalid primitive fields')
    for group in groups:
        if (
            type(group) is not tuple
            or len(group) > 20000
            or any(type(identity) is not str for identity in group)
        ):
            raise ValueError(
                'The fixture comparison binding has invalid identity groups'
            )
    return (state, schema, *groups)


def _fixture_notice_key(notice, *, validate=True):
    """Validate the actual returned notice before reading its bound values."""
    if type(notice) is not _FIXTURE_NOTICE_TYPE:
        raise ValueError('An exact fixture reconciliation notice is required')
    if validate:
        notice.__post_init__()
    return _fixture_value_key(notice, 'source_schema')


def notice_from_fixture_change(change, *, authorize):
    """Consume the actual non-actionable S6 carrier with current-owner checks.

    No environment, epoch, revision, ordering or source-removal proof exists in
    that carrier, so none is fabricated. Native source authority remains the
    caller's independently qualified responsibility, including on errors.
    """
    from machine_health.services.integration_s6_adapter import FixtureHistoryChange

    if not callable(authorize):
        raise ValueError('A current source-owner authorizer is required')

    def check():
        if authorize() is not True:
            raise PermissionError('Current fixture-change authority is unavailable')

    def source_key(*, validate=True):
        if type(change) is not FixtureHistoryChange:
            raise ValueError('The actual S6 fixture comparison carrier is required')
        if validate:
            change.__post_init__()
        return _fixture_value_key(change, 'schema_version')

    check()
    try:
        original = source_key()
        result = FixtureReconciliationNotice(
            state=change.state,
            source_schema=change.schema_version,
            added=change.added,
            changed=change.changed,
            missing=change.missing,
        )
        if _fixture_notice_key(result) != original:
            raise ValueError('The returned fixture notice changed its original intake')
        if source_key() != original:
            raise ValueError(
                'The fixture comparison changed during notice construction'
            )
    except MemoryError:
        raise
    except Exception:
        check()
        raise
    check()
    try:
        # No producer validation or caller callback follows this final check.
        if (
            source_key(validate=False) != original
            or _fixture_notice_key(result, validate=False) != original
        ):
            raise ValueError('The fixture notice binding changed before return')
    except MemoryError:
        raise
    except Exception:
        check()
        raise
    return result


MAX_COVERAGE_GAPS = 1000


@dataclass(frozen=True, kw_only=True)
class CoverageGap:
    """Private caller-reported limitation, never independently qualified proof."""

    target_kind: str
    target_key: str = field(repr=False)
    capability: str
    reason: str
    evidence_key: str = field(repr=False)

    def __post_init__(self):
        """Keep source/writer cells and opaque references literal and bounded."""
        _coverage_gap_key(self)


_COVERAGE_GAP_TYPE = CoverageGap


def _coverage_gap_key(gap):
    """Refuse foreign members and positive capability claims before comparison."""
    if type(gap) is not _COVERAGE_GAP_TYPE:
        raise ValueError('An exact private coverage limitation is required')
    for value, allowed in (
        (gap.target_kind, ('source', 'writer')),
        (
            gap.capability,
            (
                'capture',
                'reconciliation',
                'revision',
                'incarnation',
                'ordering',
                'history',
                'population',
                'retained_copies',
                'removal',
            ),
        ),
        (gap.reason, ('unsupported', 'unknown', 'incomplete', 'unavailable', 'denied')),
    ):
        if type(value) is not str or value not in allowed:
            raise ValueError('An explicit unsupported coverage disposition is required')
    literal(gap.target_key, 256)
    literal(gap.evidence_key, 256)
    return (
        gap.target_kind,
        gap.target_key,
        gap.capability,
        gap.reason,
        gap.evidence_key,
    )


def _coverage_gaps_key(gaps):
    """Preserve distinct cells without silent conflict replacement or truncation."""
    if type(gaps) is not tuple or len(gaps) > MAX_COVERAGE_GAPS:
        raise ValueError('A bounded exact coverage limitation tuple is required')
    keys = tuple(_coverage_gap_key(gap) for gap in gaps)
    if len({key[:3] for key in keys}) != len(keys):
        raise ValueError('A coverage cell cannot be duplicated or silently replaced')
    return keys


@dataclass(frozen=True, kw_only=True)
class ScanCoverageRegister:
    """In-memory limitations for one private scan, not a durable source ledger."""

    binding: ScanBinding = field(repr=False)
    transport_state: str
    gaps: tuple[CoverageGap, ...] = field(repr=False)
    coverage_complete: bool = field(default=False, init=False)
    population_complete: bool = field(default=False, init=False)
    native_authorized: bool = field(default=False, init=False)
    tombstones: tuple = field(default=(), init=False)
    removal_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)

    def __post_init__(self):
        """An empty limitation set cannot certify unobserved source coverage."""
        _coverage_register_key(self)

    @property
    def status(self):
        """Transport and caller metadata never qualify physical source coverage."""
        return 'unqualified'


_SCAN_COVERAGE_REGISTER_TYPE = ScanCoverageRegister


def _coverage_binding_key(binding):
    """Read only exact primitive scope fields, without invoking a validator."""
    if type(binding) is not ScanBinding:
        raise ValueError('An exact scan binding is required for coverage metadata')
    for value in (
        binding.environment,
        binding.actor_key,
        binding.filter_key,
        binding.generation,
    ):
        literal(value, 128)
    literal(binding.entity_kind, 32)
    literal(binding.aspect, 32)
    exact_epoch(binding.epoch)
    positive_id(binding.client_id)
    return (
        binding.environment,
        binding.epoch,
        binding.entity_kind,
        binding.aspect,
        binding.actor_key,
        binding.client_id,
        binding.filter_key,
        binding.generation,
    )


def _coverage_transport_state(state):
    """Retain an actual transport outcome without promoting it to source coverage."""
    if type(state) is not str or state not in (
        'running',
        'transport_complete',
        'unavailable',
        'denied',
        'incomplete',
        'budget_exhausted',
    ):
        raise ValueError('The actual scan transport state is required')
    return state


def _coverage_input_key(progress, gaps):
    """Bind only the projected input fields and unchanged no-removal semantics."""
    if type(progress) is not ScanProgress:
        raise ValueError('An exact scan progress is required for coverage metadata')
    if (
        progress.population_complete is not False
        or progress.removal_complete is not False
        or progress.deletion_applied is not False
        or type(progress.tombstones) is not tuple
        or progress.tombstones
    ):
        raise ValueError('Scan metadata cannot establish population or deletion')
    return (
        _coverage_binding_key(progress.binding),
        _coverage_transport_state(progress.state),
        _coverage_gaps_key(gaps),
    )


def _coverage_register_key(register):
    """Validate exact private metadata and every no-authority/no-effect flag."""
    if type(register) is not _SCAN_COVERAGE_REGISTER_TYPE:
        raise ValueError('An exact private scan coverage register is required')
    binding = _coverage_binding_key(register.binding)
    state = _coverage_transport_state(register.transport_state)
    for value in (
        register.coverage_complete,
        register.population_complete,
        register.native_authorized,
        register.removal_complete,
        register.deletion_applied,
    ):
        if value is not False:
            raise ValueError(
                'Limitation metadata cannot qualify source coverage or effects'
            )
    if type(register.tombstones) is not tuple or register.tombstones:
        raise ValueError('Limitation metadata cannot create tombstones')
    return binding, state, _coverage_gaps_key(register.gaps)


def coverage_register(progress, gaps=(), *, authorize):
    """Detach bounded limitations after current-owner checks, without source IO.

    Entry labels and references are private caller claims, not source identities,
    supported capabilities, historical proof, native authority or files to read.
    This does not change progress, persist anything or register an integration.
    """
    _progress(progress)
    original = _binding_key(progress.binding)
    _permitted(progress.binding, authorize, original)
    try:
        expected = _coverage_input_key(progress, gaps)
        result = ScanCoverageRegister(
            binding=replace(progress.binding),
            transport_state=progress.state,
            gaps=tuple(replace(gap) for gap in gaps),
        )
        if _coverage_register_key(result) != expected:
            raise ValueError(
                'The returned coverage register changed its original intake'
            )
    except MemoryError:
        raise
    except Exception:
        _permitted(progress.binding, authorize, original)
        raise
    _permitted(progress.binding, authorize, original)
    try:
        if (
            _coverage_input_key(progress, gaps) != expected
            or _coverage_register_key(result) != expected
        ):
            raise ValueError('The private coverage binding changed before return')
    except MemoryError:
        raise
    except Exception:
        _permitted(progress.binding, authorize, original)
        raise
    return result
