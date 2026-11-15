"""Durable private S7 transport scans; no source reads or removal authority."""

from dataclasses import dataclass, field, replace
from uuid import UUID

from integration_state import s7_reconciliation as peer
from integration_state.models import (
    ReconciliationAttempt,
    ReconciliationIdentity,
    ReconciliationScan,
)
from integration_state.s7_contracts import SourceAspectIdentity
from integration_state.shared_contracts import exact_uuid, integer, literal
from integration_state.shared_events import (
    SharedConflictError,
    SharedStateError,
    fingerprint,
    require_namespace,
    shared_transaction,
)

SCAN_SCHEMA = 'aimms.shared-scan.v1'


def _binding_payload(binding):
    if type(binding) is not peer.ScanBinding:
        raise ValueError('An exact S7 scan binding is required')
    binding.__post_init__()
    for value in (
        binding.environment,
        binding.actor_key,
        binding.filter_key,
        binding.generation,
    ):
        literal(value, 128)
    literal(binding.entity_kind, 32)
    literal(binding.aspect, 32)
    integer(binding.client_id)
    exact_uuid(binding.epoch)
    return {
        'schema': SCAN_SCHEMA,
        'environment': binding.environment,
        'epoch': str(binding.epoch),
        'entity_kind': binding.entity_kind,
        'aspect': binding.aspect,
        'actor_key': binding.actor_key,
        'client_id': binding.client_id,
        'filter_key': binding.filter_key,
        'generation': binding.generation,
    }


def _binding_from(payload):
    keys = {
        'schema',
        'environment',
        'epoch',
        'entity_kind',
        'aspect',
        'actor_key',
        'client_id',
        'filter_key',
        'generation',
    }
    if (
        type(payload) is not dict
        or set(payload) != keys
        or type(payload['schema']) is not str
        or payload['schema'] != SCAN_SCHEMA
    ):
        raise SharedStateError('Stored scan binding is unqualified')
    values = {key: value for key, value in payload.items() if key != 'schema'}
    values['epoch'] = UUID(values['epoch'])
    result = peer.ScanBinding(**values)
    if _binding_payload(result) != payload:
        raise SharedStateError('Stored scan binding is noncanonical')
    return result


def _item_payload(item):
    if type(item) is not SourceAspectIdentity:
        raise ValueError('An exact S7 observed identity is required')
    item.__post_init__()
    literal(item.environment, 128)
    literal(item.entity_kind, 32)
    literal(item.aspect, 32)
    exact_uuid(item.epoch)
    if type(item.source_id) is str:
        literal(item.source_id, 256)
    else:
        integer(item.source_id)
    return {
        'environment': item.environment,
        'epoch': str(item.epoch),
        'entity_kind': item.entity_kind,
        'aspect': item.aspect,
        'key_type': 'integer' if type(item.source_id) is int else 'string',
        'source_id': item.source_id,
    }


def _item_from(payload):
    if type(payload) is not dict or set(payload) != {
        'environment',
        'epoch',
        'entity_kind',
        'aspect',
        'key_type',
        'source_id',
    }:
        raise SharedStateError('Stored scan identity shape is unqualified')
    values = {key: value for key, value in payload.items() if key != 'key_type'}
    values['epoch'] = UUID(values['epoch'])
    result = SourceAspectIdentity(**values)
    if _item_payload(result) != payload:
        raise SharedStateError('Stored scan identity is unqualified')
    return result


def _page_payload(page):
    if type(page) is not peer.ScanPage:
        raise ValueError('An exact S7 source page is required')
    page.__post_init__()
    for value in (page.offset, page.total, page.next_offset):
        if value is not None:
            integer(value, zero=True)
    return {
        'binding': _binding_payload(page.binding),
        'offset': page.offset,
        'total': page.total,
        'items': [_item_payload(item) for item in page.items],
        'next_offset': page.next_offset,
        'state': page.state,
    }


def _page_from(payload):
    if (
        type(payload) is not dict
        or set(payload)
        != {'binding', 'offset', 'total', 'items', 'next_offset', 'state'}
        or type(payload['items']) is not list
    ):
        raise SharedStateError('Stored scan page shape is unqualified')
    result = peer.ScanPage(
        binding=_binding_from(payload['binding']),
        offset=payload['offset'],
        total=payload['total'],
        items=tuple(_item_from(item) for item in payload['items']),
        next_offset=payload['next_offset'],
        state=payload['state'],
    )
    if _page_payload(result) != payload:
        raise SharedStateError('Stored scan page is unqualified')
    return result


