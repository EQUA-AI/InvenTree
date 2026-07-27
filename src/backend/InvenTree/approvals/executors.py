"""Executor registry for the AI Agent Approval Queue.

Implements the registry-based dispatcher from spec Section 17.

Each action_type must have a registered executor implementing
the ApprovalExecutor interface.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import structlog

from .models import ActionType

logger = structlog.get_logger('approvals.executors')


EXECUTOR_REQUIRED_ACTIONS = frozenset({
    ActionType.PROCEDURE_PUBLISH,
    ActionType.JOB_KIT_SUBSTITUTION,
    # Creating a repair commits parts, a machine and a safety aggregate. If its
    # executor were ever unregistered, approving must fail loudly rather than
    # silently succeeding with no effect.
    ActionType.REPAIR_WORK_PACKAGE,
})


def is_executor_required(action_type) -> bool:
    """Return whether an action must have a registered executor."""
    return action_type in EXECUTOR_REQUIRED_ACTIONS


# ---------------------------------------------------------------------------
# Data classes for executor results
# ---------------------------------------------------------------------------


@dataclass
class DriftReport:
    """Result of pre-execution drift check (Section 17.1)."""

    has_drift: bool
    passed: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class EffectResult:
    """Result of executing a side effect (Section 17.1)."""

    success: bool
    effect_ref: Optional[str] = None
    result_payload: Optional[dict] = None
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Base executor class
# ---------------------------------------------------------------------------


class ApprovalExecutor(ABC):
    """Base class for all approval action type executors.

    Every action_type must have a registered executor implementing this
    interface. The executor is responsible for:
    - Validating the payload shape/content
    - Checking live preconditions vs baseline at approve-time
    - Executing the side effect idempotently
    """

    action_type: str  # Must match ActionType enum value

    @abstractmethod
    def validate(self, payload: dict) -> list[str]:
        """Validate payload shape/content.

        Returns:
            list of warning strings (empty = valid).
        """
        ...

    @abstractmethod
    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        """Check live state against baseline for drift.

        Called at approve-time (Section 10).

        Returns:
            DriftReport with pass/fail details.
        """
        ...

    @abstractmethod
    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Execute the side effect.

        Must be idempotent for the given key.

        Returns:
            EffectResult with outcome.
        """
        ...

    def compute_baseline(self, payload: dict) -> dict:
        """Compute baseline_context snapshot at creation time.

        Override per action type to snapshot live state for later
        drift comparison.
        """
        return {}

    def compute_risk_tier(self, payload: dict) -> int:
        """Compute risk tier.

        Override for dynamic tier calculation (Phase 1: always 2).
        """
        return 2


# ---------------------------------------------------------------------------
# Executor Registry
# ---------------------------------------------------------------------------


class ExecutorRegistry:
    """Registry of approval executors keyed by action_type.

    Executors must be registered at startup. The registry fails loudly
    if an action_type has no registered executor.
    """

    def __init__(self):
        """Initialize the instance."""
        self._executors: dict[str, ApprovalExecutor] = {}

    def register(self, executor: ApprovalExecutor):
        """Register an executor for an action_type.

        Args:
            executor: An ApprovalExecutor instance.

        Raises:
            ValueError: If action_type is already registered.
        """
        action_type = executor.action_type
        if action_type in self._executors:
            raise ValueError(
                f'Executor already registered for action_type: {action_type}'
            )
        self._executors[action_type] = executor
        logger.info('executor_registered', action_type=action_type)

    def get(self, action_type: str) -> ApprovalExecutor:
        """Get the executor for an action_type.

        Args:
            action_type: The action type string.

        Returns:
            The registered ApprovalExecutor.

        Raises:
            KeyError: If no executor is registered for the action_type.
        """
        if action_type not in self._executors:
            raise KeyError(f'No executor registered for action_type: {action_type}')
        return self._executors[action_type]

    def has(self, action_type: str) -> bool:
        """Check if an executor is registered for the given action_type."""
        return action_type in self._executors

    def list_registered(self) -> list[str]:
        """Return list of registered action_types."""
        return list(self._executors.keys())


