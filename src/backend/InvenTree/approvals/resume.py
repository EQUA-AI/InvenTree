"""Agent runtime resume stub for the approvals system.

Implements A-3: Agent runtime resume on approve/deny/cancel.

Phase 1: Logs the resume attempt but does not actually call the agent runtime.
Phase 4: Will replace the stub with actual agent runtime integration
(e.g., Azure Functions Durable Orchestration resume with exponential backoff).

Resume contract (spec §11.1, §11.4):
- Retry policy: exponential backoff with jitter, initial 1s, max 60s, max 5 attempts
- Idempotent resume: include idempotency_key in resume payload
- On all-retries-exhausted for approve: leave as approved for reconciliation
- On runtime rejection (unknown agent_run_id): transition to failed
"""

import structlog

logger = structlog.get_logger('approvals.resume')


def attempt_agent_resume(approval, decision: str, actor_user=None):
    """Attempt to resume the paused agent workflow with the approval decision.

    Args:
        approval: The Approval instance
        decision: One of 'approved', 'denied', 'canceled', 'expired'
        actor_user: The user who made the decision (None for system actions)

    Returns:
        True if resume was dispatched (or stub), False if permanently failed.
    """
    logger.info(
        'agent_resume_attempted',
        approval_id=str(approval.pk),
        agent_run_id=approval.agent_run_id,
        agent_checkpoint_id=approval.agent_checkpoint_id,
        tool_call_id=approval.tool_call_id,
        idempotency_key=approval.idempotency_key,
        decision=decision,
        actor_user_id=getattr(actor_user, 'pk', None),
        _stub=True,
    )

    # TODO Phase 4: Replace with actual agent runtime resume call.
    #
    # resume_payload = {
    #     'tool_call_id': approval.tool_call_id,
    #     'decision': decision,
    #     'idempotency_key': approval.idempotency_key,
    #     'payload': approval.payload if decision == 'approved' else None,
    #     'reason': approval.deny_reason or approval.canceled_reason or None,
    # }
    #
    # Retry with exponential backoff (spec §11.4):
    #   initial_delay = 1s, max_delay = 60s, max_retries = 5
    #
    # import random, time
    # for attempt in range(max_retries):
    #     try:
    #         agent_runtime.resume(
    #             agent_run_id=approval.agent_run_id,
    #             checkpoint_id=approval.agent_checkpoint_id,
    #             payload=resume_payload,
    #         )
    #         logger.info('agent_resume_success', approval_id=str(approval.pk))
    #         return True
    #     except AgentRuntimeUnavailable:
    #         delay = min(initial_delay * (2 ** attempt), max_delay)
    #         delay += random.uniform(0, delay * 0.1)  # jitter
    #         time.sleep(delay)
    #     except AgentRunNotFound:
    #         # Runtime rejected — unknown agent_run_id
    #         from django.db import transaction
    #         from .models import ApprovalStatus, ApprovalEvent, EventType
    #         with transaction.atomic():
    #             approval.refresh_from_db()
    #             approval.execution_error = {
    #                 'reason': 'agent_run_not_found',
    #                 'agent_run_id': approval.agent_run_id,
    #             }
    #             approval.transition_to(
    #                 ApprovalStatus.FAILED,
    #                 event_payload={'reason': 'agent_run_not_found'},
    #                 extra_update_fields=['execution_error'],
    #             )
    #         return False
    #
    # # All retries exhausted — leave in current state for reconciliation
    # logger.warning('agent_resume_exhausted', approval_id=str(approval.pk))
    # return False

    return True  # Stub always succeeds
