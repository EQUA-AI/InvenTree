"""Owner-approved future attestation and readiness review; no erase executor.

Policy: fresh native client admin-change requester plus explicit client grants;
a different fresh native approvals.review actor with the applicable client
scope/grants reviews the exact immutable binding. Source visibility stays
independent. All services and birth capture are off unless explicitly enabled
by trusted server configuration. No endpoint, enrollment or approval is created
at startup, and no source/copy/backup is deleted by any function here.
"""

import hashlib
import logging
from uuid import uuid5

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction

from assets.integration_authority import explicitly_granted_client_ids
from assets.integration_contracts import ClientSourceIdentity
from assets.models import Client
from integration_state.models import (
    NativeAuthorityDecision,
    NativeAuthorityEpoch,
    NativeAuthorityRequest,
    NativeAuthorityWithdrawal,
    NativeSourceBirth,
    PrivateCopyImpact,
    PrivateDeletionRequest,
    SourceNamespace,
)
from integration_state.s7_deletion_requests import DeletionIntent
from integration_state.shared_events import fingerprint, shared_transaction
from integration_state.shared_requests import record_private_intent

logger = logging.getLogger(__name__)
POLICY_VERSION = 'aimms.native-authority.v1'
MAX_CLIENTS = 32
MAX_REVIEW_ROWS = 32
MAX_COPY_ROWS = 1000


class NativeAuthorityError(PermissionError):
    """Opaque refusal; prior observations or supplied labels confer no access."""

    def __init__(self):
        """Keep private identities, paths and source failure details out."""
        super().__init__('Native authority is unavailable')


def enabled():
    """Only a literal server-side true enrolls this new code boundary."""
    return getattr(settings, 'AIMMS_INTEGRATION_AUTHORITY_ENABLED', False) is True


def _require_enabled():
    from InvenTree.restore_hold import restore_hold_enabled

    if not enabled() or settings.DATABASE_ROUTERS or restore_hold_enabled():
        raise NativeAuthorityError()


def _literal(value, size):
    if type(value) is not str or not 1 <= len(value) <= size or not value.strip():
        raise NativeAuthorityError()
    return value


def _integer(value):
    if type(value) is not int or not 1 <= value <= (1 << 63) - 1:
        raise NativeAuthorityError()
    return value


def _digest(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in '0123456789abcdef' for c in value)
    ):
        raise NativeAuthorityError()
    return value


def _actor_key(actor):
    return fingerprint({'pk': actor.pk, 'created': actor.date_joined.isoformat()})


def _source_digest(kind, source_pk):
    from assets.attribution_evidence import material_digest

    try:
        return material_digest(kind, source_pk)
    except (OSError, TypeError, ValueError):
        raise NativeAuthorityError() from None


def _request_actor(request, *, review=False):
    """Use actual native DRF authentication, never an actor supplied in JSON.

    OAuth is deliberately unsupported for these new write actions until a
    native granular review-action contract is qualified. Ordinary native OAuth
    paths are unchanged. Tests use the real DRF forced-auth mechanism only.
    """
    from rest_framework.authentication import BasicAuthentication, SessionAuthentication
    from rest_framework.request import ForcedAuthentication, Request

    from users.models import RuleSet

    _require_enabled()
    if (
        not isinstance(request, Request)
        or request.method != 'POST'
        or not isinstance(
            request.successful_authenticator,
            (BasicAuthentication, SessionAuthentication, ForcedAuthentication),
        )
        or request.auth is not None
        or not getattr(request.user, 'is_authenticated', False)
    ):
        raise NativeAuthorityError()
    actor = (
        get_user_model()
        .objects.filter(pk=_integer(request.user.pk), is_active=True)
        .first()
    )
    if actor is None:
        raise NativeAuthorityError()
    allowed = (
        actor.is_superuser or actor.has_perm('approvals.review')
        if review
        else actor.is_superuser
        or RuleSet.objects.filter(
            group__user=actor, name='admin', can_change=True
        ).exists()
    )
    if not allowed:
        raise NativeAuthorityError()
    return actor