# Singleton registry instance
registry = ExecutorRegistry()


# ---------------------------------------------------------------------------
# Stub executors (Phase 1 — to be replaced with real implementations)
# ---------------------------------------------------------------------------


class EmailExecutor(ApprovalExecutor):
    """Executor for sending emails (Phase 1 stub).

    Real implementation will use the connected email integration.
    """

    action_type = 'email'

    def validate(self, payload: dict) -> list[str]:
        """Validate."""
        warnings = []
        if 'to' not in payload or not payload['to']:
            warnings.append('Missing "to" recipients')
        if 'subject' not in payload:
            warnings.append('Missing "subject"')
        return warnings

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        # Phase 1: no live checks, always pass
        """Check preconditions."""
        return DriftReport(has_drift=False)

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        # Phase 1 stub — real implementation in Phase 4
        """Execute."""
        logger.info(
            'email_executor_stub',
            to=payload.get('to'),
            subject=payload.get('subject'),
            idempotency_key=idempotency_key,
        )
        return EffectResult(
            success=True,
            effect_ref=f'stub-email-{idempotency_key[:12]}',
            result_payload={'stub': True},
        )


class PurchaseOrderExecutor(ApprovalExecutor):
    """Executor for creating Purchase Orders (Phase 1 stub)."""

    action_type = 'purchase_order'

    def validate(self, payload: dict) -> list[str]:
        """Validate."""
        warnings = []
        if 'supplier_id' not in payload:
            warnings.append('Missing "supplier_id"')
        if 'line_items' not in payload or not payload['line_items']:
            warnings.append('Missing or empty "line_items"')
        return warnings

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        # Phase 4 will check: supplier exists/active, parts valid, etc.
        """Check preconditions."""
        return DriftReport(has_drift=False)

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Execute."""
        logger.info(
            'po_executor_stub',
            supplier_id=payload.get('supplier_id'),
            idempotency_key=idempotency_key,
        )
        return EffectResult(
            success=True,
            effect_ref=f'stub-po-{idempotency_key[:12]}',
            result_payload={'stub': True},
        )


class SalesOrderExecutor(ApprovalExecutor):
    """Executor for creating Sales Orders (Phase 1 stub)."""

    action_type = 'sales_order'

    def validate(self, payload: dict) -> list[str]:
        """Validate."""
        warnings = []
        if 'customer_id' not in payload:
            warnings.append('Missing "customer_id"')
        if 'line_items' not in payload or not payload['line_items']:
            warnings.append('Missing or empty "line_items"')
        return warnings

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        """Check preconditions."""
        return DriftReport(has_drift=False)

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Execute."""
        logger.info(
            'so_executor_stub',
            customer_id=payload.get('customer_id'),
            idempotency_key=idempotency_key,
        )
        return EffectResult(
            success=True,
            effect_ref=f'stub-so-{idempotency_key[:12]}',
            result_payload={'stub': True},
        )


class StockUpdateExecutor(ApprovalExecutor):
    """Executor for stock updates (Phase 1 stub)."""

    action_type = 'stock_update'

    def validate(self, payload: dict) -> list[str]:
        """Validate."""
        warnings = []
        if 'stock_item_id' not in payload and 'part_id' not in payload:
            warnings.append('Missing "stock_item_id" or "part_id"')
        if 'action' not in payload:
            warnings.append('Missing "action" (transfer/adjust/consume)')
        if 'quantity' not in payload:
            warnings.append('Missing "quantity"')
        return warnings

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        """Check preconditions."""
        return DriftReport(has_drift=False)

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Execute."""
        logger.info(
            'stock_executor_stub',
            stock_item_id=payload.get('stock_item_id'),
            action=payload.get('action'),
            idempotency_key=idempotency_key,
        )
        return EffectResult(
            success=True,
            effect_ref=f'stub-stock-{idempotency_key[:12]}',
            result_payload={'stub': True},
        )


class WorkflowExecutor(ApprovalExecutor):
    """Executor for running workflows (Phase 1 stub)."""

    action_type = 'workflow'

    def validate(self, payload: dict) -> list[str]:
        """Validate."""
        warnings = []
        if 'workflow_id' not in payload and 'workflow_name' not in payload:
            warnings.append('Missing "workflow_id" or "workflow_name"')
        return warnings

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        """Check preconditions."""
        return DriftReport(has_drift=False)

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Execute."""
        logger.info(
            'workflow_executor_stub',
            workflow_id=payload.get('workflow_id'),
            idempotency_key=idempotency_key,
        )
        return EffectResult(
            success=True,
            effect_ref=f'stub-workflow-{idempotency_key[:12]}',
            result_payload={'stub': True},
        )