def _progress_payload(progress):
    if type(progress) is not peer.ScanProgress:
        raise ValueError('An exact S7 scan checkpoint is required')
    progress.__post_init__()
    return {
        'attempts_used': progress.attempts_used,
        'offset': progress.offset,
        'expected_total': progress.expected_total,
        'state': progress.state,
        'pending_attempt': None
        if progress.pending_attempt is None
        else str(progress.pending_attempt),
        'last_attempt': None
        if progress.last_attempt is None
        else str(progress.last_attempt),
        'last_page': None
        if progress.last_page is None
        else _page_payload(progress.last_page),
        'seen': sorted(
            (_item_payload(item) for item in progress.seen), key=fingerprint
        ),
    }


@dataclass(frozen=True, kw_only=True)
class DurableScan:
    """A retained versioned private S7 checkpoint, never a coverage certificate."""

    scan_id: int
    namespace_id: int
    version: int
    progress: peer.ScanProgress = field(repr=False)

    def __post_init__(self):
        """Reject forged scalar/header/carrier shapes before ownership use."""
        for value in (self.scan_id, self.namespace_id, self.version):
            integer(value)
        _progress_payload(self.progress)


def _baseline(namespace_id, request_key, binding, max_pages, max_records):
    return fingerprint({
        'namespace_id': namespace_id,
        'request_key': request_key,
        'binding': _binding_payload(binding),
        'max_pages': max_pages,
        'max_records': max_records,
    })


def _restore(row):
    binding = _binding_from(row.binding)
    if row.epoch != binding.epoch or row.baseline_digest != _baseline(
        row.namespace_id, row.request_key, binding, row.max_pages, row.max_records
    ):
        raise SharedStateError('Stored scan request binding is unqualified')
    keys = {
        'attempts_used',
        'offset',
        'expected_total',
        'state',
        'pending_attempt',
        'last_attempt',
        'last_page',
    }
    if type(row.checkpoint) is not dict or set(row.checkpoint) != keys:
        raise SharedStateError('Stored scan checkpoint shape is unqualified')
    identities = list(
        ReconciliationIdentity.objects.filter(scan=row).order_by('pk')[
            : row.max_records + 1
        ]
    )
    if len(identities) > row.max_records:
        raise SharedStateError('Stored scan identity budget is exceeded')
    seen = []
    for identity in identities:
        if fingerprint(identity.identity) != identity.identity_digest:
            raise SharedStateError('Stored scan identity digest is unqualified')
        seen.append(_item_from(identity.identity))
    if len(set(seen)) != len(seen):
        raise SharedStateError('Stored scan identities repeat')
    values = dict(row.checkpoint)
    for key in ('pending_attempt', 'last_attempt'):
        values[key] = None if values[key] is None else UUID(values[key])
    values['last_page'] = (
        None if values['last_page'] is None else _page_from(values['last_page'])
    )
    progress = peer.ScanProgress(
        binding=binding,
        max_pages=row.max_pages,
        max_records=row.max_records,
        seen=frozenset(seen),
        **values,
    )
    if fingerprint(_progress_payload(progress)) != row.checkpoint_digest:
        raise SharedStateError('Stored scan progress is unqualified')
    attempts = list(
        ReconciliationAttempt.objects.filter(scan=row).order_by('ordinal')[
            : row.max_pages + 1
        ]
    )
    if len(attempts) != progress.attempts_used or [
        attempt.ordinal for attempt in attempts
    ] != list(range(1, progress.attempts_used + 1)):
        raise SharedStateError('Stored scan reservation history is incomplete')
    versions = [attempt.reserved_version for attempt in attempts]
    pending = [attempt.attempt_id for attempt in attempts if attempt.state == 'pending']
    if (
        versions != sorted(set(versions))
        or any(version > row.version for version in versions)
        or pending
        != ([] if progress.pending_attempt is None else [progress.pending_attempt])
    ):
        raise SharedStateError('Stored scan reservation fence is unqualified')
    if pending and attempts[-1].attempt_id != progress.pending_attempt:
        raise SharedStateError('An older attempt cannot own current progress')
    terminal = []
    for attempt in attempts:
        if attempt.state in ('pending', 'abandoned'):
            if attempt.response_digest is not None:
                raise SharedStateError('An unobserved attempt cannot carry a response')
        elif attempt.state == 'accepted':
            digest = attempt.response_digest
            if (
                type(digest) is not str
                or len(digest) != 64
                or any(value not in '0123456789abcdef' for value in digest)
            ):
                raise SharedStateError(
                    'An accepted attempt requires its actual response digest'
                )
        else:
            raise SharedStateError('Stored scan attempt state is unqualified')
        if attempt.state != 'pending':
            terminal.append(attempt)
    if (None if not terminal else terminal[-1].attempt_id) != progress.last_attempt:
        raise SharedStateError('Stored scan response sequence is unqualified')
    if progress.last_page is not None and (
        not terminal
        or terminal[-1].state != 'accepted'
        or terminal[-1].response_digest
        != fingerprint(_page_payload(progress.last_page))
    ):
        raise SharedStateError('Stored scan response binding is unqualified')
    expected = (
        row.pk,
        row.namespace_id,
        row.version,
        row.baseline_digest,
        row.checkpoint_digest,
    )
    result = DurableScan(
        scan_id=row.pk,
        namespace_id=row.namespace_id,
        version=row.version,
        progress=progress,
    )
    if type(result) is not DurableScan:
        raise SharedStateError('Durable scan constructor returned an unqualified shape')
    for value in (result.scan_id, result.namespace_id, result.version):
        integer(value)
    if (
        result.scan_id,
        result.namespace_id,
        result.version,
        _baseline(
            result.namespace_id,
            row.request_key,
            result.progress.binding,
            result.progress.max_pages,
            result.progress.max_records,
        ),
        fingerprint(_progress_payload(result.progress)),
    ) != expected:
        raise SharedStateError('Durable scan constructor changed validated metadata')
    return result