def _client_bindings(actor, ids):
    """Check current native client scope and independent grants for every owner."""
    from tasks.scope import ScopeError, scope_for_actor

    if type(ids) not in (tuple, list) or not 1 <= len(ids) <= MAX_CLIENTS:
        raise NativeAuthorityError()
    ids = tuple(_integer(i) for i in ids)
    if tuple(sorted(set(ids))) != ids:
        raise NativeAuthorityError()
    grants = {r['client_id'] for r in explicitly_granted_client_ids(actor)}
    try:
        native_ids = {
            s.client_id
            for s in scope_for_actor(actor)
            if s.customer_id is None and s.site_key is None
        }
    except ScopeError:
        raise NativeAuthorityError() from None
    if not set(ids).issubset(grants & native_ids):
        raise NativeAuthorityError()
    clients = list(Client.objects.filter(pk__in=ids, active=True).order_by('pk'))
    if len(clients) != len(ids):
        raise NativeAuthorityError()
    return [
        {
            'pk': c.pk,
            'incarnation': fingerprint({
                'code': c.code,
                'created': c.created_at.isoformat(),
            }),
        }
        for c in clients
    ]


def _namespace(pk, epoch=None):
    namespace = SourceNamespace.objects.filter(pk=_integer(pk), active=True).first()
    if (
        namespace is None
        or not namespace.epoch.int
        or not _literal(namespace.environment, 128)
        or (epoch is not None and namespace.epoch != epoch)
    ):
        raise NativeAuthorityError()
    return namespace


def start_attribution_epoch(namespace_id):
    """Trusted server enrollment only; never called by startup or request data."""
    _require_enabled()
    with shared_transaction(durable=True):
        namespace = SourceNamespace.objects.select_for_update().get(
            pk=_namespace(namespace_id).pk
        )
        current = NativeAuthorityEpoch.objects.filter(
            namespace=namespace, active=True
        ).first()
        if current is not None:
            if (
                current.namespace_epoch != namespace.epoch
                or current.source_environment != namespace.environment
            ):
                raise NativeAuthorityError()
            return current
        return NativeAuthorityEpoch.objects.create(
            namespace=namespace,
            namespace_epoch=namespace.epoch,
            source_environment=namespace.environment,
            active=True,
        )


def record_source_birth(kind, source_pk):
    """Best-effort marker for a real supported ORM insertion, never old rows.

    A missing/faulted marker withholds later attestation. Its own savepoint
    preserves native writes on optional metadata failure. Bulk/raw/fixture
    writes do not receive a marker. Retained key reuse is explicitly ambiguous.
    """
    from InvenTree.restore_hold import restore_hold_enabled

    if not enabled() or settings.DATABASE_ROUTERS or restore_hold_enabled():
        return
    if type(kind) is not str or kind not in ('work', 'maintenance', 'attachment'):
        return
    _integer(source_pk)
    try:
        with transaction.atomic():
            epochs = list(
                NativeAuthorityEpoch.objects.select_for_update().filter(
                    active=True, namespace__active=True
                )[:2]
            )
            if (
                len(epochs) != 1
                or epochs[0].namespace_epoch != epochs[0].namespace.epoch
                or epochs[0].source_environment != epochs[0].namespace.environment
            ):
                return
            marker, created = NativeSourceBirth.objects.get_or_create(
                epoch=epochs[0], entity_kind=kind, source_pk=source_pk
            )
            if not created:
                NativeSourceBirth.objects.filter(pk=marker.pk).update(ambiguous=True)
    except DatabaseError:
        # No storage paths or database exception contents are logged.
        logger.warning('Optional native birth capture unavailable')