class NotificationExecutor(ApprovalExecutor):
    """Executor for sending notifications (Phase 1 stub)."""

    action_type = 'notification'

    def validate(self, payload: dict) -> list[str]:
        """Validate."""
        warnings = []
        if 'recipients' not in payload or not payload['recipients']:
            warnings.append('Missing "recipients"')
        if 'message' not in payload:
            warnings.append('Missing "message"')
        return warnings

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        """Check preconditions."""
        return DriftReport(has_drift=False)

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Execute."""
        logger.info(
            'notification_executor_stub',
            recipients=payload.get('recipients'),
            idempotency_key=idempotency_key,
        )
        return EffectResult(
            success=True,
            effect_ref=f'stub-notification-{idempotency_key[:12]}',
            result_payload={'stub': True},
        )


class SafetyGateExecutor(ApprovalExecutor):
    """Executor for approved high-risk safety-gate actions."""

    action_type = 'safety_gate'

    def validate(self, payload: dict) -> list[str]:
        """Validate."""
        warnings = []
        if not payload.get('gate_id'):
            warnings.append('gate_id is required')
        if payload.get('action') not in ('waive', 'confirm'):
            warnings.append('action must be waive or confirm')
        if payload.get('action') == 'waive' and not payload.get('reason'):
            warnings.append('reason is required for waiver')
        return warnings

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        """Check preconditions."""
        try:
            from repair.models import RepairPacketGate

            gate = RepairPacketGate.objects.get(pk=payload.get('gate_id'))
        except Exception:
            return DriftReport(
                has_drift=True,
                failed=[{'check': 'gate_exists', 'reason': 'Safety gate not found'}],
            )
        if gate.packet.is_terminal:
            return DriftReport(
                has_drift=True,
                failed=[{'check': 'packet_active', 'reason': 'Packet is terminal'}],
            )
        return DriftReport(
            has_drift=False,
            passed=[{'check': 'gate_exists'}, {'check': 'packet_active'}],
        )

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Execute."""
        try:
            from repair.models import RepairPacketEvent, RepairPacketGate

            gate = RepairPacketGate.objects.get(pk=payload['gate_id'])
            action = payload.get('action')
            if action == 'waive':
                gate.waive(
                    reason=payload.get('reason', ''),
                    authority=payload.get('authority', 'approval'),
                )
                RepairPacketEvent.objects.create(
                    packet=gate.packet,
                    event_type=RepairPacketEvent.EventType.GATE_WAIVED,
                    reason=payload.get('reason', ''),
                    metadata={
                        'gate_id': gate.pk,
                        'approval_execution': True,
                        'idempotency_key': idempotency_key,
                    },
                )
            elif action == 'confirm':
                gate.confirm(note=payload.get('note', 'approved confirmation'))
                RepairPacketEvent.objects.create(
                    packet=gate.packet,
                    event_type=RepairPacketEvent.EventType.GATE_CONFIRMED,
                    reason=payload.get('note', ''),
                    metadata={
                        'gate_id': gate.pk,
                        'approval_execution': True,
                        'idempotency_key': idempotency_key,
                    },
                )
            else:
                return EffectResult(
                    success=False, error_message='Unsupported safety action'
                )
            return EffectResult(
                success=True,
                effect_ref=f'safety-gate-{gate.pk}',
                result_payload={'gate_id': gate.pk, 'status': gate.status},
            )
        except Exception as exc:
            return EffectResult(success=False, error_message=str(exc))


