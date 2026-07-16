"""Transactional services for governed procedure authoring and publication."""

import hashlib
import json
import uuid

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db import transaction
from django.utils import timezone

from approvals.models import ActionType, Approval, compute_idempotency_key
from tasks.models import (
    Procedure,
    ProcedureResourceRequirement,
    ProcedureRevision,
    ProcedureRevisionStatus,
    ProcedureStep,
)
from tasks.permissions import (
    AUTHOR_PROCEDURE,
    PUBLISH_PROCEDURE,
    REVIEW_PROCEDURE,
    require_permission,
)
from tasks.scope import MaintenanceScope, ScopeError, scope_for_actor


class ProcedurePublishError(Exception):
    """Raised when a procedure revision cannot be safely published."""


class ProcedureCommandError(Exception):
    """Raised when an authoring command is invalid."""


class ProcedureImmutableError(ProcedureCommandError):
    """Raised when governed content is changed outside the draft state."""


class ProcedureStaleVersionError(ProcedureCommandError):
    """Raised when optimistic concurrency detects a stale draft."""


def _require_draft(revision, expected_content_version):
    """Lock and validate a mutable revision and its concurrency token."""
    revision = ProcedureRevision.objects.select_for_update().get(pk=revision.pk)
    if revision.status != ProcedureRevisionStatus.DRAFT:
        raise ProcedureImmutableError('Procedure revision content is immutable')
    if revision.content_version != expected_content_version:
        raise ProcedureStaleVersionError(
            f'Expected content version {expected_content_version}; '
            f'current version is {revision.content_version}'
        )
    return revision


def _advance_version(revision):
    revision.content_version += 1
    revision.content_hash = ''
    revision.save(update_fields=['content_version', 'content_hash'])


def _procedure_scope(procedure):
    if procedure.customer_id is None:
        raise ScopeError('Procedure customer scope is unresolved')
    return MaintenanceScope(customer_id=procedure.customer_id, site_key=None)


def _require_scope(actor, procedure):
    scope = _procedure_scope(procedure)
    if scope not in scope_for_actor(actor):
        raise ScopeError('Actor and procedure maintenance scopes do not match')
    return scope


@transaction.atomic
def next_draft_revision(procedure, actor):
    """Create the next numbered draft revision for a scoped procedure family."""
    require_permission(actor, AUTHOR_PROCEDURE)
    procedure = Procedure.objects.select_for_update().get(pk=procedure.pk)
    _require_scope(actor, procedure)
    latest = procedure.revisions.order_by('-revision').first()
    return ProcedureRevision.objects.create(
        procedure=procedure,
        revision=(latest.revision + 1) if latest else 1,
        work_order_type=latest.work_order_type if latest else 'preventive',
        default_estimated_minutes=(
            latest.default_estimated_minutes if latest else None
        ),
        schema_version=latest.schema_version if latest else 1,
        created_by=actor,
    )


@transaction.atomic
def edit_draft_revision(revision, actor, expected_content_version, **values):
    """Edit draft revision metadata and advance its concurrency token."""
    require_permission(actor, AUTHOR_PROCEDURE)
    revision = _require_draft(revision, expected_content_version)
    _require_scope(actor, revision.procedure)
    for field, value in values.items():
        setattr(revision, field, value)
    _advance_version(revision)
    revision.save(update_fields=[*values, 'content_version', 'content_hash'])
    return revision


@transaction.atomic
def create_draft_step(revision, actor, expected_content_version, **values):
    """Append a step to a draft revision."""
    require_permission(actor, AUTHOR_PROCEDURE)
    revision = _require_draft(revision, expected_content_version)
    _require_scope(actor, revision.procedure)
    values.setdefault('sequence', revision.steps.count() + 1)
    step = ProcedureStep.objects.create(revision=revision, **values)
    _advance_version(revision)
    return step


@transaction.atomic
def edit_draft_step(step, actor, expected_content_version, delete=False, **values):
    """Edit or delete a stable-key step belonging to a draft revision."""
    require_permission(actor, AUTHOR_PROCEDURE)
    revision = _require_draft(step.revision, expected_content_version)
    _require_scope(actor, revision.procedure)
    if delete:
        step.delete()
        result = None
    else:
        for field, value in values.items():
            setattr(step, field, value)
        step.save(update_fields=list(values))
        result = step
    _advance_version(revision)
    return result


