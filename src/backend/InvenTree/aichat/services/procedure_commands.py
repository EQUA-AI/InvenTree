"""Review-bound procedure completion over the canonical step command."""

from decimal import Decimal, InvalidOperation

from aichat.services.proposals import ProposalError

ERROR_SPEECH = {
    'STEP_VALIDATION_ERROR': 'The step value or evidence is invalid. Review the step on screen.',
    'HOLD_POINT_BLOCKED': 'A hold point blocks this step. Resolve the required checks on screen.',
    'STEP_REQUIRED': 'This required step needs an explicit result before completion.',
    'STALE_VERSION': 'The step changed. Request a fresh completion review.',
}


def target(work_order, intent):
    """An application/step pair is meaningful only inside this scoped work order."""
    from tasks.models import WorkOrderStepExecution

    execution = WorkOrderStepExecution.objects.filter(
        application__work_order=work_order,
        application_id=intent.get('application_id'),
        step_key=intent.get('step_key'),
    ).first()
    if execution is None:
        raise ProposalError('That procedure step is unavailable for this work order.')
    return execution


def preview(work_order, intent):
    """Read the complete immutable instruction and exact proposed measurement."""
    from tasks.services.procedure_execution import _derived_result, _requires_evidence

    if set(intent) - {'application_id', 'step_key', 'value', 'passed', 'reason'}:
        raise ProposalError('Unsupported procedure completion fields.')
    execution = target(work_order, intent)
    if execution.status != 'pending':
        raise ProposalError('This step already has a result. Review it on screen.')
    value, passed = intent.get('value'), intent.get('passed')
    if passed is not None and not isinstance(passed, bool):
        raise ProposalError('The passed result must be explicitly true or false.')
    if _requires_evidence(execution.step_snapshot):
        raise ProposalError(
            'This step requires visual evidence. Complete its review on screen.'
        )
    if execution.step_snapshot.get('required') and value is None and passed is None:
        raise ProposalError(ERROR_SPEECH['STEP_REQUIRED'])
    if execution.step_snapshot.get('value_type') == 'number':
        raw = value.get('number') if isinstance(value, dict) else value
        try:
            if not Decimal(str(raw)).is_finite():
                raise InvalidOperation
        except (ValueError, InvalidOperation):
            raise ProposalError(ERROR_SPEECH['STEP_VALIDATION_ERROR']) from None
    try:
        stored_value, derived_passed = _derived_result(
            execution.step_snapshot, value, passed
        )
    except Exception:
        raise ProposalError(ERROR_SPEECH['STEP_VALIDATION_ERROR']) from None
    return {
        'application_id': execution.application_id,
        'step_key': str(execution.step_key),
        'step_execution_id': execution.pk,
        'step_version': execution.version,
        'work_order_version': work_order.lifecycle_version,
        'step_snapshot': execution.step_snapshot,
        'value': stored_value,
        'passed': derived_passed,
        'resulting_status': 'failed' if derived_passed is False else 'completed',
        'warning': 'Records only this step result. This does not close the work order or waive a safety gate.',
    }


def authorize(actor, work_order, intent):
    """Canonical permissions are required before showing an executable review."""
    from tasks.permissions import EXECUTE_WORKORDER, require_permission
    from tasks.services.procedure_execution import _require_step_permission

    require_permission(actor, EXECUTE_WORKORDER)
    execution = target(work_order, intent)
    _require_step_permission(actor, execution)
    return execution.version


def execute(proposal, actor, correlation):
    """Caller holds the proposal transaction and work-order lock."""
    from tasks.models import WorkOrderCommand
    from tasks.services.procedure_execution import (
        ProcedureExecutionError,
        complete_step,
    )

    key = f'proposal:{proposal.pk}'
    intent = proposal.intent
    try:
        result = complete_step(
            work_order_id=proposal.target_work_order_id,
            application_id=intent['application_id'],
            step_key=intent['step_key'],
            actor=actor,
            expected_version=proposal.target_version,
            idempotency_key=key,
            value=intent.get('value'),
            passed=intent.get('passed'),
            correlation_id=correlation,
        )
    except ProcedureExecutionError as exc:
        raise ProposalError(
            ERROR_SPEECH.get(
                exc.code, 'The step could not be completed. Review it on screen.'
            )
        ) from exc
    command = WorkOrderCommand.objects.get(
        work_order_id=proposal.target_work_order_id, idempotency_key=key
    )
    return {
        'command': 'complete_step',
        'work_order_id': proposal.target_work_order_id,
        'application_id': result.application_id,
        'step_key': str(result.step_key),
        'step_execution_id': result.pk,
        'step_version': result.version,
        'status': result.status,
        'value': result.value,
        'passed': result.passed,
        'idempotency_key': key,
        'event_id': int(command.result_ref),
        'correlation_id': str(command.correlation_id),
    }


def verified(proposal):
    """Verify immutable command/event evidence, not mutable current step state."""
    from tasks.models import WorkOrderCommand, WorkOrderEvent

    receipt = proposal.receipt or {}
    command = WorkOrderCommand.objects.filter(
        work_order_id=proposal.target_work_order_id,
        idempotency_key=f'proposal:{proposal.pk}',
        command='complete_step',
        status='succeeded',
    ).first()
    if (
        not command
        or receipt.get('command') != 'complete_step'
        or (
            str(receipt.get('event_id')) != command.result_ref
            or receipt.get('correlation_id') != str(command.correlation_id)
            or receipt.get('step_version') != proposal.target_version + 1
            or receipt.get('application_id') != proposal.intent.get('application_id')
            or receipt.get('step_key') != proposal.intent.get('step_key')
            or receipt.get('work_order_id') != proposal.target_work_order_id
            or receipt.get('idempotency_key') != command.idempotency_key
            or receipt.get('step_execution_id')
            != proposal.preview.get('step_execution_id')
            or receipt.get('value') != proposal.preview.get('value')
            or receipt.get('passed') != proposal.preview.get('passed')
            or receipt.get('status') != proposal.preview.get('resulting_status')
        )
    ):
        return False
    return WorkOrderEvent.objects.filter(
        pk=command.result_ref,
        actor_id=proposal.owner_id,
        work_order_id=proposal.target_work_order_id,
        idempotency_key=command.idempotency_key,
        correlation_id=command.correlation_id,
        event_type='STEP_FAILED'
        if receipt.get('status') == 'failed'
        else 'STEP_COMPLETED',
        metadata__step_execution_id=receipt.get('step_execution_id'),
        metadata__version=receipt.get('step_version'),
    ).exists()
