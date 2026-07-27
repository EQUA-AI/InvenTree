"""Unified, extensible work-order readiness evaluation."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from django.conf import settings
from django.utils import timezone

from tasks.models import WorkOrderLifecycle
from tasks.scope import ScopeError, require_work_order_scope, scope_for_work_order
from tasks.services.finalization import PacketFinalization, is_packet_finalization

SCOPE_UNRESOLVED = 'SCOPE_UNRESOLVED'
SCOPE_MISMATCH = 'SCOPE_MISMATCH'
PACKET_OWNS_LIFECYCLE = 'PACKET_OWNS_LIFECYCLE'
ASSET_REQUIRED = 'ASSET_REQUIRED'
ASSIGNEE_REQUIRED = 'ASSIGNEE_REQUIRED'
APPROVAL_PENDING = 'APPROVAL_PENDING'
PROCEDURE_REQUIRED = 'PROCEDURE_REQUIRED'
PROCEDURE_INELIGIBLE = 'PROCEDURE_INELIGIBLE'
PROCEDURE_DRIFT = 'PROCEDURE_DRIFT'
STEP_REQUIRED = 'STEP_REQUIRED'
STEP_FAILED = 'STEP_FAILED'
HOLD_POINT_BLOCKED = 'HOLD_POINT_BLOCKED'
SAFETY_PACKET_REQUIRED = 'SAFETY_PACKET_REQUIRED'
SAFETY_GATE_BLOCKED = 'SAFETY_GATE_BLOCKED'
RETURN_TO_SERVICE_BLOCKED = 'RETURN_TO_SERVICE_BLOCKED'
JOB_KIT_REQUIRED = 'JOB_KIT_REQUIRED'
JOB_KIT_SHORT = 'JOB_KIT_SHORT'
JOB_KIT_NOT_STAGED = 'JOB_KIT_NOT_STAGED'
JOB_KIT_SCAN_REQUIRED = 'JOB_KIT_SCAN_REQUIRED'
TOOL_RETURN_REQUIRED = 'TOOL_RETURN_REQUIRED'
INSTRUMENT_CALIBRATION_INVALID = 'INSTRUMENT_CALIBRATION_INVALID'
CLOSEOUT_REQUIRED = 'CLOSEOUT_REQUIRED'
VERIFICATION_REQUIRED = 'VERIFICATION_REQUIRED'
PART_VARIANCE_UNRESOLVED = 'PART_VARIANCE_UNRESOLVED'
PART_CANDIDATE_UNRESOLVED = 'PART_CANDIDATE_UNRESOLVED'
STALE_VERSION = 'STALE_VERSION'
READINESS_ERROR = 'READINESS_ERROR'

POLICY_VERSION = 1


@dataclass(frozen=True)
class ReadinessBlocker:
    """One stable, explainable readiness decision."""

    code: str
    message: str
    source: str
    object_type: str
    object_id: str
    blocking: bool = True
    remediation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkOrderReadiness:
    """Immutable readiness result containing every discovered blocker."""

    action: str
    ready: bool
    evaluated_at: datetime
    lifecycle_version: int
    policy_version: int
    blockers: tuple[ReadinessBlocker, ...]
    warnings: tuple[ReadinessBlocker, ...]
    snapshot_hash: str


@dataclass(frozen=True)
class _ReadinessContext:
    work_order: Any
    action: str
    actor: Any
    expected_version: int | None
    # Set only by the packet-owned finalization service, which is *the* path a
    # packet's work order is allowed to complete through. It suppresses the
    # packet-ownership blocker and nothing else - every other readiness check
    # still applies, so safety, parts and verification are not weakened.
    #
    # A PacketFinalization token rather than a flag, so a request-borne value
    # cannot satisfy it; see tasks.services.finalization.
    packet_finalization: PacketFinalization | None = None


ReadinessCheck = Callable[[_ReadinessContext], list[ReadinessBlocker]]


def _blocker(
    context, code, message, *, metadata=None, blocking=True
) -> ReadinessBlocker:
    return ReadinessBlocker(
        code=code,
        message=message,
        source='work_order',
        object_type='KanbanCard',
        object_id=str(context.work_order.pk),
        blocking=blocking,
        metadata=metadata or {},
    )


def _scope_check(context):
    try:
        scope_for_work_order(context.work_order)
        require_work_order_scope(context.actor, context.work_order)
    except ScopeError as exc:
        code = SCOPE_MISMATCH if 'match' in str(exc).lower() else SCOPE_UNRESOLVED
        return [_blocker(context, code, str(exc))]
    return []


def _packet_ownership_check(context):
    if is_packet_finalization(context.packet_finalization, context.work_order):
        return []
    if hasattr(context.work_order, 'repair_packet'):
        return [
            _blocker(
                context,
                PACKET_OWNS_LIFECYCLE,
                'The linked Repair Packet owns this work-order lifecycle',
            )
        ]
    return []


def _asset_check(context):
    required_actions = {'start', 'resume', 'verify', 'complete'}
    required = getattr(settings, 'AIMMS_WORK_ORDER_REQUIRE_ASSET', True)
    if (
        required
        and context.action in required_actions
        and context.work_order.machine_id is None
    ):
        return [
            _blocker(context, ASSET_REQUIRED, 'An asset is required for this action')
        ]
    return []


def _assignee_check(context):
    required_actions = {'start', 'resume', 'complete'}
    required = getattr(settings, 'AIMMS_WORK_ORDER_REQUIRE_ASSIGNEE', True)
    if (
        required
        and context.action in required_actions
        and context.work_order.assigned_to_id is None
    ):
        return [
            _blocker(
                context,
                ASSIGNEE_REQUIRED,
                'A typed assignee is required for this action',
            )
        ]
    return []


def _version_check(context):
    if (
        context.expected_version is not None
        and context.work_order.lifecycle_version != context.expected_version
    ):
        return [
            _blocker(
                context,
                STALE_VERSION,
                'The work order has changed since it was loaded',
                metadata={
                    'expected_version': context.expected_version,
                    'current_version': context.work_order.lifecycle_version,
                },
            )
        ]
    return []


_ACTION_STATES = {
    'plan': {WorkOrderLifecycle.DRAFT},
    'mark_ready': {WorkOrderLifecycle.PLANNED},
    'start': {WorkOrderLifecycle.READY},
    'hold': {WorkOrderLifecycle.IN_PROGRESS},
    'resume': {WorkOrderLifecycle.ON_HOLD},
    'verify': {WorkOrderLifecycle.IN_PROGRESS},
    'complete': {WorkOrderLifecycle.VERIFYING},
    'cancel': {
        WorkOrderLifecycle.DRAFT,
        WorkOrderLifecycle.PLANNED,
        WorkOrderLifecycle.READY,
        WorkOrderLifecycle.ON_HOLD,
    },
    'assign': set(WorkOrderLifecycle.values),
    'rework': {WorkOrderLifecycle.VERIFYING},
    'readiness_drift': {WorkOrderLifecycle.READY},
}


def _lifecycle_check(context):
    allowed = _ACTION_STATES.get(context.action)
    if allowed is None or context.work_order.lifecycle_status not in allowed:
        return [
            _blocker(
                context,
                READINESS_ERROR,
                f'Action {context.action!r} is not legal from lifecycle state '
                f'{context.work_order.lifecycle_status!r}',
            )
        ]
    return []


# Future phases append procedure, kit, and safety checks to this registry.
READINESS_CHECKS: list[ReadinessCheck] = [
    _scope_check,
    _packet_ownership_check,
    _asset_check,
    _assignee_check,
    _version_check,
    _lifecycle_check,
]


def _required_steps_check(context):
    """Block verify/complete when the primary procedure is unfinished or failed."""
    if context.action not in {'verify', 'complete'}:
        return []

    # Lazy import prevents the split task model modules from forming a cycle.
    from tasks.models import (
        ProcedureStepType,
        StepExecutionStatus,
        WorkOrderProcedureApplication,
    )

    application = (
        WorkOrderProcedureApplication.objects
        .filter(work_order=context.work_order, primary=True)
        .order_by('pk')
        .first()
    )
    if application is None:
        return []

    executions = list(application.step_executions.all())
    incomplete = [
        item
        for item in executions
        if item.step_snapshot.get('required', False)
        and item.status
        not in {StepExecutionStatus.COMPLETED, StepExecutionStatus.NOT_APPLICABLE}
    ]
    failed_verifications = [
        item
        for item in executions
        if item.step_snapshot.get('step_type') == ProcedureStepType.VERIFICATION
        and item.status == StepExecutionStatus.FAILED
    ]
    blockers = []
    if incomplete:
        blockers.append(
            _blocker(
                context,
                STEP_REQUIRED,
                'Required procedure steps are incomplete',
                metadata={'step_keys': [str(item.step_key) for item in incomplete]},
            )
        )
    if failed_verifications:
        blockers.append(
            _blocker(
                context,
                STEP_FAILED,
                'A verification procedure step has failed',
                metadata={
                    'step_keys': [str(item.step_key) for item in failed_verifications]
                },
            )
        )
    return blockers


# Procedure execution is additive: retain the established ordering above.
READINESS_CHECKS.append(_required_steps_check)


def _closeout_wizard_enabled() -> bool:
    return bool(getattr(settings, 'AIMMS_CLOSEOUT_WIZARD_ENABLED', False))


def _closeout_recon_enforced() -> bool:
    return bool(getattr(settings, 'AIMMS_CLOSEOUT_RECON_ENFORCED', False))


def _closeout_capture_check(context):
    """Block completion while an in-flight capture lacks required decisions."""
    if context.action != 'complete' or not _closeout_wizard_enabled():
        return []

    # Lazy import: the closeout services import this module's exception types.
    from tasks.services.closeout_capture import (
        _live_proposal,
        active_capture,
        decisions_cover_required_fields,
    )

    capture = active_capture(context.work_order)
    if capture is None:
        return []
    from tasks.closeout_models import CloseoutCaptureStatus

    if capture.status != CloseoutCaptureStatus.REVIEWED:
        return [
            _blocker(
                context,
                CLOSEOUT_REQUIRED,
                'The closeout capture has not finished review',
                metadata={'capture_id': capture.pk, 'capture_status': capture.status},
            )
        ]
    proposal = (
        _live_proposal(capture.current_revision) if capture.current_revision else None
    )
    missing = (
        decisions_cover_required_fields(proposal)
        if proposal is not None
        else ['action', 'result', 'verification_summary']
    )
    if missing:
        return [
            _blocker(
                context,
                CLOSEOUT_REQUIRED,
                'Required closeout fields are missing reviewed decisions',
                metadata={'capture_id': capture.pk, 'missing_fields': missing},
            )
        ]
    return []


def _closeout_readings_check(context):
    """Block completion while required readings are pending or failed."""
    if context.action != 'complete' or not _closeout_wizard_enabled():
        return []
    from tasks.services.closeout_reconcile import unresolved_required_readings

    readings = unresolved_required_readings(context.work_order)
    if not readings:
        return []
    return [
        _blocker(
            context,
            VERIFICATION_REQUIRED,
            'Required closeout readings are unresolved',
            metadata={
                'reading_ids': [reading.pk for reading in readings],
                'remediation': 'closeout_readings',
            },
        )
    ]


def _closeout_parts_check(context):
    """Surface unresolved usage variances and narrative candidates."""
    if context.action != 'complete' or not _closeout_wizard_enabled():
        return []
    from tasks.closeout_models import CloseoutPartUsage, CloseoutPartUsageState
    from tasks.jobkit_models import ACTIVE_ALLOCATION_STATUSES, JobKitAllocation
    from tasks.services.closeout_reconcile import unresolved_usage_rows

    variances, candidates = unresolved_usage_rows(context.work_order)
    enforced = _closeout_recon_enforced()
    candidate_policy = str(
        getattr(settings, 'AIMMS_CLOSEOUT_CANDIDATE_POLICY', 'block')
    )
    # Active allocations without a reconciled usage row are unaccounted-for
    # custody: consumption is reconciled, never inferred (CO-ADR-004).
    reconciled_allocations = set(
        CloseoutPartUsage.objects.filter(
            work_order=context.work_order,
            state=CloseoutPartUsageState.RECONCILED,
            allocation__isnull=False,
        ).values_list('allocation_id', flat=True)
    )
    unseeded = [
        allocation_id
        for allocation_id in JobKitAllocation.objects.filter(
            line__kit__work_order=context.work_order,
            status__in=[status.value for status in ACTIVE_ALLOCATION_STATUSES],
        ).values_list('pk', flat=True)
        if allocation_id not in reconciled_allocations
    ]
    blockers = []
    if variances or unseeded:
        blockers.append(
            _blocker(
                context,
                PART_VARIANCE_UNRESOLVED,
                'Part usage has not been reconciled against custody truth',
                blocking=enforced,
                metadata={
                    'row_ids': [row.pk for row in variances],
                    'unreconciled_allocation_ids': unseeded,
                    'remediation': 'closeout_part_usage',
                },
            )
        )
    if candidates:
        blockers.append(
            _blocker(
                context,
                PART_CANDIDATE_UNRESOLVED,
                'Narrative part candidates are neither bound nor dismissed',
                blocking=enforced and candidate_policy == 'block',
                metadata={
                    'row_ids': [row.pk for row in candidates],
                    'remediation': 'closeout_part_usage',
                },
            )
        )
    return blockers


def _closeout_tool_return_check(context):
    """Require issued tools and safety equipment back before completion."""
    if (
        context.action != 'complete'
        or not _closeout_wizard_enabled()
        or not _closeout_recon_enforced()
    ):
        return []
    from tasks.jobkit_models import JobKitAllocation, JobKitAllocationStatus
    from tasks.procedure_models import ProcedureResourceKind

    outstanding = list(
        JobKitAllocation.objects.filter(
            line__kit__work_order=context.work_order,
            status=JobKitAllocationStatus.ISSUED,
            line__kind__in=[ProcedureResourceKind.TOOL, ProcedureResourceKind.SAFETY],
        ).values_list('pk', flat=True)
    )
    if not outstanding:
        return []
    return [
        _blocker(
            context,
            TOOL_RETURN_REQUIRED,
            'Issued tools or safety equipment have not been returned',
            metadata={
                'allocation_ids': outstanding,
                'remediation': 'job_kit_allocations',
            },
        )
    ]


# Closeout Automation (Feature #15) checks are additive and flag-gated; with
# the wizard flag off the registry behaves exactly as before.
READINESS_CHECKS.extend([
    _closeout_capture_check,
    _closeout_readings_check,
    _closeout_parts_check,
    _closeout_tool_return_check,
])


def evaluate_work_order_readiness(
    work_order,
    *,
    action: str,
    actor,
    expected_version: int | None = None,
    packet_finalization: PacketFinalization | None = None,
) -> WorkOrderReadiness:
    """Evaluate all registered checks, converting unknown failures to blockers."""
    context = _ReadinessContext(
        work_order, action, actor, expected_version, packet_finalization
    )
    blockers: list[ReadinessBlocker] = []
    for check in READINESS_CHECKS:
        try:
            blockers.extend(check(context))
        except Exception as exc:  # Fail closed at the readiness boundary.
            blockers.append(
                _blocker(
                    context,
                    READINESS_ERROR,
                    'Readiness evaluation failed',
                    metadata={
                        'check': check.__name__,
                        'error_type': type(exc).__name__,
                    },
                )
            )

    warnings = tuple(blocker for blocker in blockers if not blocker.blocking)
    blocking = tuple(blocker for blocker in blockers if blocker.blocking)
    snapshot = {
        'action': action,
        'lifecycle_version': work_order.lifecycle_version,
        'policy_version': POLICY_VERSION,
        'blockers': [asdict(item) for item in blocking],
        'warnings': [asdict(item) for item in warnings],
    }
    snapshot_hash = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()
    return WorkOrderReadiness(
        action=action,
        ready=not blocking,
        evaluated_at=timezone.now(),
        lifecycle_version=work_order.lifecycle_version,
        policy_version=POLICY_VERSION,
        blockers=blocking,
        warnings=warnings,
        snapshot_hash=snapshot_hash,
    )