@transaction.atomic
def reorder_draft_steps(revision, actor, expected_content_version, step_keys):
    """Atomically replace ordering using the full set of stable step keys."""
    require_permission(actor, AUTHOR_PROCEDURE)
    revision = _require_draft(revision, expected_content_version)
    _require_scope(actor, revision.procedure)
    keys = [str(key) for key in step_keys]
    current = list(revision.steps.order_by('pk'))
    current_keys = {str(step.key) for step in current}
    if len(keys) != len(set(keys)) or set(keys) != current_keys:
        raise ProcedureCommandError(
            'Step keys must be the complete, unique set for this revision'
        )
    by_key = {str(step.key): step for step in current}
    # Avoid the unique sequence constraint while swapping positions.
    offset = len(current) + 1
    for step in current:
        step.sequence += offset
        step.save(update_fields=['sequence'])
    for sequence, key in enumerate(keys, start=1):
        step = by_key[key]
        step.sequence = sequence
        step.save(update_fields=['sequence'])
    _advance_version(revision)
    return revision.steps.order_by('sequence')


@transaction.atomic
def create_draft_resource(revision, actor, expected_content_version, **values):
    """Append a resource requirement to a draft revision."""
    require_permission(actor, AUTHOR_PROCEDURE)
    revision = _require_draft(revision, expected_content_version)
    _require_scope(actor, revision.procedure)
    values.setdefault('sequence', revision.resource_requirements.count() + 1)
    resource = ProcedureResourceRequirement.objects.create(revision=revision, **values)
    _advance_version(revision)
    return resource


@transaction.atomic
def edit_draft_resource(
    resource, actor, expected_content_version, delete=False, **values
):
    """Edit or delete a resource requirement on a draft revision."""
    require_permission(actor, AUTHOR_PROCEDURE)
    revision = _require_draft(resource.revision, expected_content_version)
    _require_scope(actor, revision.procedure)
    if delete:
        resource.delete()
        result = None
    else:
        for field, value in values.items():
            setattr(resource, field, value)
        resource.save(update_fields=list(values))
        result = resource
    _advance_version(revision)
    return result


def _blocker(code, message):
    return {'code': code, 'message': message}


def review_blockers(revision, reviewer=None):
    """Return stable-coded, currently computable publication blockers."""
    blockers = []
    if revision.procedure.customer_id is None:
        blockers.append(_blocker('SCOPE_UNRESOLVED', 'Procedure scope is unresolved'))
    steps = list(revision.steps.order_by('sequence', 'pk'))
    if not any(step.required for step in steps):
        blockers.append(
            _blocker('REQUIRED_STEP_MISSING', 'At least one required step is required')
        )
    sequences = [step.sequence for step in steps]
    if len(sequences) != len(set(sequences)) or sequences != list(
        range(1, len(steps) + 1)
    ):
        blockers.append(
            _blocker(
                'STEP_ORDER_INVALID', 'Step ordering must be unique and contiguous'
            )
        )
    invalid_values = any(
        (
            step.value_type == 'number'
            and step.min_value is not None
            and step.max_value is not None
            and step.min_value > step.max_value
        )
        or (step.value_type == 'choice' and not step.allowed_values)
        or (
            step.value_type != 'number'
            and (step.min_value is not None or step.max_value is not None or step.unit)
        )
        or (step.value_type != 'choice' and bool(step.allowed_values))
        for step in steps
    )
    if invalid_values:
        blockers.append(
            _blocker(
                'STEP_VALUE_RULE_INVALID',
                'A step has invalid type-specific value rules',
            )
        )
    if any(
        step.step_type == 'safety' and step.safety_gate_template_id is None
        for step in steps
    ):
        blockers.append(
            _blocker(
                'SAFETY_REFERENCE_INVALID', 'A safety step has no safety reference'
            )
        )
    resources = list(revision.resource_requirements.select_related('part'))
    if any(
        resource.part_id is None or not getattr(resource.part, 'active', True)
        for resource in resources
    ):
        blockers.append(
            _blocker(
                'RESOURCE_INVALID', 'A resource is missing or inactive in the catalog'
            )
        )
    if revision.field_decisions.exclude(decision__in=['accepted', 'rejected']).exists():
        blockers.append(
            _blocker(
                'FIELD_DECISION_UNRESOLVED', 'An assisted field decision is unresolved'
            )
        )
    if revision.revision > 1 and not revision.change_summary.strip():
        blockers.append(
            _blocker(
                'CHANGE_SUMMARY_REQUIRED', 'Later revisions require a change summary'
            )
        )
    if reviewer is not None and reviewer.pk == revision.created_by_id:
        blockers.append(
            _blocker(
                'AUTHOR_REVIEWER_CONFLICT',
                'Author and reviewer must be different users',
            )
        )
    if revision.review_due_at is not None and revision.review_due_at <= timezone.now():
        blockers.append(
            _blocker('REVIEW_POLICY_INVALID', 'Review due date must be in the future')
        )
    return blockers