def _binding(row):
    return {
        'policy': POLICY_VERSION,
        'purpose': row.purpose,
        'namespace': row.namespace_id,
        'epoch': str(row.namespace_epoch),
        'request_key': row.request_key,
        'requester_pk': row.requester_pk,
        'requester_key': row.requester_key,
        'clients': row.client_bindings,
        'request_reference': row.request_reference,
        'reference': row.reference_digest,
        'source': row.source_binding,
    }


def _checked_request(pk, *, requester_current=True):
    row = NativeAuthorityRequest.objects.filter(pk=_integer(pk)).first()
    if row is None or row.binding_digest != fingerprint(_binding(row)):
        raise NativeAuthorityError()
    if (
        type(row.source_binding) is not dict
        or row.source_binding.get('environment')
        != _namespace(row.namespace_id, row.namespace_epoch).environment
    ):
        raise NativeAuthorityError()
    if requester_current:
        _requester_current(row)
    return row


def _requester_current(row):
    from users.models import RuleSet

    actor = get_user_model().objects.filter(pk=row.requester_pk, is_active=True).first()
    if (
        actor is None
        or _actor_key(actor) != row.requester_key
        or not (
            actor.is_superuser
            or RuleSet.objects.filter(
                group__user=actor, name='admin', can_change=True
            ).exists()
        )
        or _client_bindings(actor, [c['pk'] for c in row.client_bindings])
        != row.client_bindings
    ):
        raise NativeAuthorityError()


def _write_request(
    request, namespace, *, purpose, request_key, client_ids, reference, source_binding
):
    actor = _request_actor(request)
    clients = _client_bindings(actor, client_ids)
    row = NativeAuthorityRequest(
        namespace=namespace,
        namespace_epoch=namespace.epoch,
        purpose=purpose,
        request_key=_literal(request_key, 128),
        requester_pk=actor.pk,
        requester_key=_actor_key(actor),
        client_bindings=clients,
        request_reference=_literal(reference, 1024),
        reference_digest=hashlib.sha256(_literal(reference, 1024).encode()).hexdigest(),
        source_binding=source_binding,
    )
    row.binding_digest = fingerprint(_binding(row))
    with shared_transaction(durable=True):
        locked_namespace = SourceNamespace.objects.select_for_update().get(
            pk=namespace.pk
        )
        _namespace(locked_namespace.pk, namespace.epoch)
        current_actor = _request_actor(request)
        if (
            _actor_key(current_actor) != row.requester_key
            or _client_bindings(current_actor, client_ids) != clients
        ):
            raise NativeAuthorityError()
        prior = NativeAuthorityRequest.objects.filter(
            namespace=namespace, purpose=purpose, request_key=request_key
        ).first()
        if prior is not None:
            if (
                prior.binding_digest != row.binding_digest
                or fingerprint(_binding(prior)) != row.binding_digest
            ):
                raise NativeAuthorityError()
            return prior
        row.save(force_insert=True)
    return row


def request_attribution(
    request,
    *,
    namespace_id,
    kind,
    source_pk,
    client_ids,
    evidence_reference,
    request_key,
):
    """Propose a revision-specific future attestation; never backfill old rows."""
    from assets.attribution_evidence import native_visible, subject

    actor = _request_actor(request)
    if type(kind) is not str or kind not in ('work', 'maintenance', 'attachment'):
        raise NativeAuthorityError()
    clients = _client_bindings(actor, client_ids)
    namespace = _namespace(namespace_id)
    birth = NativeSourceBirth.objects.filter(
        epoch__namespace=namespace,
        epoch__namespace_epoch=namespace.epoch,
        epoch__source_environment=namespace.environment,
        epoch__active=True,
        entity_kind=kind,
        source_pk=_integer(source_pk),
        ambiguous=False,
    ).first()
    if birth is None or not native_visible(actor, subject(kind, source_pk)):
        raise NativeAuthorityError()
    digest = _source_digest(kind, source_pk)
    if digest is None:
        raise NativeAuthorityError()
    if _client_bindings(_request_actor(request), client_ids) != clients:
        raise NativeAuthorityError()
    return _write_request(
        request,
        namespace,
        purpose='attribution',
        request_key=request_key,
        client_ids=client_ids,
        reference=evidence_reference,
        source_binding={
            'schema': 'aimms.source-attestation.v1',
            'environment': namespace.environment,
            'birth_pk': birth.pk,
            'kind': kind,
            'source_pk': source_pk,
            'incarnation': str(birth.incarnation),
            'material_sha256': digest,
        },
    )


