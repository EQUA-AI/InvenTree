"""Retained private S7 planning metadata, never native approval or erasure."""

from dataclasses import dataclass, field
from uuid import UUID

from django.db.models import Q

from assets.integration_contracts import ClientSourceIdentity
from integration_state import s7_deletion_requests as peer
from integration_state.models import (
    PrivateCopyAttempt,
    PrivateCopyImpact,
    PrivateDeletionRequest,
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

REQUEST_SCHEMA = 'aimms.private-request.v1'
COPY_KINDS = (
    'original',
    'derivative',
    'index',
    'cache',
    'report',
    'audit',
    'ledger',
    'backup',
    'unknown',
)


def _kind(value):
    literal(value, 16)
    if value not in COPY_KINDS:
        raise ValueError('An explicit private copy kind is required')


def _impact_payload(impact):
    if type(impact) is not peer.CopyImpact:
        raise ValueError('An exact private S7 copy impact is required')
    impact.__post_init__()
    literal(impact.copy_key, 256)
    if impact.copy_revision is not None:
        literal(impact.copy_revision, 256) if type(
            impact.copy_revision
        ) is str else integer(impact.copy_revision)
    return {
        'copy_key': impact.copy_key,
        'source': _source_payload(impact.source),
        'copy_revision': impact.copy_revision,
        'owners': None
        if impact.owners is None
        else [_client_payload(value) for value in impact.owners],
        'attribution': impact.attribution,
        'retention': impact.retention,
    }


def _impact_from(payload):
    if (
        type(payload) is not dict
        or set(payload)
        != {'copy_key', 'source', 'copy_revision', 'owners', 'attribution', 'retention'}
        or (payload['owners'] is not None and type(payload['owners']) is not list)
    ):
        raise SharedStateError('Stored private copy shape is unqualified')
    impact = peer.CopyImpact(
        copy_key=payload['copy_key'],
        source=_source_from(payload['source']),
        copy_revision=payload['copy_revision'],
        owners=None
        if payload['owners'] is None
        else tuple(_client_from(value) for value in payload['owners']),
        attribution=payload['attribution'],
        retention=payload['retention'],
    )
    if _impact_payload(impact) != payload:
        raise SharedStateError('Stored private copy claim is noncanonical')
    return impact


def _copy_digest(impact, copy_kind):
    _kind(copy_kind)
    return fingerprint({
        'schema': 'aimms.private-copy.v1',
        'kind': copy_kind,
        'impact': _impact_payload(impact),
    })


def _progress_payload(progress):
    if type(progress) is not peer.CopyProgress:
        raise ValueError('An exact synthetic S7 copy checkpoint is required')
    progress.__post_init__()
    integer(progress.sequence, zero=True)
    if progress.copy_revision is not None:
        literal(progress.copy_revision, 256) if type(
            progress.copy_revision
        ) is str else integer(progress.copy_revision)
    return {
        'request_fingerprint': progress.request_fingerprint,
        'copy_fingerprint': progress.copy_fingerprint,
        'copy_key': progress.copy_key,
        'copy_revision': progress.copy_revision,
        'attempt_id': str(progress.attempt_id),
        'attempt_history': [str(value) for value in progress.attempt_history],
        'max_attempts': progress.max_attempts,
        'sequence': progress.sequence,
        'status': progress.status,
        'evidence_digest': progress.evidence_digest,
    }


def _progress_from(payload):
    if (
        type(payload) is not dict
        or set(payload)
        != {
            'request_fingerprint',
            'copy_fingerprint',
            'copy_key',
            'copy_revision',
            'attempt_id',
            'attempt_history',
            'max_attempts',
            'sequence',
            'status',
            'evidence_digest',
        }
        or type(payload['attempt_history']) is not list
    ):
        raise SharedStateError('Stored private progress shape is unqualified')
    values = dict(payload)
    values['attempt_id'] = UUID(values['attempt_id'])
    values['attempt_history'] = tuple(
        UUID(value) for value in values['attempt_history']
    )
    result = peer.CopyProgress(**values)
    if _progress_payload(result) != payload:
        raise SharedStateError('Stored private progress is noncanonical')
    return result


def _source_payload(value):
    if type(value) is not SourceAspectIdentity:
        raise ValueError('An exact S7 source aspect is required')
    value.__post_init__()
    literal(value.environment, 128)
    literal(value.entity_kind, 32)
    literal(value.aspect, 32)
    exact_uuid(value.epoch)
    if type(value.source_id) is str:
        literal(value.source_id, 256)
    else:
        integer(value.source_id)
    return {
        'environment': value.environment,
        'epoch': str(value.epoch),
        'entity_kind': value.entity_kind,
        'aspect': value.aspect,
        'key_type': 'integer' if type(value.source_id) is int else 'string',
        'source_id': value.source_id,
    }


def _source_from(payload):
    if type(payload) is not dict or set(payload) != {
        'environment',
        'epoch',
        'entity_kind',
        'aspect',
        'key_type',
        'source_id',
    }:
        raise SharedStateError('Stored private source shape is unqualified')
    values = {key: value for key, value in payload.items() if key != 'key_type'}
    values['epoch'] = UUID(values['epoch'])
    result = SourceAspectIdentity(**values)
    if _source_payload(result) != payload:
        raise SharedStateError('Stored private source identity is noncanonical')
    return result


def _client_payload(value):
    if type(value) is not ClientSourceIdentity:
        raise ValueError('An exact native client identity is required')
    value.__post_init__()
    literal(value.environment, 128)
    integer(value.pk)
    return {'environment': value.environment, 'pk': value.pk}


def _client_from(payload):
    if type(payload) is not dict or set(payload) != {'environment', 'pk'}:
        raise SharedStateError('Stored private client shape is unqualified')
    result = ClientSourceIdentity(**payload)
    if _client_payload(result) != payload:
        raise SharedStateError('Stored private client is unqualified')
    return result


def _intent_payload(intent):
    if type(intent) is not peer.DeletionIntent:
        raise ValueError('An exact private S7 intent is required')
    intent.__post_init__()
    exact_uuid(intent.request_id)
    integer(intent.request_revision)
    literal(intent.request_key, 128)
    literal(intent.actor_key, 128)
    return {
        'schema': REQUEST_SCHEMA,
        'request_id': str(intent.request_id),
        'request_key': intent.request_key,
        'request_revision': intent.request_revision,
        'actor_key': intent.actor_key,
        'client': _client_payload(intent.client),
        'scope': [_source_payload(value) for value in intent.scope],
        'default_retention': intent.default_retention,
    }


def _intent_from(payload):
    if (
        type(payload) is not dict
        or set(payload)
        != {
            'schema',
            'request_id',
            'request_key',
            'request_revision',
            'actor_key',
            'client',
            'scope',
            'default_retention',
        }
        or type(payload['scope']) is not list
    ):
        raise SharedStateError('Stored private intent shape is unqualified')
    result = peer.DeletionIntent(
        request_id=UUID(payload['request_id']),
        request_key=payload['request_key'],
        request_revision=payload['request_revision'],
        actor_key=payload['actor_key'],
        client=_client_from(payload['client']),
        scope=tuple(_source_from(value) for value in payload['scope']),
    )
    if _intent_payload(result) != payload:
        raise SharedStateError('Stored private intent is noncanonical')
    return result


def _epoch(intent):
    _intent_payload(intent)
    epoch = intent.scope[0].epoch
    if any(value.epoch != epoch for value in intent.scope):
        raise SharedStateError(
            'One qualified namespace epoch is required per private intent'
        )
    return epoch


@dataclass(frozen=True, kw_only=True)
class PrivateResult:
    """Private bookkeeping never grants authority or certifies complete coverage."""

    native_authorized: bool = field(default=False, init=False)
    coverage_complete: bool = field(default=False, init=False)
    deletion_applied: bool = field(default=False, init=False)
    erasure_complete: bool = field(default=False, init=False)

    def __post_init__(self):
        """Refuse a forged constructor/record claiming real action or approval."""
        if any(
            value is not False
            for value in (
                self.native_authorized,
                self.coverage_complete,
                self.deletion_applied,
                self.erasure_complete,
            )
        ):
            raise SharedStateError(
                'Private planning metadata has no native action authority'
            )


@dataclass(frozen=True, kw_only=True)
class DurablePrivateIntent(PrivateResult):
    """One immutable retained actor/client-bound request value, not a command."""

    request_pk: int
    namespace_id: int
    intent: peer.DeletionIntent = field(repr=False)
    intent_digest: str = field(repr=False)

    def __post_init__(self):
        """Validate exact private header and original peer intent fingerprint."""
        super().__post_init__()
        integer(self.request_pk)
        integer(self.namespace_id)
        _intent_payload(self.intent)
        if type(
            self.intent_digest
        ) is not str or self.intent_digest != peer.intent_fingerprint(self.intent):
            raise SharedStateError('Private intent fingerprint is unqualified')


@dataclass(frozen=True, kw_only=True)
class DurableCopyImpact(PrivateResult):
    """Immutable private copy claim and kind, not complete impact discovery."""

    impact_pk: int
    request_pk: int
    namespace_id: int
    impact: peer.CopyImpact = field(repr=False)
    copy_kind: str
    copy_digest: str = field(repr=False)

    def __post_init__(self):
        """Validate the exact original source/ownership/retention claim."""
        super().__post_init__()
        for value in (self.impact_pk, self.request_pk, self.namespace_id):
            integer(value)
        if self.copy_digest != _copy_digest(self.impact, self.copy_kind):
            raise SharedStateError('Private copy fingerprint is unqualified')


@dataclass(frozen=True, kw_only=True)
class DurableCopyProgress(PrivateResult):
    """Versioned actual synthetic S7 progress, never a public erasure receipt."""

    attempt_pk: int
    impact_pk: int
    namespace_id: int
    version: int
    progress: peer.CopyProgress = field(repr=False)

    def __post_init__(self):
        """Preserve actual S7 synthetic-only and unknown-revision semantics."""
        super().__post_init__()
        for value in (self.attempt_pk, self.impact_pk, self.namespace_id, self.version):
            integer(value)
        _progress_payload(self.progress)


def _restore_request(row):
    if fingerprint(row.payload) != row.payload_digest:
        raise SharedStateError('Stored private intent digest is unqualified')
    intent = _intent_from(row.payload)
    if (
        row.epoch != _epoch(intent)
        or row.intent_id != intent.request_id
        or row.request_key != intent.request_key
        or row.intent_digest != peer.intent_fingerprint(intent)
    ):
        raise SharedStateError('Stored private intent binding is unqualified')
    expected = (row.pk, row.namespace_id, row.intent_digest, row.payload_digest)
    result = DurablePrivateIntent(
        request_pk=row.pk,
        namespace_id=row.namespace_id,
        intent=intent,
        intent_digest=row.intent_digest,
    )
    if type(result) is not DurablePrivateIntent:
        raise SharedStateError('Private intent constructor shape is unqualified')
    PrivateResult.__post_init__(result)
    for value in (result.request_pk, result.namespace_id):
        integer(value)
    if (
        result.request_pk,
        result.namespace_id,
        result.intent_digest,
        fingerprint(_intent_payload(result.intent)),
    ) != expected:
        raise SharedStateError('Private intent constructor changed validated metadata')
    return result


def _same(previous, incoming):
    try:
        return peer.same_intent(previous, incoming)
    except PermissionError:
        raise
    except ValueError as exc:
        raise SharedConflictError(
            'Private request identity has a conflicting payload'
        ) from exc


def _permitted(intent, authorize, expected):
    if not callable(authorize):
        raise ValueError('A current native-owner authorizer is required')
    if (
        _intent_payload(intent) != expected
        or authorize(intent) is not True
        or _intent_payload(intent) != expected
    ):
        raise PermissionError('Current private request authority is unavailable')


def _observe(intent, authorize, action):
    expected = _intent_payload(intent)
    _permitted(intent, authorize, expected)
    try:
        result = action()
    except MemoryError:
        raise
    except Exception:
        _permitted(intent, authorize, expected)
        raise
    _permitted(intent, authorize, expected)
    if type(result) not in (
        DurablePrivateIntent,
        DurableCopyImpact,
        DurableCopyProgress,
    ):
        raise SharedStateError('Private result shape is unqualified')
    PrivateResult.__post_init__(result)
    with shared_transaction(durable=True):
        require_namespace(
            result.namespace_id,
            epoch=_epoch(intent),
            environment=intent.client.environment,
        )
    return result


def _open_request(request_pk, intent):
    namespace_id = (
        PrivateDeletionRequest.objects
        .filter(pk=request_pk)
        .values_list('namespace_id', flat=True)
        .first()
    )
    if namespace_id is None:
        raise SharedStateError('Private request is unavailable')
    namespace = require_namespace(
        namespace_id, epoch=_epoch(intent), environment=intent.client.environment
    )
    row = PrivateDeletionRequest.objects.select_for_update().get(
        pk=request_pk, namespace=namespace
    )
    _same(_restore_request(row).intent, intent)
    return row


def _restore_impact(row, intent, namespace_id):
    impact = _impact_from(row.payload)
    if row.copy_key != impact.copy_key or row.payload_digest != _copy_digest(
        impact, row.copy_kind
    ):
        raise SharedStateError('Stored private copy binding is unqualified')
    if (
        impact.source.environment != intent.client.environment
        or impact.source.epoch != _epoch(intent)
    ):
        raise SharedStateError(
            'Stored private copy source belongs to another namespace'
        )
    expected = (row.pk, row.request_id, namespace_id, row.copy_kind, row.payload_digest)
    result = DurableCopyImpact(
        impact_pk=row.pk,
        request_pk=row.request_id,
        namespace_id=namespace_id,
        impact=impact,
        copy_kind=row.copy_kind,
        copy_digest=row.payload_digest,
    )
    if type(result) is not DurableCopyImpact:
        raise SharedStateError('Private copy constructor shape is unqualified')
    PrivateResult.__post_init__(result)
    for value in (result.impact_pk, result.request_pk, result.namespace_id):
        integer(value)
    if (
        result.impact_pk,
        result.request_pk,
        result.namespace_id,
        result.copy_kind,
        _copy_digest(result.impact, result.copy_kind),
    ) != expected:
        raise SharedStateError('Private copy constructor changed validated metadata')
    return result


def _open_impact(impact_pk, intent, impact):
    request_pk = (
        PrivateCopyImpact.objects
        .filter(pk=impact_pk)
        .values_list('request_id', flat=True)
        .first()
    )
    if request_pk is None:
        raise SharedStateError('Private copy impact is unavailable')
    request = _open_request(request_pk, intent)
    row = PrivateCopyImpact.objects.select_for_update().get(
        pk=impact_pk, request=request
    )
    restored = _restore_impact(row, intent, request.namespace_id)
    if _impact_payload(restored.impact) != _impact_payload(impact):
        raise SharedConflictError(
            'Copy source, owners, retention or content binding changed'
        )
    return request, row


def _restore_attempt(row, impact, request):
    progress = _progress_from(row.checkpoint)
    peer._progress(
        _restore_request(request).intent, _impact_from(impact.payload), progress
    )
    if (
        row.attempt_id != progress.attempt_id
        or row.ordinal != progress.attempts_used
        or row.max_attempts != progress.max_attempts
        or fingerprint(_progress_payload(progress)) != row.checkpoint_digest
    ):
        raise SharedStateError('Stored private attempt binding is unqualified')
    history = list(
        PrivateCopyAttempt.objects.filter(impact=impact).order_by('ordinal')[
            : progress.max_attempts + 1
        ]
    )
    if (
        len(history) != progress.attempts_used
        or [value.ordinal for value in history]
        != list(range(1, progress.attempts_used + 1))
        or tuple(value.attempt_id for value in history) != progress.attempt_history
        or any(value.max_attempts != progress.max_attempts for value in history)
    ):
        raise SharedStateError('Stored private attempt history is incomplete')
    intent = _restore_request(request).intent
    claimed = _impact_from(impact.payload)
    identities = tuple(value.attempt_id for value in history)
    for prior in history:
        checkpoint = _progress_from(prior.checkpoint)
        peer._progress(intent, claimed, checkpoint)
        if (
            fingerprint(_progress_payload(checkpoint)) != prior.checkpoint_digest
            or checkpoint.attempt_history != identities[: prior.ordinal]
            or checkpoint.attempt_id != prior.attempt_id
            or checkpoint.max_attempts != prior.max_attempts
            or prior.version != checkpoint.sequence + 1
            or (
                prior.ordinal < row.ordinal
                and checkpoint.status not in ('failed', 'uncertain')
            )
        ):
            raise SharedStateError(
                'Stored predecessor does not qualify this private retry'
            )
    expected = (
        row.pk,
        impact.pk,
        request.namespace_id,
        row.version,
        row.checkpoint_digest,
    )
    result = DurableCopyProgress(
        attempt_pk=row.pk,
        impact_pk=impact.pk,
        namespace_id=request.namespace_id,
        version=row.version,
        progress=progress,
    )
    if type(result) is not DurableCopyProgress:
        raise SharedStateError('Private progress constructor shape is unqualified')
    PrivateResult.__post_init__(result)
    for value in (
        result.attempt_pk,
        result.impact_pk,
        result.namespace_id,
        result.version,
    ):
        integer(value)
    if (
        result.attempt_pk,
        result.impact_pk,
        result.namespace_id,
        result.version,
        fingerprint(_progress_payload(result.progress)),
    ) != expected:
        raise SharedStateError(
            'Private progress constructor changed validated metadata'
        )
    return result


def record_copy_impact(request_pk, impact, *, intent, copy_kind, authorize):
    """Retain a bounded immutable supplied copy claim, not an erasure inventory."""
    integer(request_pk)
    detached = _intent_from(_intent_payload(intent))
    copied = _impact_from(_impact_payload(impact))
    binding_digest = _copy_digest(copied, copy_kind)
    if (
        copied.source.environment != detached.client.environment
        or copied.source.epoch != _epoch(detached)
    ):
        raise SharedStateError('Private copy belongs to another source namespace')

    def action():
        with shared_transaction(durable=True):
            request = _open_request(request_pk, detached)
            prior = (
                PrivateCopyImpact.objects
                .select_for_update()
                .filter(request=request, copy_key=copied.copy_key)
                .first()
            )
            if prior is not None:
                if prior.payload_digest != binding_digest:
                    raise SharedConflictError(
                        'Retained copy key has a different claim or kind'
                    )
                return _restore_impact(prior, detached, request.namespace_id)
            if (
                PrivateCopyImpact.objects.filter(request=request).count()
                >= peer.MAX_IMPACTS
            ):
                raise SharedStateError('Private copy impact budget is exhausted')
            row = PrivateCopyImpact.objects.create(
                request=request,
                copy_key=copied.copy_key,
                copy_kind=copy_kind,
                payload=_impact_payload(copied),
                payload_digest=binding_digest,
            )
            return _restore_impact(row, detached, request.namespace_id)

    return _observe(detached, authorize, action)


def start_private_copy_attempt(
    impact_pk, *, intent, impact, attempt, max_attempts, authorize
):
    """Commit an actual synthetic S7 attempt before any caller-owned fixture IO."""
    integer(impact_pk)
    exact_uuid(attempt)
    detached = _intent_from(_intent_payload(intent))
    copied = _impact_from(_impact_payload(impact))
    progress = peer.start_copy_progress(
        detached,
        copied,
        attempt=attempt,
        max_attempts=max_attempts,
        authorize=lambda value: True,
    )

    def action():
        with shared_transaction(durable=True):
            request, stored = _open_impact(impact_pk, detached, copied)
            prior = (
                PrivateCopyAttempt.objects
                .select_for_update()
                .filter(impact=stored)
                .order_by('-ordinal')
                .first()
            )
            if prior is not None:
                actual = _restore_attempt(prior, stored, request)
                if actual.progress == progress:
                    return actual
                raise SharedConflictError('An existing copy attempt cannot be reset')
            if PrivateCopyAttempt.objects.filter(attempt_id=attempt).exists():
                raise SharedConflictError(
                    'A private attempt identity is already retained'
                )
            payload = _progress_payload(progress)
            row = PrivateCopyAttempt.objects.create(
                impact=stored,
                attempt_id=attempt,
                ordinal=1,
                max_attempts=max_attempts,
                version=1,
                checkpoint=payload,
                checkpoint_digest=fingerprint(payload),
            )
            return _restore_attempt(row, stored, request)

    return _observe(detached, authorize, action)


def read_private_copy_progress(impact_pk, *, intent, impact, authorize):
    """Restore the exact current private attempt without a source/receipt grant."""
    integer(impact_pk)
    detached = _intent_from(_intent_payload(intent))
    copied = _impact_from(_impact_payload(impact))

    def action():
        with shared_transaction(durable=True):
            request, stored = _open_impact(impact_pk, detached, copied)
            row = (
                PrivateCopyAttempt.objects
                .select_for_update()
                .filter(impact=stored)
                .order_by('-ordinal')
                .first()
            )
            if row is None:
                raise SharedStateError('Private copy attempt is unavailable')
            return _restore_attempt(row, stored, request)

    return _observe(detached, authorize, action)


def _latest_copy(impact_pk, intent, impact):
    request, stored = _open_impact(impact_pk, intent, impact)
    row = (
        PrivateCopyAttempt.objects
        .select_for_update()
        .filter(impact=stored)
        .order_by('-ordinal')
        .first()
    )
    if row is None:
        raise SharedStateError('Private copy attempt is unavailable')
    return request, stored, row, _restore_attempt(row, stored, request)


def record_private_copy_progress(snapshot, incoming, *, intent, impact, authorize):
    """CAS one actual S7 synthetic transition without issuing any native effect."""
    if type(snapshot) is not DurableCopyProgress:
        raise ValueError('An exact durable private copy checkpoint is required')
    snapshot.__post_init__()
    detached = _intent_from(_intent_payload(intent))
    copied = _impact_from(_impact_payload(impact))
    proposed = _progress_from(_progress_payload(incoming))

    def action():
        with shared_transaction(durable=True):
            _, _, _, actual = _latest_copy(snapshot.impact_pk, detached, copied)
            if actual.version != snapshot.version:
                if (
                    actual.attempt_pk == snapshot.attempt_pk
                    and actual.progress == proposed
                ):
                    return actual
                raise SharedConflictError('Private progress version changed')
            if actual != snapshot:
                raise SharedConflictError('Private progress attempt or payload changed')
        expected = _progress_payload(
            proposed
            if proposed.sequence > actual.progress.sequence
            else actual.progress
        )
        desired = peer.transition_copy_progress(
            detached, copied, actual.progress, proposed, authorize=lambda value: True
        )
        if (
            type(desired) is not peer.CopyProgress
            or _progress_payload(desired) != expected
        ):
            raise SharedStateError(
                'Private transition changed the validated response metadata'
            )
        if desired == actual.progress:
            return actual
        with shared_transaction(durable=True):
            request, stored, row, current = _latest_copy(
                snapshot.impact_pk, detached, copied
            )
            if current != actual:
                raise SharedConflictError('Private progress changed before commit')
            payload = _progress_payload(desired)
            row.checkpoint = payload
            row.checkpoint_digest = fingerprint(payload)
            row.version += 1
            integer(row.version)
            row.save(update_fields=['checkpoint', 'checkpoint_digest', 'version'])
            return _restore_attempt(row, stored, request)

    return _observe(detached, authorize, action)


def retry_private_copy_attempt(snapshot, *, intent, impact, attempt, authorize):
    """Retain failed/uncertain synthetic attempts and their fixed cumulative bound."""
    if type(snapshot) is not DurableCopyProgress:
        raise ValueError('An exact durable private copy checkpoint is required')
    snapshot.__post_init__()
    exact_uuid(attempt)
    detached = _intent_from(_intent_payload(intent))
    copied = _impact_from(_impact_payload(impact))

    def action():
        with shared_transaction(durable=True):
            _, _, _, actual = _latest_copy(snapshot.impact_pk, detached, copied)
            if actual != snapshot:
                raise SharedConflictError(
                    'Only the latest exact copy attempt may retry'
                )
        desired = peer.retry_copy_progress(
            detached,
            copied,
            actual.progress,
            attempt=attempt,
            authorize=lambda value: True,
        )
        with shared_transaction(durable=True):
            request, stored, _, current = _latest_copy(
                snapshot.impact_pk, detached, copied
            )
            if current != actual:
                raise SharedConflictError(
                    'Private copy attempt changed before retry commit'
                )
            if PrivateCopyAttempt.objects.filter(attempt_id=attempt).exists():
                raise SharedConflictError(
                    'A private attempt identity is already retained'
                )
            payload = _progress_payload(desired)
            row = PrivateCopyAttempt.objects.create(
                impact=stored,
                attempt_id=attempt,
                ordinal=desired.attempts_used,
                max_attempts=desired.max_attempts,
                version=1,
                checkpoint=payload,
                checkpoint_digest=fingerprint(payload),
            )
            return _restore_attempt(row, stored, request)

    return _observe(detached, authorize, action)


def record_private_intent(namespace_id, intent, *, authorize):
    """Retain an exact private intent without accepting a native deletion request."""
    integer(namespace_id)
    payload = _intent_payload(intent)
    detached = _intent_from(payload)

    def action():
        with shared_transaction(durable=True):
            namespace = require_namespace(
                namespace_id,
                epoch=_epoch(detached),
                environment=detached.client.environment,
            )
            prior = (
                PrivateDeletionRequest.objects
                .select_for_update()
                .filter(namespace=namespace)
                .filter(
                    Q(request_key=detached.request_key)
                    | Q(intent_id=detached.request_id)
                )
                .first()
            )
            if prior is not None:
                result = _restore_request(prior)
                _same(result.intent, detached)
                return result
            row = PrivateDeletionRequest.objects.create(
                namespace=namespace,
                epoch=namespace.epoch,
                intent_id=detached.request_id,
                request_key=detached.request_key,
                payload=payload,
                payload_digest=fingerprint(payload),
                intent_digest=peer.intent_fingerprint(detached),
            )
            return _restore_request(row)

    return _observe(detached, authorize, action)


def read_private_intent(request_pk, *, intent, authorize):
    """Restore only the exact current-owner-bound private request, never authority."""
    integer(request_pk)
    detached = _intent_from(_intent_payload(intent))

    def action():
        with shared_transaction(durable=True):
            return _restore_request(_open_request(request_pk, detached))

    return _observe(detached, authorize, action)
