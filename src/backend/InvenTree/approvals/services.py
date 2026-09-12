"""Shared approval actions for REST, voice and internal callers.

Services own transactions and business transitions. Transport adapters own
HTTP requests; typed service errors preserve the existing REST error contract.
"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

import structlog
from rest_framework import status

from . import serializers as approval_serializers
from .models import (
    Approval,
    ApprovalEvent,
    ApprovalExecution,
    ApprovalRevision,
    ApprovalStatus,
    EventType,
)
from .policy import ApprovalPolicyError, require_policy
from .resume import attempt_agent_resume
from .review_evidence import (
    ReviewEvidenceError,
    acknowledge,
    invalidate,
    require_acknowledgment,
    revision_binding_enabled,
)
from .review_sections import compute_review_hash

logger = structlog.get_logger('approvals.services')


class ApprovalServiceError(Exception):
    """Business rejection, without an HTTP request or response dependency."""

    def __init__(self, code, detail, http_status, **extra):
        """Capture the existing public error contract without response objects."""
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status
        self.extra = extra


class ApprovalNotFoundError(ApprovalServiceError):
    """Absent or inaccessible approval."""


class ApprovalConflictError(ApprovalServiceError):
    """State, revision or lock conflict."""


class ApprovalForbiddenError(ApprovalServiceError):
    """Permission or review gate refusal."""


@dataclass(frozen=True)
class ApprovalServiceResult:
    """Transport-neutral payload and status of the existing approval contract."""

    data: object
    status: int = 200


def _reject(error_code, detail, http_status, **extra):
    error = {
        404: ApprovalNotFoundError,
        403: ApprovalForbiddenError,
        409: ApprovalConflictError,
        423: ApprovalConflictError,
    }.get(http_status, ApprovalServiceError)
    raise error(error_code, detail, http_status, **extra)


def _require_reviewer(actor):
    actor = get_user_model().objects.filter(pk=getattr(actor, 'pk', None)).first()
    if (
        not actor
        or not actor.is_active
        or not (actor.is_superuser or actor.has_perm('approvals.review'))
    ):
        _reject('forbidden', 'Approval review permission required', 403)
    return actor


def _approve_gates(approval, actor, channel):
    try:
        require_policy(approval, actor=actor, channel=channel)
        require_acknowledgment(approval, actor=actor, channel=channel)
    except (ApprovalPolicyError, ReviewEvidenceError) as exc:
        _reject('forbidden', str(exc), 403)


def _get_approval_or_404(pk, *, actor):
    from aichat.services.email.access import visible_approvals

    return visible_approvals(Approval.objects.filter(pk=pk), actor).first()


def _get_approval_for_update(pk, *, actor):
    from aichat.services.email.access import visible_approvals

    allowed = visible_approvals(Approval.objects.filter(pk=pk), actor).values('pk')
    return Approval.objects.select_for_update().filter(pk__in=allowed).first()


@transaction.atomic
def open_approval(approval_id, *, actor, data=None, channel='screen', evidence=None):
    """Run open approval against the authoritative approval state."""
    actor = _require_reviewer(actor)
    approval = _get_approval_for_update(approval_id, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {approval_id} not found', status.HTTP_404_NOT_FOUND
        )

    if not approval.can_transition_to(ApprovalStatus.IN_REVIEW):
        return _reject(
            'conflict',
            f'Cannot transition from {approval.status} to in_review',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    approval.transition_to(ApprovalStatus.IN_REVIEW, actor_user=actor)

    return ApprovalServiceResult(
        approval_serializers.ApprovalDetailSerializer(approval).data,
        status=status.HTTP_200_OK,
    )


@transaction.atomic
def confirm_viewed(approval_id, *, actor, data=None, channel='screen', evidence=None):
    """Run confirm viewed against the authoritative approval state."""
    actor = _require_reviewer(actor)
    approval = _get_approval_for_update(approval_id, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {approval_id} not found', status.HTTP_404_NOT_FOUND
        )

    if approval.is_terminal:
        return _reject(
            'conflict',
            'Approval is in a terminal state',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    # Must be in_review or changes_requested to confirm viewed
    allowed_statuses = {ApprovalStatus.IN_REVIEW, ApprovalStatus.CHANGES_REQUESTED}
    if approval.status not in allowed_statuses:
        return _reject(
            'conflict',
            f'Cannot confirm-viewed when status is {approval.status}. '
            'Must be in_review or changes_requested.',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    if (
        channel == 'voice'
        or revision_binding_enabled()
        or hasattr(approval, 'email_draft')
    ):
        try:
            acknowledge(
                approval,
                actor=actor,
                channel=channel,
                evidence=evidence if evidence is not None else (data or {}),
            )
        except ReviewEvidenceError as exc:
            _reject('review_evidence_invalid', str(exc), 409)
    now = timezone.now()
    approval.viewed_confirmed_at = now
    approval.viewed_confirmed_by_user = actor
    approval.save(
        update_fields=['viewed_confirmed_at', 'viewed_confirmed_by_user', 'updated_at']
    )

    ApprovalEvent.objects.create(
        approval=approval,
        event_type=EventType.VIEWED_CONFIRMED,
        actor_user=actor,
        event_payload={'confirmed_at': now.isoformat()},
    )

    return ApprovalServiceResult(
        approval_serializers.ApprovalDetailSerializer(approval).data,
        status=status.HTTP_200_OK,
    )


@transaction.atomic
def request_changes(approval_id, *, actor, data=None, channel='screen', evidence=None):
    """Run request changes against the authoritative approval state."""
    actor = _require_reviewer(actor)
    serializer = approval_serializers.RequestChangesSerializer(data=(data or {}))
    serializer.is_valid(raise_exception=True)

    approval = _get_approval_for_update(approval_id, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {approval_id} not found', status.HTTP_404_NOT_FOUND
        )

    if not approval.can_transition_to(ApprovalStatus.CHANGES_REQUESTED):
        return _reject(
            'conflict',
            f'Cannot transition from {approval.status} to changes_requested',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    approval.transition_to(
        ApprovalStatus.CHANGES_REQUESTED,
        actor_user=actor,
        event_payload={'instructions': serializer.validated_data['instructions']},
    )

    return ApprovalServiceResult(
        approval_serializers.ApprovalDetailSerializer(approval).data,
        status=status.HTTP_200_OK,
    )


def approve(approval_id, *, actor, data=None, channel='screen', evidence=None):
    """Run approve against the authoritative approval state."""
    actor = _require_reviewer(actor)
    pk = approval_id

    # ── Phase 1: Read-only checks (no lock, no transaction) ──
    approval = _get_approval_or_404(pk, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {pk} not found', status.HTTP_404_NOT_FOUND
        )

    # Idempotent: if already approved/executing/succeeded, return current
    if approval.status in (
        ApprovalStatus.APPROVED,
        ApprovalStatus.EXECUTING,
        ApprovalStatus.SUCCEEDED,
    ):
        data = approval_serializers.ApprovalDetailSerializer(approval).data
        data['was_already_terminal'] = True  # A-6
        return ApprovalServiceResult(data, status=status.HTTP_200_OK)

    if approval.is_terminal:
        data = approval_serializers.ApprovalDetailSerializer(approval).data
        data['was_already_terminal'] = True  # A-6
        return ApprovalServiceResult(data, status=status.HTTP_200_OK)

    try:
        approval.check_lock_allows_action(actor, 'approve')
    except ValueError as e:
        return _reject(
            'locked',
            str(e),
            status.HTTP_423_LOCKED,
            holder_user_id=approval.lock_holder_id,
            expires_at=(
                approval.modification_lock_expires_at.isoformat()
                if approval.modification_lock_expires_at
                else None
            ),
        )

    _approve_gates(approval, actor, channel)
    expected_hash = compute_review_hash(approval)

    if not approval.can_transition_to(ApprovalStatus.APPROVED):
        return _reject(
            'conflict',
            f'Cannot transition from {approval.status} to approved',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    # ── Phase 2: Revalidation (no lock, may do network I/O) ──
    from .execution import dispatch, preflight, record_result
    from .executors import registry as executor_registry

    executor = (
        executor_registry.get(approval.action_type)
        if executor_registry.has(approval.action_type)
        else None
    )
    preflight_failure = preflight(executor, approval)
    if executor is not None and preflight_failure is None:
        try:
            drift_report = executor.check_preconditions(
                approval.payload, approval.baseline_context
            )
            revalidation_failed = bool(drift_report and drift_report.has_drift)
        except Exception:
            logger.warning('revalidation_error', approval_id=str(pk), exc_info=True)
            revalidation_failed = True
            drift_report = 'Preconditions could not be verified; no effect dispatched.'
        if revalidation_failed:
            with transaction.atomic():
                locked = _get_approval_for_update(pk, actor=actor)
                if locked and not locked.is_terminal:
                    invalidate(locked)
                    ApprovalEvent.objects.create(
                        approval=locked,
                        event_type=EventType.REVALIDATION_FAILED,
                        actor_user=actor,
                        event_payload={'drift_report': str(drift_report)},
                    )
                    if locked.can_transition_to(ApprovalStatus.CHANGES_REQUESTED):
                        locked.transition_to(
                            ApprovalStatus.CHANGES_REQUESTED,
                            actor_user=actor,
                            event_payload={'reason': 'revalidation_failed'},
                        )
            _reject(
                'conflict',
                'Approve-time revalidation failed; review is required again.',
                409,
            )

    # ── Phase 3: Lock + transition to approved → executing ──
    with transaction.atomic():
        locked = _get_approval_for_update(pk, actor=actor)
        if not locked:
            return _reject(
                'not_found', f'Approval {pk} not found', status.HTTP_404_NOT_FOUND
            )

        # Re-verify state after acquiring lock
        if locked.status in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.EXECUTING,
            ApprovalStatus.SUCCEEDED,
        ):
            data = approval_serializers.ApprovalDetailSerializer(locked).data
            data['was_already_terminal'] = True
            return ApprovalServiceResult(data, status=status.HTTP_200_OK)

        if locked.is_terminal:
            data = approval_serializers.ApprovalDetailSerializer(locked).data
            data['was_already_terminal'] = True
            return ApprovalServiceResult(data, status=status.HTTP_200_OK)

        if not locked.can_transition_to(ApprovalStatus.APPROVED):
            return _reject(
                'conflict',
                f'Cannot transition from {locked.status} to approved',
                status.HTTP_409_CONFLICT,
                current_status=locked.status,
            )

        actor = _require_reviewer(actor)
        if compute_review_hash(locked) != expected_hash:
            _reject(
                'conflict',
                'The approval changed during revalidation; review it again.',
                409,
            )
        try:
            locked.check_lock_allows_action(actor, 'approve')
        except ValueError as exc:
            _reject('locked', str(exc), 423)
        _approve_gates(locked, actor, channel)

        mailbox_bound = hasattr(locked, 'email_draft')
        if mailbox_bound:
            from ai.core.integrations.email.contracts import MailboxError
            from aichat.services.email.access import require_account

            try:
                require_account(actor, locked.email_draft.account_id, 'send')
            except MailboxError:
                _reject('forbidden', 'Mailbox send permission required.', 403)

        # The operation key exists durably before any possible external dispatch.
        execution, created = ApprovalExecution.objects.get_or_create(
            idempotency_key=locked.idempotency_key,
            defaults={
                'approval': locked,
                'actor': actor,
                'revision': locked.current_revision_number,
                'review_hash': expected_hash,
                'state': 'pending_dispatch' if mailbox_bound else 'submitting',
            },
        )
        if not created:
            _reject(
                'execution_exists',
                'This operation already has an execution record; check its status instead of retrying.',
                409,
            )
        locked.transition_to(ApprovalStatus.APPROVED, actor_user=actor)
        if preflight_failure is None:
            if mailbox_bound:
                locked.execution_result = {
                    'execution_state': 'pending_dispatch',
                    'operation_id': execution.pk,
                }
                locked.save(update_fields=['execution_result'])
            locked.transition_to(ApprovalStatus.EXECUTING, actor_user=actor)
            if mailbox_bound:
                from aichat.services.email.dispatch import publish

                transaction.on_commit(lambda: publish(execution.pk))

    if preflight_failure is not None:
        locked = record_result(locked.pk, execution.pk, preflight_failure)
    elif not mailbox_bound:
        locked = dispatch(executor, locked, actor=actor, execution=execution)
    if locked.status == ApprovalStatus.SUCCEEDED:
        attempt_agent_resume(locked, 'approved', actor)
    return ApprovalServiceResult(
        approval_serializers.ApprovalDetailSerializer(locked).data
    )


@transaction.atomic
def deny(approval_id, *, actor, data=None, channel='screen', evidence=None):
    """Run deny against the authoritative approval state."""
    actor = _require_reviewer(actor)
    serializer = approval_serializers.DenySerializer(data=(data or {}))
    serializer.is_valid(raise_exception=True)

    approval = _get_approval_for_update(approval_id, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {approval_id} not found', status.HTTP_404_NOT_FOUND
        )

    # Idempotent: if already terminal, return
    if approval.is_terminal:
        return ApprovalServiceResult(
            approval_serializers.ApprovalDetailSerializer(approval).data,
            status=status.HTTP_200_OK,
        )

    # Check lock blocks deny for non-holder
    try:
        approval.check_lock_allows_action(actor, 'deny')
    except ValueError as e:
        return _reject(
            'locked',
            str(e),
            status.HTTP_423_LOCKED,
            holder_user_id=approval.lock_holder_id,
            expires_at=(
                approval.modification_lock_expires_at.isoformat()
                if approval.modification_lock_expires_at
                else None
            ),
        )

    if not approval.can_transition_to(ApprovalStatus.DENIED):
        return _reject(
            'conflict',
            f'Cannot transition from {approval.status} to denied',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    reason = serializer.validated_data['reason']
    approval.deny_reason = reason
    # D-3: Save deny_reason atomically with transition
    approval.transition_to(
        ApprovalStatus.DENIED,
        actor_user=actor,
        event_payload={'reason': reason},
        extra_update_fields=['deny_reason'],
    )

    # A-3: Resume agent runtime with denial
    attempt_agent_resume(approval, 'denied', actor)

    return ApprovalServiceResult(
        approval_serializers.ApprovalDetailSerializer(approval).data,
        status=status.HTTP_200_OK,
    )


@transaction.atomic
def cancel(approval_id, *, actor, data=None, channel='screen', evidence=None):
    """Run cancel against the authoritative approval state."""
    actor = _require_reviewer(actor)
    serializer = approval_serializers.CancelSerializer(data=(data or {}))
    serializer.is_valid(raise_exception=True)

    approval = _get_approval_for_update(approval_id, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {approval_id} not found', status.HTTP_404_NOT_FOUND
        )

    # Idempotent: if already terminal, return
    if approval.is_terminal:
        return ApprovalServiceResult(
            approval_serializers.ApprovalDetailSerializer(approval).data,
            status=status.HTTP_200_OK,
        )

    if not approval.can_transition_to(ApprovalStatus.CANCELED):
        return _reject(
            'conflict',
            f'Cannot transition from {approval.status} to canceled',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    reason = serializer.validated_data.get('reason', '')

    # If there are revisions beyond 0, revert to previous
    if approval.current_revision_number > 0:
        prev_revision = ApprovalRevision.objects.filter(
            approval=approval, revision_number=approval.current_revision_number - 1
        ).first()
        if prev_revision:
            old_revision = approval.current_revision_number
            approval.payload = prev_revision.payload_snapshot
            # A-7: Update current_revision_number on revert
            approval.current_revision_number = prev_revision.revision_number
            approval.save(
                update_fields=['payload', 'current_revision_number', 'updated_at']
            )

            ApprovalEvent.objects.create(
                approval=approval,
                event_type=EventType.CANCEL_REVERTED,
                actor_user=actor,
                event_payload={
                    'reverted_from_revision': old_revision,
                    'reverted_to_revision': prev_revision.revision_number,
                },
            )

    # D-3: Store cancel reason and transition atomically
    approval.canceled_reason = reason
    approval.transition_to(
        ApprovalStatus.CANCELED,
        actor_user=actor,
        event_payload={'reason': reason},
        extra_update_fields=['canceled_reason'],
    )

    # A-3: Resume agent with cancellation
    attempt_agent_resume(approval, 'canceled', actor)

    return ApprovalServiceResult(
        approval_serializers.ApprovalDetailSerializer(approval).data,
        status=status.HTTP_200_OK,
    )


@transaction.atomic
def acquire_modify_lock(
    approval_id, *, actor, data=None, channel='screen', evidence=None
):
    """Run acquire modify lock against the authoritative approval state."""
    actor = _require_reviewer(actor)
    approval = _get_approval_for_update(approval_id, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {approval_id} not found', status.HTTP_404_NOT_FOUND
        )

    if approval.is_terminal:
        return _reject(
            'conflict',
            'Cannot modify a terminal approval',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    try:
        lock_meta = approval.acquire_lock(actor)
    except ValueError as e:
        # A-11: Return 423 Locked for lock conflicts
        return _reject(
            'locked',
            str(e),
            status.HTTP_423_LOCKED,
            holder_user_id=approval.lock_holder_id,
            expires_at=(
                approval.modification_lock_expires_at.isoformat()
                if approval.modification_lock_expires_at
                else None
            ),
        )

    return ApprovalServiceResult(lock_meta, status=status.HTTP_200_OK)


@transaction.atomic
def release_modify_lock(
    approval_id, *, actor, data=None, channel='screen', evidence=None
):
    """Run release modify lock against the authoritative approval state."""
    actor = _require_reviewer(actor)
    approval = _get_approval_for_update(approval_id, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {approval_id} not found', status.HTTP_404_NOT_FOUND
        )

    try:
        approval.release_lock(actor)
    except ValueError as e:
        return _reject('forbidden', str(e), status.HTTP_403_FORBIDDEN)

    return ApprovalServiceResult({'detail': 'Lock released'}, status=status.HTTP_200_OK)


@transaction.atomic
def revise(approval_id, *, actor, data=None, channel='screen', evidence=None):
    """Run revise against the authoritative approval state."""
    actor = _require_reviewer(actor)
    serializer = approval_serializers.ReviseSerializer(data=(data or {}))
    serializer.is_valid(raise_exception=True)

    approval = _get_approval_for_update(approval_id, actor=actor)
    if not approval:
        return _reject(
            'not_found', f'Approval {approval_id} not found', status.HTTP_404_NOT_FOUND
        )

    # Status restriction: only in_review or changes_requested
    allowed_statuses = {ApprovalStatus.IN_REVIEW, ApprovalStatus.CHANGES_REQUESTED}
    if approval.status not in allowed_statuses:
        return _reject(
            'invalid_status',
            'Revisions are only allowed when status is in_review or changes_requested',
            status.HTTP_409_CONFLICT,
            current_status=approval.status,
        )

    # Lock enforcement
    if approval.is_lock_active and approval.modification_lock_user_id != actor.pk:
        return _reject(
            'locked',
            'Approval is being modified by another user',
            status.HTTP_423_LOCKED,
            holder_user_id=approval.modification_lock_user_id,
            expires_at=(
                approval.modification_lock_expires_at.isoformat()
                if approval.modification_lock_expires_at
                else None
            ),
        )

    # Optimistic concurrency check
    expected_rev = serializer.validated_data['expected_revision']
    if expected_rev != approval.current_revision_number:
        return _reject(
            'conflict',
            f'Expected revision {expected_rev} but current is {approval.current_revision_number}',
            status.HTTP_409_CONFLICT,
            current_revision=approval.current_revision_number,
        )

    data = serializer.validated_data
    new_revision_number = approval.current_revision_number + 1

    if hasattr(approval, 'email_draft'):
        from ai.core.integrations.email.contracts import MailboxError
        from aichat.services.email.drafts import revise_draft

        try:
            data['payload'] = revise_draft(approval, actor, data['payload'])
        except MailboxError as exc:
            _reject('invalid_payload', exc.code, 400)

    from .executors import registry

    executor = (
        registry.get(approval.action_type)
        if registry.has(approval.action_type)
        else None
    )
    if getattr(executor, 'requires_canonical_payload', False):
        try:
            data['payload'] = executor.prepare_payload(data['payload'])
            approval.baseline_context = executor.compute_baseline(data['payload'])
        except Exception as exc:
            _reject('invalid_payload', str(exc), 400)

    # A-9: Payload size check moved to ReviseSerializer.validate_payload()

    # Create new revision
    ApprovalRevision.objects.create(
        approval=approval,
        revision_number=new_revision_number,
        payload_snapshot=data['payload'],
        diff_summary=data.get('diff_summary'),
        created_by_user=actor,
    )

    # Update approval
    approval.payload = data['payload']
    approval.current_revision_number = new_revision_number
    approval.save(
        update_fields=[
            'payload',
            'baseline_context',
            'current_revision_number',
            'updated_at',
        ]
    )

    invalidate(approval)

    # Emit revised event
    ApprovalEvent.objects.create(
        approval=approval,
        event_type=EventType.REVISED,
        actor_user=actor,
        event_payload={
            'revision_number': new_revision_number,
            'diff_summary': data.get('diff_summary'),
            'note': data.get('note', ''),
        },
    )

    return ApprovalServiceResult(
        approval_serializers.ApprovalDetailSerializer(approval).data,
        status=status.HTTP_200_OK,
    )