def request_deletion_readiness(
    request, *, namespace_id, client_pk, client_request_reference, request_key, scope
):
    """Bind an actual native requester to existing private S7/S5 intent state."""
    actor = _request_actor(request)
    _client_bindings(actor, (client_pk,))
    namespace = _namespace(namespace_id)
    reference_digest = hashlib.sha256(
        _literal(client_request_reference, 1024).encode()
    ).hexdigest()
    actor_key = fingerprint({
        'actor': _actor_key(actor),
        'client_request': reference_digest,
    })
    intent = DeletionIntent(
        request_id=uuid5(
            namespace.epoch, 'native-readiness:' + _literal(request_key, 128)
        ),
        request_key=request_key,
        request_revision=1,
        actor_key=actor_key,
        client=ClientSourceIdentity(namespace.environment, client_pk),
        scope=scope,
    )

    def authorize(_intent):
        current = _request_actor(request)
        return _actor_key(current) == _actor_key(actor) and bool(
            _client_bindings(current, (client_pk,))
        )

    stored = record_private_intent(namespace.pk, intent, authorize=authorize)
    # S5 commits independently; a later refusal leaves its retained private
    # intent, never a falsely approved native command or source deletion.
    return _write_request(
        request,
        namespace,
        purpose='deletion_readiness',
        request_key=request_key,
        client_ids=(client_pk,),
        reference=client_request_reference,
        source_binding={
            'schema': 'aimms.deletion-readiness.v1',
            'environment': namespace.environment,
            'private_request_pk': stored.request_pk,
            'intent_digest': stored.intent_digest,
        },
    )


def _attribution_current(row, actor):
    from assets.attribution_evidence import native_visible, subject

    b = row.source_binding
    if (
        type(b) is not dict
        or set(b)
        != {
            'schema',
            'environment',
            'birth_pk',
            'kind',
            'source_pk',
            'incarnation',
            'material_sha256',
        }
        or b['schema'] != 'aimms.source-attestation.v1'
    ):
        raise NativeAuthorityError()
    birth = NativeSourceBirth.objects.filter(
        pk=b['birth_pk'],
        entity_kind=b['kind'],
        source_pk=b['source_pk'],
        ambiguous=False,
        epoch__active=True,
        epoch__namespace_id=row.namespace_id,
        epoch__namespace_epoch=row.namespace_epoch,
        epoch__source_environment=b['environment'],
        incarnation=b['incarnation'],
    ).first()
    if (
        birth is None
        or not native_visible(actor, subject(b['kind'], b['source_pk']))
        or _source_digest(b['kind'], b['source_pk']) != b['material_sha256']
    ):
        raise NativeAuthorityError()
    return b['material_sha256']


def _readiness_intent(row):
    """Restore the exact retained request before projecting any declared scope."""
    from integration_state.shared_requests import _restore_request

    b = row.source_binding
    if (
        type(b) is not dict
        or set(b) != {'schema', 'environment', 'private_request_pk', 'intent_digest'}
        or b['schema'] != 'aimms.deletion-readiness.v1'
    ):
        raise NativeAuthorityError()
    private = PrivateDeletionRequest.objects.filter(
        pk=b['private_request_pk'],
        namespace_id=row.namespace_id,
        epoch=row.namespace_epoch,
        intent_digest=b['intent_digest'],
    ).first()
    if private is None:
        raise NativeAuthorityError()
    try:
        return _restore_request(private)
    except (TypeError, ValueError):
        raise NativeAuthorityError() from None


