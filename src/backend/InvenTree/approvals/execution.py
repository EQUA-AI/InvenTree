"""Durable approval execution: persist before dispatch, never retry uncertainty."""

from django.db import transaction

from .executors import EffectResult, compute_effect_idempotency_key, registry
from .models import Approval, ApprovalExecution, ApprovalStatus, ExecutedEffect
from .sanitizers import redact_error


def preflight(executor, approval):
    """Return a proven pre-dispatch failure or permission to perform live checks."""
    if executor is None:
        return EffectResult(
            False,
            error_message='No executor registered for required action',
            outcome='failed_before_effect',
        )
    if not isinstance(approval.payload, dict):
        return EffectResult(
            False,
            error_message='Request payload must be an object.',
            outcome='failed_before_effect',
        )
    if approval.action_type == 'email':
        from ai.core.integrations.email.policy import BLOCKED_ERROR, check_recipients

        decision = check_recipients(
            approval.payload.get('to'),
            approval.payload.get('cc'),
            approval.payload.get('bcc'),
        )
        if not decision.allowed:
            return EffectResult(
                False,
                error_message=BLOCKED_ERROR,
                outcome='failed_before_effect',
                result_payload={'blocked_by_policy': True},
            )
    if not getattr(executor, 'implemented', False):
        return EffectResult(
            False,
            error_message='Canonical business executor is unavailable; no effect dispatched.',
            outcome='failed_before_effect',
        )
    try:
        errors = executor.validate(approval.payload)
    except Exception:
        errors = ['Request validation could not be completed.']
    if errors:
        return EffectResult(
            False, error_message='; '.join(errors), outcome='failed_before_effect'
        )
    return None


def classify(result):
    """Success requires a real receipt; unspecified post-dispatch failure is unknown."""
    if not isinstance(result, EffectResult):
        return 'unknown'
    if result.success:
        if (
            not result.effect_ref
            or str(result.effect_ref).startswith('stub-')
            or not isinstance(result.result_payload or {}, dict)
            or (result.result_payload or {}).get('stub')
        ):
            return 'unknown'
        return 'succeeded'
    return (
        result.outcome
        if result.outcome in ('failed_before_effect', 'unknown', 'partial')
        else 'unknown'
    )


def _result_from_record(record):
    return EffectResult(
        success=record.state == 'succeeded',
        effect_ref=record.result.get('effect_ref'),
        result_payload=record.result.get('payload') or {},
        error_message=record.detail,
        outcome=record.state,
    )


@transaction.atomic
def record_result(approval_id, execution_id, result):
    """Record a verified result without downgrading a committed success receipt."""
    approval = Approval.objects.select_for_update().get(pk=approval_id)
    execution = ApprovalExecution.objects.select_for_update().get(
        pk=execution_id, approval=approval
    )
    if execution.state == 'succeeded':
        result = _result_from_record(execution)
    state = classify(result)
    if execution.state == 'partial' and state in ('unknown', 'failed_before_effect'):
        state = 'partial'
    payload = (
        result.result_payload
        if isinstance(getattr(result, 'result_payload', None), dict)
        else {}
    )
    if execution.state == 'partial' and state == 'partial':
        # A later lookup which proves nothing must not erase previously known
        # effects. Only an authoritative success may replace this evidence.
        payload = {**(execution.result.get('payload') or {}), **payload}
    error = redact_error(
        getattr(result, 'error_message', None) or 'Outcome unverified; do not retry.'
    )
    execution.state = state
    execution.detail = (
        ''
        if state == 'succeeded'
        else error.get('error', 'Outcome unverified; do not retry.')
    )
    execution.result = {
        'effect_ref': result.effect_ref if state == 'succeeded' else None,
        'payload': payload,
    }
    execution.save(update_fields=['state', 'detail', 'result', 'updated_at'])
    approval.execution_result = {
        **payload,
        'execution_state': state,
        'operation_id': execution.pk,
        'effect_ref': execution.result['effect_ref'],
    }
    if state == 'succeeded':
        ExecutedEffect.objects.get_or_create(
            idempotency_key=compute_effect_idempotency_key(
                approval.idempotency_key, approval.action_type
            ),
            defaults={
                'approval': approval,
                'effect_type': approval.action_type,
                'effect_ref': result.effect_ref,
            },
        )
        if approval.status == ApprovalStatus.EXECUTING:
            approval.transition_to(
                ApprovalStatus.SUCCEEDED,
                actor_user=execution.actor,
                extra_update_fields=['execution_result'],
            )
    elif state == 'failed_before_effect':
        approval.execution_error = error
        if approval.status in (ApprovalStatus.APPROVED, ApprovalStatus.EXECUTING):
            approval.transition_to(
                ApprovalStatus.FAILED,
                actor_user=execution.actor,
                extra_update_fields=['execution_result', 'execution_error'],
            )
    else:
        # Do not transition to FAILED: the provider may have committed the effect.
        approval.execution_error = error
        approval.save(
            update_fields=['execution_result', 'execution_error', 'updated_at']
        )
    return approval