class RepairWorkPackageExecutor(ApprovalExecutor):
    """Executor for an approved repair work package.

    Approving one creates a machine-linked work order and its Repair Packet. The
    executor owns none of that logic: it delegates to
    ``repair.work_packages.create_repair_work_package``, the same audited command
    the Maintenance button, the machine page and the Health blade call. Approving
    through this queue therefore cannot do anything a planner could not do by
    hand, and cannot skip a check the manual path applies.

    Creating a repair plans work. It does not start it, satisfy a safety gate or
    mark readiness - none of which this executor can reach.
    """

    action_type = ActionType.REPAIR_WORK_PACKAGE

    def validate(self, payload: dict) -> list[str]:
        """Validate the draft against the canonical work-package schema."""
        from repair.work_packages import WorkPackageError, validate_draft

        try:
            validate_draft(payload)
        except WorkPackageError as exc:
            return [str(exc)]
        return []

    def compute_baseline(self, payload: dict) -> dict:
        """Snapshot what the approver is deciding against.

        Records the machine, the open repairs at the time of the request and the
        anomaly's condition. Drift is judged against this, so a repair approved
        days later cannot quietly answer a fault that has since been fixed or
        superseded.
        """
        from assets.models import AssetMachine
        from repair.work_packages import find_duplicate_repairs

        machine = AssetMachine.objects.filter(
            pk=payload.get('machine_id') or payload.get('machine')
        ).first()
        if machine is None:
            return {'machine_exists': False}

        anomaly_id = (payload.get('source') or {}).get('anomaly_id')
        anomaly_state = None
        if anomaly_id:
            from assets.health_models import MachineAnomaly

            anomaly = MachineAnomaly.objects.filter(pk=anomaly_id).first()
            if anomaly is not None:
                anomaly_state = {
                    'id': anomaly.pk,
                    'status': anomaly.status,
                    'severity': anomaly.severity,
                    'machine_id': anomaly.machine_id,
                    'work_order_id': anomaly.work_order_id,
                }

        return {
            'machine_exists': True,
            'machine_id': machine.pk,
            'machine_name': machine.name,
            'open_repair_work_order_ids': sorted(
                item['work_order_id']
                for item in find_duplicate_repairs(machine, anomaly_id=anomaly_id)
            ),
            'anomaly': anomaly_state,
        }

    def check_preconditions(self, payload: dict, baseline_context: dict) -> DriftReport:
        """Re-read live state at approve time and report what moved."""
        from assets.models import AssetMachine
        from repair.work_packages import find_duplicate_repairs

        machine = AssetMachine.objects.filter(
            pk=payload.get('machine_id') or payload.get('machine')
        ).first()
        if machine is None:
            return DriftReport(
                has_drift=True,
                failed=[
                    {
                        'check': 'machine_exists',
                        'reason': 'The machine no longer exists',
                    }
                ],
            )

        passed = [{'check': 'machine_exists'}]
        failed = []
        warnings = []

        anomaly_id = (payload.get('source') or {}).get('anomaly_id')
        baseline_anomaly = baseline_context.get('anomaly') or {}

        if anomaly_id:
            from assets.health_models import ACTIVE_ANOMALY_STATUSES, MachineAnomaly

            anomaly = MachineAnomaly.objects.filter(pk=anomaly_id).first()
            if anomaly is None:
                failed.append({
                    'check': 'anomaly_exists',
                    'reason': 'The anomaly this repair answers no longer exists',
                })
            elif anomaly.machine_id != machine.pk:
                failed.append({
                    'check': 'anomaly_machine',
                    'reason': 'The anomaly belongs to a different machine',
                })
            elif anomaly.status not in {s.value for s in ACTIVE_ANOMALY_STATUSES}:
                # The condition resolved while the request waited. Approving now
                # would raise a repair for a fault nobody currently has.
                failed.append({
                    'check': 'anomaly_active',
                    'reason': (
                        f'The anomaly is now {anomaly.get_status_display().lower()}'
                    ),
                })
            elif (
                anomaly.work_order_id
                and anomaly.work_order_id != baseline_anomaly.get('work_order_id')
            ):
                failed.append({
                    'check': 'anomaly_unclaimed',
                    'reason': 'Another repair has since been raised for this anomaly',
                })
            else:
                passed.append({'check': 'anomaly_active'})

        current_open = sorted(
            item['work_order_id']
            for item in find_duplicate_repairs(machine, anomaly_id=anomaly_id)
        )
        baseline_open = sorted(baseline_context.get('open_repair_work_order_ids') or [])
        new_open = [wo for wo in current_open if wo not in baseline_open]
        if new_open:
            # Not fatal on its own: a second repair may be legitimate. It is
            # surfaced so the approver decides knowingly.
            warnings.append(
                f'{len(new_open)} repair(s) were opened on this machine since the '
                'request was raised'
            )

        return DriftReport(
            has_drift=bool(failed), passed=passed, failed=failed, warnings=warnings
        )

    def execute(self, payload: dict, idempotency_key: str) -> EffectResult:
        """Create the work package through the canonical command."""
        from repair.work_packages import (
            DuplicateRepairConflict,
            WorkPackageError,
            create_repair_work_package,
        )

        actor = self._actor_for(payload)

        try:
            result = create_repair_work_package(
                actor=actor, draft=payload, idempotency_key=idempotency_key
            )
        except DuplicateRepairConflict as exc:
            return EffectResult(
                success=False,
                error_message=str(exc),
                result_payload={'duplicates': exc.duplicates},
            )
        except WorkPackageError as exc:
            return EffectResult(success=False, error_message=str(exc))

        return EffectResult(
            success=True,
            effect_ref=f'work-order-{result.work_order_id}',
            result_payload=result.as_dict(),
        )

    @staticmethod
    def _actor_for(payload: dict):
        """Resolve the actor the command runs as.

        The command re-checks permission and scope for whoever this is, so a
        payload naming a user it should not have does not widen authority - the
        command refuses.
        """
        actor_id = payload.get('actor_id')
        if not actor_id:
            return None

        from django.contrib.auth import get_user_model

        return get_user_model().objects.filter(pk=actor_id, is_active=True).first()

    def compute_risk_tier(self, payload: dict) -> int:
        """Critical faults warrant the higher review tier."""
        criticality = (payload.get('fault') or {}).get('criticality')
        return 3 if criticality == 'critical' else 2


# ---------------------------------------------------------------------------
# Register all Phase 1 stub executors
# ---------------------------------------------------------------------------


def register_default_executors():
    """Register all default executors. Called at app startup."""
    executors = [
        EmailExecutor(),
        PurchaseOrderExecutor(),
        SalesOrderExecutor(),
        StockUpdateExecutor(),
        WorkflowExecutor(),
        NotificationExecutor(),
        SafetyGateExecutor(),
        RepairWorkPackageExecutor(),
    ]
    for executor in executors:
        if not registry.has(executor.action_type):
            registry.register(executor)


# Auto-register on import
register_default_executors()


# ---------------------------------------------------------------------------
# Effect idempotency key helper (E-4)
# ---------------------------------------------------------------------------


def compute_effect_idempotency_key(
    approval_idempotency_key: str, effect_type: str, sequence: int = 0
) -> str:
    """Compute a deterministic idempotency key for executed effects.

    Derivation: SHA-256(approval_idempotency_key + ':' + effect_type + ':' + sequence)

    This ensures effect keys are unique per approval + effect type + sequence
    and never collide with the approval-level idempotency key.
    """
    raw = f'{approval_idempotency_key}:{effect_type}:{sequence}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