def _readiness_impact(row):
    """Compute bounded review truth from retained claims, never trust completeness.

    The current code cannot certify full domain/history/backup coverage. Every
    view explicitly retains that gap and keeps erasure authority false. Shared,
    protected and unknown copy ownership remains withheld from removal; private
    foreign owner IDs, keys, storage URLs and content are not projected.
    """
    stored = _readiness_intent(row)
    copies = list(
        PrivateCopyImpact.objects.filter(request_id=stored.request_pk).order_by('pk')[
            : MAX_COPY_ROWS + 1
        ]
    )
    if len(copies) > MAX_COPY_ROWS:
        raise NativeAuthorityError()
    projected = []
    from integration_state.shared_requests import _copy_digest, _impact_from

    for copy in copies:
        impact = _impact_from(copy.payload)
        if _copy_digest(impact, copy.copy_kind) != copy.payload_digest:
            raise NativeAuthorityError()
        projected.append({
            'claim_digest': copy.payload_digest,
            'kind': copy.copy_kind,
            'removal_authorized': False,
            'shared_or_unknown_owner': impact.owners is None
            or len(impact.owners) != 1
            or impact.owners[0].pk != row.client_bindings[0]['pk'],
            'retention': impact.retention,
        })
    from django.db import connection

    from integration_state.capture import capture_is_installed
    from integration_state.readiness_coverage import scope_coverage

    coverage = scope_coverage(
        stored.intent.scope, placement_capture_verified=capture_is_installed(connection)
    )
    return {
        'schema': 'aimms.readiness-impact.v2',
        'scope_coverage': coverage,
        'request_digest': row.binding_digest,
        'copy_claims': projected,
        'coverage_complete': False,
        'gaps': [
            'source_population_and_writers_unqualified',
            'historical_attribution_incomplete',
            'copy_inventory_and_backup_feasibility_unqualified',
        ],
        'default_retention': 'indefinite',
        'erasure_authorized': False,
    }


def inspect_authority_request(request, *, request_pk):
    """Expose a bounded exact review hash only to a current eligible reviewer."""
    actor = _request_actor(request, review=True)
    row = _checked_request(request_pk, requester_current=False)
    if not NativeAuthorityDecision.objects.filter(request=row).exists():
        _requester_current(row)
    if (
        actor.pk == row.requester_pk
        or _client_bindings(actor, [c['pk'] for c in row.client_bindings])
        != row.client_bindings
    ):
        raise NativeAuthorityError()
    if row.purpose == 'attribution':
        impact = {
            'schema': 'aimms.attestation-review.v1',
            'material_sha256': _attribution_current(row, actor),
            'historical_backfill': False,
        }
        details = {
            'source': {
                'environment': row.source_binding['environment'],
                'kind': row.source_binding['kind'],
                'source_pk': row.source_binding['source_pk'],
            }
        }
    elif row.purpose == 'deletion_readiness':
        impact = _readiness_impact(row)
        intent = _readiness_intent(row).intent
        details = {
            # These are the requester's declared subjects, not discovered
            # foreign records or proof of source ownership/completeness.
            'declared_scope': [
                {
                    'environment': s.environment,
                    'epoch': str(s.epoch),
                    'entity_kind': s.entity_kind,
                    'aspect': s.aspect,
                    'source_id': s.source_id,
                }
                for s in intent.scope
            ],
            'source_ownership_verified': False,
        }
    else:
        raise NativeAuthorityError()
    impact_digest = fingerprint(impact)
    decision = NativeAuthorityDecision.objects.filter(request=row).first()
    review_status = (
        'withdrawn'
        if NativeAuthorityWithdrawal.objects.filter(request=row).exists()
        else 'pending'
        if decision is None
        else 'stale'
        if decision.request_digest != row.binding_digest
        or decision.impact_digest != impact_digest
        else 'approved_review'
        if decision.approved
        else 'rejected_review'
    )
    return {
        'request_pk': row.pk,
        'purpose': row.purpose,
        'request_digest': row.binding_digest,
        'impact_digest': impact_digest,
        'impact': impact,
        'policy': POLICY_VERSION,
        'review_status': review_status,
        'request_reference': row.request_reference,
        'request_details': details,
        'client_ids': [c['pk'] for c in row.client_bindings],
    }