def _reviewed_snapshot(revision):
    """Build a stable JSON-compatible definition snapshot."""
    revision_fields = (
        'revision',
        'work_order_type',
        'change_summary',
        'default_estimated_minutes',
        'schema_version',
        'content_version',
    )
    step_fields = (
        'key',
        'sequence',
        'step_type',
        'title',
        'instruction',
        'required',
        'estimated_minutes',
        'required_permission',
        'value_type',
        'unit',
        'min_value',
        'max_value',
        'allowed_values',
        'evidence_policy',
        'safety_gate_template_id',
    )
    resource_fields = (
        'key',
        'sequence',
        'kind',
        'part_id',
        'quantity',
        'fulfillment_mode',
        'required',
        'substitution_policy',
        'requires_scan',
        'notes',
    )

    def normalized(obj, fields):
        return {
            field: str(getattr(obj, field))
            if field in {'key', 'min_value', 'max_value', 'quantity'}
            and getattr(obj, field) is not None
            else getattr(obj, field)
            for field in fields
        }

    return {
        'procedure_id': revision.procedure_id,
        'revision': normalized(revision, revision_fields),
        'steps': [
            normalized(item, step_fields)
            for item in revision.steps.order_by('sequence', 'pk')
        ],
        'resources': [
            normalized(item, resource_fields)
            for item in revision.resource_requirements.order_by('sequence', 'pk')
        ],
    }


@transaction.atomic
def request_review(revision, actor, expected_content_version=None):
    """Freeze a draft definition and create its publication approval."""
    require_permission(actor, REVIEW_PROCEDURE)
    revision = (
        ProcedureRevision.objects
        .select_for_update()
        .select_related('procedure')
        .get(pk=revision.pk)
    )
    if revision.status != ProcedureRevisionStatus.DRAFT:
        raise ProcedureImmutableError('Only draft revisions can enter review')
    if (
        expected_content_version is not None
        and revision.content_version != expected_content_version
    ):
        raise ProcedureStaleVersionError('Procedure revision content version is stale')
    scope = _require_scope(actor, revision.procedure)
    blockers = review_blockers(revision, reviewer=actor)
    if blockers:
        raise ProcedureCommandError('; '.join(item['code'] for item in blockers))
    snapshot = _reviewed_snapshot(revision)
    encoded = json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
    content_hash = hashlib.sha256(encoded).hexdigest()
    revision.content_hash = content_hash
    revision.status = ProcedureRevisionStatus.IN_REVIEW
    revision.reviewed_by = actor
    revision.save(update_fields=['content_hash', 'status', 'reviewed_by'])
    run_id = f'procedure-review-{revision.pk}'
    tool_call_id = f'{revision.content_version}-{content_hash}'
    payload = {
        'procedure_id': revision.procedure_id,
        'revision_id': revision.pk,
        'revision_number': revision.revision,
        'content_version': revision.content_version,
        'content_hash': content_hash,
        'scope': {'customer_id': scope.customer_id},
        'reviewed_snapshot': snapshot,
        'requested_by_id': actor.pk,
    }
    approval, _ = Approval.objects.get_or_create(
        idempotency_key=compute_idempotency_key(run_id, tool_call_id),
        defaults={
            'action_type': ActionType.PROCEDURE_PUBLISH,
            'summary': f'Publish {revision.procedure.code} revision {revision.revision}',
            'payload': payload,
            'agent_run_id': run_id,
            'agent_checkpoint_id': str(uuid.uuid4()),
            'tool_call_id': tool_call_id,
            'baseline_context': snapshot,
        },
    )
    return approval