def _permitted(binding, authorize, expected):
    if not callable(authorize):
        raise ValueError('A current source-owner authorizer is required')
    if (
        _binding_payload(binding) != expected
        or authorize(binding) is not True
        or _binding_payload(binding) != expected
    ):
        raise PermissionError('Current durable scan authority is unavailable')


def _observe(binding, authorize, action):
    """Run current-owner callbacks outside every owned transaction, including errors."""
    expected = _binding_payload(binding)
    _permitted(binding, authorize, expected)
    try:
        result = action()
    except MemoryError:
        raise
    except Exception:
        _permitted(binding, authorize, expected)
        raise
    _permitted(binding, authorize, expected)
    if type(result) is not DurableScan:
        raise SharedStateError('Durable scan result shape is unqualified')
    with shared_transaction(durable=True):
        require_namespace(
            result.namespace_id, epoch=binding.epoch, environment=binding.environment
        )
    return result


def _open(scan_id, binding):
    namespace_id = (
        ReconciliationScan.objects
        .filter(pk=scan_id)
        .values_list('namespace_id', flat=True)
        .first()
    )
    if namespace_id is None:
        raise SharedStateError('Durable scan is unavailable')
    namespace = require_namespace(
        namespace_id, epoch=binding.epoch, environment=binding.environment
    )
    row = (
        ReconciliationScan.objects
        .select_for_update()
        .filter(pk=scan_id, namespace=namespace)
        .first()
    )
    if row is None or row.binding != _binding_payload(binding):
        raise SharedConflictError('Scan actor/population binding changed')
    return row


def _read(scan_id, binding):
    with shared_transaction(durable=True):
        return _restore(_open(scan_id, binding))


def _save(snapshot, desired, *, page=None, abandoned=False):
    desired.__post_init__()
    with shared_transaction(durable=True):
        row = _open(snapshot.scan_id, snapshot.progress.binding)
        actual = _restore(row)
        if actual != snapshot:
            raise SharedConflictError('Durable scan version or reservation changed')
        next_version = row.version + 1
        integer(next_version)
        old = actual.progress
        if (
            desired.pending_attempt is not None
            and desired.pending_attempt != old.pending_attempt
        ):
            ReconciliationAttempt.objects.create(
                scan=row,
                attempt_id=desired.pending_attempt,
                ordinal=desired.attempts_used,
                reserved_version=next_version,
                state='pending',
            )
        if old.pending_attempt is not None and desired.pending_attempt is None:
            attempt = (
                ReconciliationAttempt.objects
                .select_for_update()
                .filter(scan=row, attempt_id=old.pending_attempt, state='pending')
                .first()
            )
            if attempt is None:
                raise SharedConflictError('Durable page reservation is unqualified')
            attempt.state = 'abandoned' if abandoned else 'accepted'
            attempt.response_digest = (
                None if page is None else fingerprint(_page_payload(page))
            )
            attempt.save(update_fields=['state', 'response_digest'])
        for item in desired.seen - old.seen:
            payload = _item_payload(item)
            ReconciliationIdentity.objects.create(
                scan=row, identity=payload, identity_digest=fingerprint(payload)
            )
        full = _progress_payload(desired)
        row.checkpoint = {key: value for key, value in full.items() if key != 'seen'}
        row.checkpoint_digest = fingerprint(full)
        row.version = next_version
        row.save(update_fields=['checkpoint', 'checkpoint_digest', 'version'])
        return _restore(row)