def decide_authority_request(
    request, *, request_pk, expected_request_digest, expected_impact_digest, approved
):
    """Retain one exact review by a different actor; no erasure effect exists."""
    _digest(expected_request_digest)
    _digest(expected_impact_digest)
    if type(approved) is not bool:
        raise NativeAuthorityError()
    view = inspect_authority_request(request, request_pk=request_pk)
    if (
        view['request_digest'] != expected_request_digest
        or view['impact_digest'] != expected_impact_digest
    ):
        raise NativeAuthorityError()
    if view['review_status'] == 'withdrawn':
        raise NativeAuthorityError()
    actor = _request_actor(request, review=True)
    with shared_transaction(durable=True):
        namespace_id = NativeAuthorityRequest.objects.values_list(
            'namespace_id', flat=True
        ).get(pk=request_pk)
        SourceNamespace.objects.select_for_update().get(pk=namespace_id)
        row = NativeAuthorityRequest.objects.select_for_update().get(pk=request_pk)
        _namespace(row.namespace_id, row.namespace_epoch)
        if NativeAuthorityWithdrawal.objects.filter(request=row).exists():
            raise NativeAuthorityError()
        current = _request_actor(request, review=True)
        if (
            current.pk != actor.pk
            or _actor_key(current) != _actor_key(actor)
            or current.pk == row.requester_pk
            or _client_bindings(current, [c['pk'] for c in row.client_bindings])
            != row.client_bindings
            or row.binding_digest != expected_request_digest
            or fingerprint(_binding(row)) != expected_request_digest
        ):
            raise NativeAuthorityError()
        prior = NativeAuthorityDecision.objects.filter(request=row).first()
        if prior is not None:
            if (
                prior.reviewer_pk,
                prior.reviewer_key,
                prior.approved,
                prior.request_digest,
                prior.impact_digest,
            ) != (
                actor.pk,
                _actor_key(actor),
                approved,
                expected_request_digest,
                expected_impact_digest,
            ):
                raise NativeAuthorityError()
            result = prior
        else:
            _requester_current(row)
            result = NativeAuthorityDecision.objects.create(
                request=row,
                reviewer_pk=actor.pk,
                reviewer_key=_actor_key(actor),
                approved=approved,
                request_digest=expected_request_digest,
                impact_digest=expected_impact_digest,
            )
    # Refresh outside state ownership: native source/file work is never moved
    # under the metadata lock. A late drift retains the decision but refuses
    # this outcome; admission always performs its own current-source check.
    if (
        inspect_authority_request(request, request_pk=request_pk)['impact_digest']
        != expected_impact_digest
    ):
        raise NativeAuthorityError()
    return result