def _expected_scope(procedure: Procedure, payload_scope: dict) -> MaintenanceScope:
    """Validate and return the exact procedure scope from an approval payload."""
    if not isinstance(payload_scope, dict):
        raise ProcedurePublishError('Procedure publication scope is missing')

    customer_id = payload_scope.get('customer_id')
    site_key = payload_scope.get('site_key')
    if procedure.customer_id is None:
        raise ProcedurePublishError('Procedure customer scope is unresolved')
    if customer_id != procedure.customer_id or site_key is not None:
        raise ProcedurePublishError('Approval scope does not match procedure scope')
    return MaintenanceScope(customer_id=customer_id, site_key=None)


def validate_publish_preconditions(
    *,
    procedure: Procedure,
    revision: ProcedureRevision,
    procedure_id: int,
    revision_number: int,
    content_hash: str,
    content_version: int,
    actor,
    scope: dict,
) -> None:
    """Validate the immutable approval snapshot against live rows and actor scope."""
    try:
        require_permission(actor, PUBLISH_PROCEDURE)
    except PermissionDenied as exc:
        raise ProcedurePublishError(str(exc)) from exc

    if revision.procedure_id != procedure.pk or procedure.pk != procedure_id:
        raise ProcedurePublishError(
            'Revision does not belong to the approved procedure'
        )
    if revision.revision != revision_number:
        raise ProcedurePublishError('Revision number changed since approval')
    if revision.content_hash != content_hash:
        raise ProcedurePublishError('Content hash changed since approval')
    if revision.content_version != content_version:
        raise ProcedurePublishError('Content version changed since approval')

    expected_scope = _expected_scope(procedure, scope)
    try:
        if expected_scope not in scope_for_actor(actor):
            raise ProcedurePublishError(
                'Actor and procedure maintenance scopes do not match'
            )
    except ScopeError as exc:
        raise ProcedurePublishError(str(exc)) from exc


@transaction.atomic
def publish_revision(
    *,
    procedure_id: int,
    revision_id: int,
    revision_number: int,
    content_hash: str,
    content_version: int,
    actor,
    scope: dict,
) -> str:
    """Publish one reviewed revision atomically and idempotently.

    The procedure family is locked before its selected revision so all callers of
    this service acquire locks in a stable order.
    """
    try:
        procedure = Procedure.objects.select_for_update().get(pk=procedure_id)
        revision = ProcedureRevision.objects.select_for_update().get(pk=revision_id)
    except ObjectDoesNotExist as exc:
        raise ProcedurePublishError(
            'Approved procedure or revision does not exist'
        ) from exc

    validate_publish_preconditions(
        procedure=procedure,
        revision=revision,
        procedure_id=procedure_id,
        revision_number=revision_number,
        content_hash=content_hash,
        content_version=content_version,
        actor=actor,
        scope=scope,
    )

    effect_ref = f'procedure-publish:{revision_id}:{content_hash}'
    if revision.status == ProcedureRevisionStatus.PUBLISHED:
        if procedure.current_revision_id != revision.pk:
            raise ProcedurePublishError(
                'Published revision is not the current revision'
            )
        return effect_ref
    if revision.status != ProcedureRevisionStatus.IN_REVIEW:
        raise ProcedurePublishError('Revision is not in review')

    prior = (
        ProcedureRevision.objects
        .select_for_update()
        .filter(procedure=procedure, status=ProcedureRevisionStatus.PUBLISHED)
        .exclude(pk=revision.pk)
        .first()
    )
    if prior is not None:
        prior.status = ProcedureRevisionStatus.SUPERSEDED
        prior.save(update_fields=['status'])

    revision.status = ProcedureRevisionStatus.PUBLISHED
    revision.published_by = actor
    revision.published_at = timezone.now()
    revision.save(update_fields=['status', 'published_by', 'published_at'])
    procedure.current_revision = revision
    procedure.save(update_fields=['current_revision', 'updated_at'])
    return effect_ref