def begin_scan(
    namespace_id, request_key, binding, *, max_pages, max_records, authorize
):
    """Idempotently persist one exact actor/population request and its fixed budgets."""
    integer(namespace_id)
    literal(request_key, 128)
    detached = _binding_from(_binding_payload(binding))
    progress = peer.begin_scan(detached, max_pages=max_pages, max_records=max_records)
    baseline = _baseline(namespace_id, request_key, detached, max_pages, max_records)

    def action():
        with shared_transaction(durable=True):
            namespace = require_namespace(
                namespace_id, epoch=detached.epoch, environment=detached.environment
            )
            prior = (
                ReconciliationScan.objects
                .select_for_update()
                .filter(namespace=namespace, request_key=request_key)
                .first()
            )
            if prior is not None:
                if prior.baseline_digest != baseline:
                    raise SharedConflictError(
                        'Scan request has a conflicting owner, population or budget'
                    )
                return _restore(prior)
            full = _progress_payload(progress)
            row = ReconciliationScan.objects.create(
                namespace=namespace,
                epoch=namespace.epoch,
                request_key=request_key,
                binding=_binding_payload(detached),
                baseline_digest=baseline,
                max_pages=max_pages,
                max_records=max_records,
                version=1,
                checkpoint={key: value for key, value in full.items() if key != 'seen'},
                checkpoint_digest=fingerprint(full),
            )
            return _restore(row)

    return _observe(detached, authorize, action)


def read_scan(scan_id, *, binding, authorize):
    """Restore actual S7 progress without adopting another actor/population."""
    integer(scan_id)
    detached = _binding_from(_binding_payload(binding))
    return _observe(detached, authorize, lambda: _read(scan_id, detached))


def reserve_scan_page(snapshot, *, authorize):
    """Commit a consumed attempt before the caller performs source IO."""
    if type(snapshot) is not DurableScan:
        raise ValueError('An exact durable scan snapshot is required')
    snapshot.__post_init__()
    desired = peer.reserve_page(snapshot.progress)
    return _observe(
        snapshot.progress.binding, authorize, lambda: _save(snapshot, desired)
    )


def accept_scan_page(snapshot, page, *, attempt, authorize):
    """Apply actual S7 page semantics with current-owner checks and durable CAS."""
    if type(snapshot) is not DurableScan:
        raise ValueError('An exact durable scan reservation is required')
    snapshot.__post_init__()
    exact_uuid(attempt)
    detached = _page_from(_page_payload(page))

    def action():
        actual = _read(snapshot.scan_id, snapshot.progress.binding)
        if actual.version != snapshot.version:
            if (
                actual.progress.last_attempt == attempt
                and actual.progress.last_page == detached
            ):
                return actual
            raise SharedConflictError('Durable scan version or attempt changed')
        if actual != snapshot:
            raise SharedConflictError('Durable scan payload changed')
        desired = peer.accept_page(
            actual.progress, detached, attempt=attempt, authorize=lambda binding: True
        )
        if desired == actual.progress:
            return actual
        return _save(actual, desired, page=detached)

    return _observe(snapshot.progress.binding, authorize, action)


def abandon_scan_page(snapshot, *, attempt, authorize):
    """Consume a lost reservation without pretending its source response arrived."""
    if type(snapshot) is not DurableScan:
        raise ValueError('An exact durable scan reservation is required')
    snapshot.__post_init__()
    exact_uuid(attempt)
    if (
        snapshot.progress.pending_attempt != attempt
        or snapshot.progress.state != 'running'
    ):
        raise SharedConflictError('The lost attempt does not own this reservation')
    desired = replace(
        snapshot.progress,
        pending_attempt=None,
        last_attempt=attempt,
        last_page=None,
        state='unavailable',
    )
    return _observe(
        snapshot.progress.binding,
        authorize,
        lambda: _save(snapshot, desired, abandoned=True),
    )


def retry_scan(snapshot, *, authorize):
    """Reuse the exact retained S7 budgets and binding, never a new progress zero."""
    if type(snapshot) is not DurableScan:
        raise ValueError('An exact durable scan checkpoint is required')
    snapshot.__post_init__()
    desired = peer.retry_scan(snapshot.progress, authorize=lambda binding: True)
    return _observe(
        snapshot.progress.binding, authorize, lambda: _save(snapshot, desired)
    )