def withdraw_authority_request(
    request, *, request_pk, expected_request_digest, reason_reference
):
    """Invalidate one exact claim using fresh native client admin-change authority.

    This action only reduces disclosure/readiness. It has no erasure effect and
    cannot restore an old claim. A new request and different-user review are
    required to establish subsequent evidence.
    """
    _digest(expected_request_digest)
    reason = _literal(reason_reference, 1024)
    actor = _request_actor(request)
    row = _checked_request(request_pk, requester_current=False)
    if row.binding_digest != expected_request_digest:
        raise NativeAuthorityError()
    clients = [c['pk'] for c in row.client_bindings]
    if _client_bindings(actor, clients) != row.client_bindings:
        raise NativeAuthorityError()
    with shared_transaction(durable=True):
        SourceNamespace.objects.select_for_update().get(pk=row.namespace_id)
        row = NativeAuthorityRequest.objects.select_for_update().get(pk=row.pk)
        _namespace(row.namespace_id, row.namespace_epoch)
        current = _request_actor(request)
        if (
            _actor_key(current) != _actor_key(actor)
            or _client_bindings(current, clients) != row.client_bindings
            or row.binding_digest != expected_request_digest
            or fingerprint(_binding(row)) != expected_request_digest
        ):
            raise NativeAuthorityError()
        values = {
            'actor_pk': current.pk,
            'actor_key': _actor_key(current),
            'request_digest': expected_request_digest,
            'reason_reference': reason,
            'reference_digest': hashlib.sha256(reason.encode()).hexdigest(),
        }
        prior = NativeAuthorityWithdrawal.objects.filter(request=row).first()
        if prior is not None:
            if any(getattr(prior, key) != value for key, value in values.items()):
                raise NativeAuthorityError()
            return prior
        return NativeAuthorityWithdrawal.objects.create(request=row, **values)


def qualified_client_ids(*, environment, kind, source_pk, actor):
    """Validate a future-only exact source proof; labels and current owner fail."""
    _require_enabled()
    if type(environment) is not str or type(kind) is not str:
        raise NativeAuthorityError()
    namespace = SourceNamespace.objects.filter(
        environment=environment, active=True
    ).first()
    if namespace is None:
        return ()
    births = NativeSourceBirth.objects.filter(
        epoch__namespace=namespace,
        epoch__namespace_epoch=namespace.epoch,
        epoch__source_environment=environment,
        epoch__active=True,
        entity_kind=kind,
        source_pk=_integer(source_pk),
        ambiguous=False,
    )
    birth = births.first()
    if birth is None:
        return ()
    rows = list(
        NativeAuthorityRequest.objects
        .filter(
            namespace=namespace,
            namespace_epoch=namespace.epoch,
            purpose='attribution',
            source_binding__birth_pk=birth.pk,
            decision__approved=True,
            withdrawal__isnull=True,
        )
        .select_related('decision')
        .order_by('pk')[: MAX_REVIEW_ROWS + 1]
    )
    if not rows or len(rows) > MAX_REVIEW_ROWS:
        return ()
    # Approval is a source fact authenticated at decision time, not a grant
    # delegated by the signing staff. Current-reader authorization is separate;
    # append-only withdrawal invalidates a claim without rewriting that audit.
    trusted, entitled = [], []
    for row in rows:
        decision = row.decision
        if (
            row.binding_digest != fingerprint(_binding(row))
            or decision.request_digest != row.binding_digest
            or row.requester_pk == decision.reviewer_pk
            or decision.impact_digest
            != fingerprint({
                'schema': 'aimms.attestation-review.v1',
                'material_sha256': row.source_binding.get('material_sha256'),
                'historical_backfill': False,
            })
        ):
            return ()
        ids = tuple(c['pk'] for c in row.client_bindings)
        trusted.append((row, ids))
        try:
            if _client_bindings(actor, ids) == row.client_bindings:
                entitled.append(row)
        except NativeAuthorityError:
            pass
    # Do not open source bytes for a caller without a matching independent
    # grant. Once entitled, evaluate all current approved ownership sets so an
    # ungranted conflicting claim cannot be silently ignored.
    current_digest = None
    for row in entitled:
        try:
            current_digest = _attribution_current(row, actor)
            break
        except NativeAuthorityError:
            continue
    if current_digest is None:
        return ()
    accepted = {
        ids
        for row, ids in trusted
        if row.source_binding.get('material_sha256') == current_digest
    }
    # Conflicting approved ownership sets are ambiguous, never unioned into
    # broader authority. Shared ownership needs every explicitly granted owner.
    return next(iter(accepted)) if len(accepted) == 1 else ()
