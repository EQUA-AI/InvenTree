"""Transactional services for applying and executing governed procedures."""

import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase

from django.db import models, transaction
from django.utils import timezone

from common.models import Attachment
from tasks.models import (
    Procedure,
    ProcedureRevision,
    ProcedureRevisionStatus,
    ProcedureStepType,
    StepExecutionStatus,
    WorkOrderCommand,
    WorkOrderDeviation,
    WorkOrderEvent,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from tasks.permissions import APPLY_PROCEDURE, EXECUTE_WORKORDER, require_permission
from tasks.services.procedures import _reviewed_snapshot
from tasks.services.work_orders import (
    IdempotencyConflict,
    StaleVersion,
    WorkOrderCommandError,
    _canonical_hash,
    _locked_work_order,
    _require_scope,
    _require_version,
)


class ProcedureExecutionError(WorkOrderCommandError):
    """Base exception for rejected procedure execution commands."""


class ProcedureNotPublished(ProcedureExecutionError):  # noqa: N818 - established command error name
    """The selected revision is not eligible for application."""

    code = 'PROCEDURE_NOT_PUBLISHED'


class ProcedureApplicationConflict(ProcedureExecutionError):  # noqa: N818 - established command error name
    """The work order already has a different primary application."""

    code = 'PROCEDURE_APPLICATION_CONFLICT'


class StepValidationError(ProcedureExecutionError):
    """A step value or disposition is invalid."""

    code = 'STEP_VALIDATION_ERROR'


class HoldPointBlocked(ProcedureExecutionError):  # noqa: N818 - established command error name
    """A hard hold point prevents the requested execution."""

    code = 'HOLD_POINT_BLOCKED'


class RequiredStepError(ProcedureExecutionError):
    """A required step was silently skipped."""

    code = 'STEP_REQUIRED'


def _application_snapshot(revision):
    """Return the versioned, immutable application snapshot."""
    snapshot = _reviewed_snapshot(revision)
    snapshot['snapshot_schema_version'] = 1
    snapshot['policy_version'] = 1
    snapshot['revision_id'] = revision.pk
    snapshot['revision']['content_hash'] = revision.content_hash
    snapshot['scope'] = {
        'customer_id': revision.procedure.customer_id,
        'site_key': None,
    }
    snapshot['applicability'] = [
        {
            'id': rule.pk,
            'machine_id': rule.machine_id,
            'manufacturer': rule.manufacturer,
            'model': rule.model,
            'location_pattern': rule.location_pattern,
            'required_tags': rule.required_tags,
            'predicate': rule.predicate,
        }
        for rule in revision.applicability_rules.order_by('pk')
    ]
    return snapshot


def _snapshot_hash(snapshot):
    """Hash the exact compact canonical JSON persisted on the application."""
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()


def _applicability_matches(revision, work_order):
    """Evaluate the currently supported deterministic applicability fields."""
    rules = list(revision.applicability_rules.all())
    if not rules:
        return True
    machine = work_order.machine
    tags = set(work_order.tags or [])
    for rule in rules:
        if rule.predicate:
            continue  # Unknown predicate schemas fail closed.
        if rule.machine_id and rule.machine_id != work_order.machine_id:
            continue
        if rule.manufacturer and (
            machine is None or machine.manufacturer != rule.manufacturer
        ):
            continue
        if rule.model and (machine is None or machine.model != rule.model):
            continue
        if rule.location_pattern and (
            machine is None or not fnmatchcase(machine.location, rule.location_pattern)
        ):
            continue
        if not set(rule.required_tags or []).issubset(tags):
            continue
        return True
    return False


def _command_replay(work_order, command, idempotency_key, request_hash):
    prior = WorkOrderCommand.objects.filter(
        work_order=work_order, idempotency_key=idempotency_key
    ).first()
    if prior is None:
        return None
    if prior.command != command or prior.request_hash != request_hash:
        raise IdempotencyConflict('Idempotency key was reused with a different request')
    try:
        event = WorkOrderEvent.objects.get(
            pk=int(prior.result_ref), work_order=work_order
        )
    except (TypeError, ValueError, WorkOrderEvent.DoesNotExist) as exc:
        raise ProcedureExecutionError(
            'Stored command result cannot be replayed'
        ) from exc
    return event


def _record_command(
    *,
    work_order,
    actor,
    command,
    event_type,
    idempotency_key,
    request_hash,
    metadata,
    reason='',
    correlation_id=None,
    note='',
):
    correlation_id = correlation_id or uuid.uuid4()
    if note:
        metadata = {**metadata, 'note': note}
    event = WorkOrderEvent.objects.create(
        work_order=work_order,
        event_type=event_type,
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        actor=actor,
        reason=reason,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        metadata=metadata,
    )
    WorkOrderCommand.objects.create(
        work_order=work_order,
        command=command,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        request_hash=request_hash,
        status='succeeded',
        result_ref=str(event.pk),
    )
    return event


def _step_from_event(event):
    return WorkOrderStepExecution.objects.get(pk=event.metadata['step_execution_id'])


def _is_hard_hold(step_snapshot):
    if step_snapshot.get('step_type') != ProcedureStepType.HOLD_POINT:
        return False
    policy = step_snapshot.get('evidence_policy') or {}
    return policy.get('release_policy', 'hard') == 'hard' or policy.get('hard') is True


def _requires_evidence(step_snapshot):
    policy = step_snapshot.get('evidence_policy') or {}
    return bool(policy.get('required') or policy.get('require_evidence'))


def _validate_evidence(work_order, execution, evidence_ids):
    ids = list(dict.fromkeys(evidence_ids or []))
    if not ids:
        return ids
    ownership = models.Q(model_type='kanbancard', model_id=work_order.pk) | models.Q(
        model_type='workorderstepexecution', model_id=execution.pk
    )
    packet = getattr(work_order, 'repair_packet', None)
    if packet is not None:
        ownership |= models.Q(model_type='repairpacket', model_id=packet.pk)
    valid = Attachment.objects.filter(pk__in=ids).filter(ownership)
    if valid.count() != len(ids):
        raise StepValidationError('Evidence does not belong to this work order step')
    return ids


def _validate_hold_points(execution):
    prior = execution.application.step_executions.filter(
        sequence__lt=execution.sequence
    ).order_by('sequence')
    for candidate in prior:
        if (
            _is_hard_hold(candidate.step_snapshot)
            and candidate.status != StepExecutionStatus.COMPLETED
        ):
            raise HoldPointBlocked('A prior hard hold point has not been released')
    if (
        _is_hard_hold(execution.step_snapshot)
        and prior.filter(status=StepExecutionStatus.FAILED).exists()
    ):
        raise HoldPointBlocked('A failed prior step blocks this hard hold point')


def _require_step_permission(actor, execution):
    permission = execution.step_snapshot.get('required_permission')
    if permission:
        require_permission(actor, permission)


def _derived_result(snapshot, value, asserted_passed):
    value_type = snapshot.get('value_type', 'none')
    if value_type == 'number':
        raw = value.get('number') if isinstance(value, dict) else value
        try:
            number = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise StepValidationError('A valid numeric value is required') from exc
        minimum = snapshot.get('min_value')
        maximum = snapshot.get('max_value')
        passed = (minimum is None or number >= Decimal(minimum)) and (
            maximum is None or number <= Decimal(maximum)
        )
        return value, passed
    if value_type == 'boolean':
        raw = value.get('boolean') if isinstance(value, dict) else value
        if not isinstance(raw, bool):
            raise StepValidationError('A boolean pass/fail value is required')
        return value, raw
    if value_type == 'choice':
        raw = value.get('choice') if isinstance(value, dict) else value
        if raw not in snapshot.get('allowed_values', []):
            raise StepValidationError('Value is not one of the allowed choices')
        return value, asserted_passed
    if value_type == 'none' and value not in (None, {}, ''):
        raise StepValidationError('This step does not accept a structured value')
    return value if value is not None else {}, asserted_passed


@transaction.atomic
def apply_procedure_revision(
    *,
    work_order_id,
    revision_id,
    actor,
    expected_version,
    idempotency_key,
    correlation_id=None,
):
    """Apply one exact immutable procedure revision to a work order."""
    work_order = _locked_work_order(work_order_id)
    payload = {
        'work_order_id': work_order_id,
        'revision_id': revision_id,
        'expected_version': expected_version,
    }
    request_hash = _canonical_hash('apply_procedure', actor, payload)
    replay = _command_replay(
        work_order, 'apply_procedure', idempotency_key, request_hash
    )
    if replay:
        return WorkOrderProcedureApplication.objects.get(
            pk=replay.metadata['application_id']
        )
    _require_version(work_order, expected_version)
    require_permission(actor, APPLY_PROCEDURE)
    _require_scope(actor, work_order)
    revision_pointer = ProcedureRevision.objects.only('procedure_id').get(
        pk=revision_id
    )
    Procedure.objects.select_for_update().get(pk=revision_pointer.procedure_id)
    revision = (
        ProcedureRevision.objects
        .select_for_update()
        .select_related('procedure')
        .get(pk=revision_id)
    )
    if revision.procedure.customer_id != work_order.customer_id:
        raise ProcedureExecutionError('Procedure and work-order scopes do not match')
    if (
        revision.status != ProcedureRevisionStatus.PUBLISHED
        and revision.procedure.current_revision_id != revision.pk
    ):
        raise ProcedureNotPublished('Procedure revision is not published or current')
    if revision.work_order_type != work_order.work_order_type:
        raise ProcedureExecutionError('Procedure does not match the work-order type')
    if not _applicability_matches(revision, work_order):
        raise ProcedureExecutionError('Procedure is not applicable to this work order')
    if work_order.procedure_applications.filter(primary=True).exists():
        raise ProcedureApplicationConflict('Work order already has a primary procedure')

    snapshot = _application_snapshot(revision)
    snapshot_hash = _snapshot_hash(snapshot)
    application = WorkOrderProcedureApplication.objects.create(
        work_order=work_order,
        revision=revision,
        snapshot=snapshot,
        snapshot_hash=snapshot_hash,
        applied_by=actor,
        idempotency_key=idempotency_key,
        primary=True,
    )
    WorkOrderStepExecution.objects.bulk_create([
        WorkOrderStepExecution(
            application=application,
            step_key=step['key'],
            sequence=step['sequence'],
            step_snapshot=step,
            status=StepExecutionStatus.PENDING,
        )
        for step in snapshot['steps']
    ])
    _record_command(
        work_order=work_order,
        actor=actor,
        command='apply_procedure',
        event_type='PROCEDURE_APPLIED',
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        correlation_id=correlation_id,
        metadata={
            'application_id': application.pk,
            'revision_id': revision.pk,
            'snapshot_hash': snapshot_hash,
        },
    )
    return application


def _locked_execution(work_order, application_id, step_key):
    try:
        return (
            WorkOrderStepExecution.objects
            .select_for_update()
            .select_related('application')
            .get(
                application_id=application_id,
                step_key=step_key,
                application__work_order=work_order,
            )
        )
    except WorkOrderStepExecution.DoesNotExist as exc:
        raise StepValidationError('Step does not belong to this work order') from exc


@transaction.atomic
def complete_step(
    *,
    work_order_id,
    application_id,
    step_key,
    actor,
    expected_version,
    idempotency_key,
    value=None,
    passed=None,
    note='',
    evidence_ids=None,
    correlation_id=None,
):
    """Validate and record a completed or failed procedure step."""
    work_order = _locked_work_order(work_order_id)
    payload = {
        'application_id': application_id,
        'step_key': str(step_key),
        'expected_version': expected_version,
        'value': value,
        'passed': passed,
        'note': note,
        'evidence_ids': evidence_ids or [],
    }
    request_hash = _canonical_hash('complete_step', actor, payload)
    replay = _command_replay(work_order, 'complete_step', idempotency_key, request_hash)
    if replay:
        return _step_from_event(replay)
    require_permission(actor, EXECUTE_WORKORDER)
    _require_scope(actor, work_order)
    if not work_order.is_active:
        raise StepValidationError('Work order is not active')
    execution = _locked_execution(work_order, application_id, step_key)
    _require_step_permission(actor, execution)
    if execution.version != expected_version:
        raise StaleVersion(
            f'Expected version {expected_version}, current version {execution.version}'
        )
    _validate_hold_points(execution)
    evidence_ids = _validate_evidence(work_order, execution, evidence_ids)
    if _requires_evidence(execution.step_snapshot) and not evidence_ids:
        raise StepValidationError('Evidence is required for this step')
    if execution.step_snapshot.get('required') and value is None and passed is None:
        raise RequiredStepError('A required step cannot be silently skipped')
    stored_value, derived_passed = _derived_result(
        execution.step_snapshot, value, passed
    )
    execution.value = stored_value
    execution.passed = derived_passed
    execution.status = (
        StepExecutionStatus.FAILED
        if derived_passed is False
        else StepExecutionStatus.COMPLETED
    )
    execution.note = note
    execution.completed_by = actor
    execution.completed_at = timezone.now()
    execution.version += 1
    execution.save(
        update_fields=[
            'value',
            'passed',
            'status',
            'note',
            'completed_by',
            'completed_at',
            'version',
        ]
    )
    _record_command(
        work_order=work_order,
        actor=actor,
        command='complete_step',
        event_type=(
            'STEP_COMPLETED'
            if execution.status == StepExecutionStatus.COMPLETED
            else 'STEP_FAILED'
        ),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        correlation_id=correlation_id,
        metadata={
            'application_id': application_id,
            'step_execution_id': execution.pk,
            'step_key': str(step_key),
            'version': execution.version,
            'evidence_ids': evidence_ids,
        },
        note=note,
    )
    return execution


@transaction.atomic
def mark_step_not_applicable(
    *,
    work_order_id,
    application_id,
    step_key,
    actor,
    expected_version,
    idempotency_key,
    reason,
    evidence_ids=None,
    correlation_id=None,
):
    """Record an explicit not-applicable deviation without changing live safety."""
    if not reason or not reason.strip():
        raise RequiredStepError('A not-applicable disposition reason is required')
    work_order = _locked_work_order(work_order_id)
    payload = {
        'application_id': application_id,
        'step_key': str(step_key),
        'expected_version': expected_version,
        'reason': reason,
        'evidence_ids': evidence_ids or [],
    }
    request_hash = _canonical_hash('step_not_applicable', actor, payload)
    replay = _command_replay(
        work_order, 'step_not_applicable', idempotency_key, request_hash
    )
    if replay:
        return _step_from_event(replay)
    require_permission(actor, EXECUTE_WORKORDER)
    _require_scope(actor, work_order)
    if not work_order.is_active:
        raise StepValidationError('Work order is not active')
    execution = _locked_execution(work_order, application_id, step_key)
    _require_step_permission(actor, execution)
    if execution.version != expected_version:
        raise StaleVersion(
            f'Expected version {expected_version}, current version {execution.version}'
        )
    evidence_ids = _validate_evidence(work_order, execution, evidence_ids)
    if _requires_evidence(execution.step_snapshot) and not evidence_ids:
        raise StepValidationError('Evidence is required for this disposition')
    execution.status = StepExecutionStatus.NOT_APPLICABLE
    execution.disposition_reason = reason.strip()
    execution.completed_by = actor
    execution.completed_at = timezone.now()
    execution.version += 1
    execution.save(
        update_fields=[
            'status',
            'disposition_reason',
            'completed_by',
            'completed_at',
            'version',
        ]
    )
    application = execution.application
    deviation = WorkOrderDeviation.objects.create(
        work_order=work_order,
        category='step_not_applicable',
        application_key=str(application.pk),
        step_key=str(step_key),
        expected=execution.step_snapshot,
        actual={
            'status': StepExecutionStatus.NOT_APPLICABLE,
            'evidence_ids': evidence_ids,
        },
        reason=reason.strip(),
        actor=actor,
    )
    _record_command(
        work_order=work_order,
        actor=actor,
        command='step_not_applicable',
        event_type='STEP_NOT_APPLICABLE',
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        correlation_id=correlation_id,
        reason=reason,
        metadata={
            'application_id': application_id,
            'step_execution_id': execution.pk,
            'step_key': str(step_key),
            'deviation_id': deviation.pk,
            'version': execution.version,
            'evidence_ids': evidence_ids,
        },
    )
    return execution


@transaction.atomic
def reopen_step(
    *,
    work_order_id,
    application_id,
    step_key,
    actor,
    expected_version,
    idempotency_key,
    reason='',
    correlation_id=None,
):
    """Reopen a recorded step for authorized correction or rework."""
    work_order = _locked_work_order(work_order_id)
    payload = {
        'application_id': application_id,
        'step_key': str(step_key),
        'expected_version': expected_version,
        'reason': reason,
    }
    request_hash = _canonical_hash('reopen_step', actor, payload)
    replay = _command_replay(work_order, 'reopen_step', idempotency_key, request_hash)
    if replay:
        return _step_from_event(replay)
    require_permission(actor, EXECUTE_WORKORDER)
    _require_scope(actor, work_order)
    if not work_order.is_active:
        raise StepValidationError('Work order is not active')
    execution = _locked_execution(work_order, application_id, step_key)
    _require_step_permission(actor, execution)
    if execution.version != expected_version:
        raise StaleVersion(
            f'Expected version {expected_version}, current version {execution.version}'
        )
    if execution.status in {
        StepExecutionStatus.PENDING,
        StepExecutionStatus.IN_PROGRESS,
    }:
        raise StepValidationError('Only a recorded terminal step can be reopened')
    prior_status = execution.status
    execution.status = StepExecutionStatus.PENDING
    execution.value = {}
    execution.passed = None
    execution.note = ''
    execution.completed_by = None
    execution.completed_at = None
    execution.disposition_reason = ''
    execution.version += 1
    execution.save(
        update_fields=[
            'status',
            'value',
            'passed',
            'note',
            'completed_by',
            'completed_at',
            'disposition_reason',
            'version',
        ]
    )
    _record_command(
        work_order=work_order,
        actor=actor,
        command='reopen_step',
        event_type='STEP_REOPENED',
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        correlation_id=correlation_id,
        reason=reason,
        metadata={
            'application_id': application_id,
            'step_execution_id': execution.pk,
            'step_key': str(step_key),
            'prior_status': prior_status,
            'version': execution.version,
        },
    )
    return execution