class _RollbackEffectError(Exception):
    def __init__(self, result):
        self.result = result


def dispatch(executor, approval, *, actor, execution):
    """Execute once; pure database effects commit their receipt in the same txn."""
    if getattr(executor, 'atomic_execution', False):
        try:
            with transaction.atomic():
                # Match every other approval path's lock order: request, then
                # operation. Opposite orders can deadlock against reconciliation.
                Approval.objects.select_for_update().get(pk=approval.pk)
                current = ApprovalExecution.objects.select_for_update().get(
                    pk=execution.pk
                )
                if current.state != 'submitting':
                    return record_result(
                        approval.pk, current.pk, _result_from_record(current)
                    )
                result = executor.execute_for_approval(
                    approval, actor=actor, execution=current
                )
                if classify(result) != 'succeeded':
                    raise _RollbackEffectError(result)
                return record_result(approval.pk, current.pk, result)
        except _RollbackEffectError as exc:
            # This executor explicitly promises database-only effects. The outer
            # transaction rolled back every business write, including partial ones.
            result = EffectResult(
                False,
                error_message=getattr(
                    exc.result, 'error_message', 'Database effect rolled back.'
                ),
                result_payload={'rolled_back': True},
                outcome='failed_before_effect',
            )
        except Exception as exc:
            current = ApprovalExecution.objects.get(pk=execution.pk)
            if current.state == 'succeeded':
                # A failing on_commit hook cannot undo a committed business receipt.
                result = _result_from_record(current)
            else:
                result = EffectResult(
                    False, error_message=str(exc), outcome='failed_before_effect'
                )
    else:
        try:
            result = executor.execute_for_approval(
                approval, actor=actor, execution=execution
            )
        except Exception:
            result = EffectResult(
                False,
                error_message='The provider outcome is unverified; do not retry.',
                outcome='unknown',
            )
    return record_result(approval.pk, execution.pk, result)


def reconcile_execution(approval_id):
    """Read authoritative state only. This function must never dispatch a retry."""
    approval = Approval.objects.get(pk=approval_id)
    execution = approval.executions.order_by('-created_at').first()
    if not execution:
        return approval  # Historical stub ledgers are not rewritten.
    if hasattr(approval, 'email_draft'):
        from aichat.services.email.dispatch import reconcile

        return reconcile(approval, execution)
    if execution.state in ('succeeded', 'failed_before_effect'):
        return record_result(approval.pk, execution.pk, _result_from_record(execution))
    try:
        result = registry.get(approval.action_type).reconcile(approval, execution)
    except Exception:
        result = EffectResult(
            False,
            error_message='Authoritative reconciliation is unavailable; do not retry.',
            outcome='unknown',
        )
    return record_result(approval.pk, execution.pk, result)
