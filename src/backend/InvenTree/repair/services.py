"""Service layer for Repair Packet orchestration.

Keeps FSM/lifecycle orchestration and AI-generation wiring out of the API views.
Generation goes through the typed boundary in :mod:`repair.generation` (heuristic
fallback + AI-service forward path) and is recorded in a provenance/idempotency
ledger so retries never double-write. Lifecycle transitions are transactional and
audited.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from tasks.models import WorkOrderLifecycle

from .generation import GenerationResult, get_generator
from .models import (
    GenerationStatus,
    LockoutPoint,
    PacketStatus,
    RepairPacket,
    RepairPacketApprovalLink,
    RepairPacketEvent,
    RepairPacketGate,
    RepairPacketGenerationRun,
    SafetyEvidenceProof,
    SafetyGateTemplate,
    is_valid_packet_transition,
)
from .schema import DIAGNOSIS_SCHEMA_VERSION, validate_diagnosis


# --------------------------------------------------------------------------- #
# Audit helpers
# --------------------------------------------------------------------------- #
def _record_event(
    packet: RepairPacket,
    event_type: str,
    *,
    from_status: str = '',
    to_status: str = '',
    actor=None,
    reason: str = '',
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append an immutable audit event to the packet's history."""
    RepairPacketEvent.objects.create(
        packet=packet,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor=actor if (actor and getattr(actor, 'is_authenticated', False)) else None,
        reason=reason,
        metadata=metadata or {},
    )


# --------------------------------------------------------------------------- #
# Generation (wf7 seam)
# --------------------------------------------------------------------------- #

# Packet criticality and board priority are separate vocabularies: the board has
# no 'critical' step, so critical work lands on the highest board priority it has.
_PACKET_PRIORITY = {
    'low': 'low',
    'medium': 'medium',
    'high': 'high',
    'critical': 'high',
}


def _actor_from_id(user_id):
    """Resolve the generation actor from an offload-safe user id."""
    if not user_id:
        return None

    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=user_id).first()


def _ensure_work_order_with_parts(
    packet: RepairPacket, result: GenerationResult, *, actor=None, run_id: str = ''
):
    """Create/link a work order and materialise resolved part lines onto it.

    The work order is created through ``tasks.services.scheduling.create_work_order``
    so a packet-owned card carries the same audit event, idempotency ledger entry
    and lifecycle defaults as one raised from the board or from an approved AI
    work package. Creating a bare ``WorkOrder`` here is not allowed: it produced
    machineless cards that no scope, readiness or maintenance-history rule could
    resolve.

    A packet with no machine therefore cannot materialise a work order. Rather
    than fabricating an unscoped one, the gap is recorded as a packet event and
    the packet stays re-generatable once its asset is set - diagnosis and safety
    gates from the same run are still persisted.
    """
    from tasks.services import scheduling

    resolvable = [line for line in result.parts if line.part_id]
    if not resolvable and packet.work_order_id is None:
        return

    if packet.work_order_id is None:
        if packet.machine_id is None:
            _record_event(
                packet,
                RepairPacketEvent.EventType.WORK_ORDER_SKIPPED,
                to_status=packet.status,
                actor=actor,
                reason='Work order not created: the packet has no asset.',
                metadata={'reason_code': 'PACKET_HAS_NO_MACHINE', 'run_id': run_id},
            )
            return

        command = scheduling.create_work_order(
            actor=actor,
            idempotency_key=f'repair-packet:{packet.pk}:work-order:{run_id}',
            title=f'Work order for {packet.reference or packet.pk}'[:200],
            machine_id=packet.machine_id,
            description=packet.fault_summary,
            priority=_PACKET_PRIORITY.get(packet.criticality, 'medium'),
            work_order_type='corrective',
        )

        packet.work_order_id = command.work_order_id
        packet.save(update_fields=['work_order', 'updated_at'])

        _record_event(
            packet,
            RepairPacketEvent.EventType.WORK_ORDER_CREATED,
            to_status=packet.status,
            actor=actor,
            metadata={
                'work_order_id': command.work_order_id,
                'correlation_id': str(command.correlation_id),
                'run_id': run_id,
            },
        )

    scheduling.materialise_required_parts(
        work_order_id=packet.work_order_id,
        lines=[(line.part_id, line.quantity) for line in resolvable],
    )


def _matches(pattern: str, value: str | None) -> bool:
    """Case-insensitive regex matcher used by safety-template applicability."""
    if not pattern:
        return True
    if not value:
        return False
    try:
        return bool(re.search(pattern, value))
    except re.error:
        return False


def resolve_templates_for(packet: RepairPacket) -> list[SafetyGateTemplate]:
    """Return active safety templates that apply to a packet."""
    text = f'{packet.fault_summary} {packet.symptom}'.lower()
    machine = packet.machine
    out: list[SafetyGateTemplate] = []

    for template in SafetyGateTemplate.objects.filter(active=True):
        rule = template.applies_to or {}
        if rule.get('always'):
            out.append(template)
            continue
        if (
            'criticality_in' in rule
            and packet.criticality not in rule['criticality_in']
        ):
            continue
        keywords = [str(k).lower() for k in rule.get('fault_keywords', [])]
        if keywords and not any(k in text for k in keywords):
            continue
        if rule.get('manufacturer_matches') and not _matches(
            rule['manufacturer_matches'], machine and machine.manufacturer
        ):
            continue
        if rule.get('model_matches') and not _matches(
            rule['model_matches'], machine and machine.model
        ):
            continue
        if rule.get('location_matches') and not _matches(
            rule['location_matches'], machine and machine.location
        ):
            continue
        out.append(template)
    return out


def resolve_safety_gates(packet: RepairPacket, actor=None) -> int:
    """Materialise applicable safety templates onto a packet, idempotently."""
    created = 0
    for template in resolve_templates_for(packet):
        _, was_created = RepairPacketGate.objects.get_or_create(
            packet=packet,
            template=template,
            defaults={
                'name': template.name,
                'gate_type': template.gate_type,
                'sequence': template.default_sequence,
                'is_blocking': template.is_blocking,
                'is_mandatory': template.is_mandatory,
                'required_permission': template.required_permission,
                'requires_photo': template.requires_photo,
                'requires_second_person': template.requires_second_person,
            },
        )
        created += int(was_created)

    if created:
        _record_event(
            packet,
            RepairPacketEvent.EventType.GATES_RESOLVED,
            actor=actor,
            metadata={'created': created},
        )
    return created


def _create_safety_gates(packet: RepairPacket, result: GenerationResult) -> int:
    """Create generator-suggested advisory gates, then template-backed gates."""
    created = 0
    for gate in result.safety_gates:
        _, was_created = RepairPacketGate.objects.get_or_create(
            packet=packet,
            name=gate.name,
            defaults={
                'gate_type': gate.gate_type,
                'requires_photo': gate.requires_photo,
                'is_blocking': False,
                'is_mandatory': False,
            },
        )
        created += int(was_created)
    created += resolve_safety_gates(packet)
    return created


def run_repair_packet_workflow(
    packet: RepairPacket, params: dict[str, Any] | None = None
) -> RepairPacket:
    """Generate the diagnosis + parts path + safety gates for a packet.

    Idempotent on ``agent_run_id``: a completed run with the same id is a no-op.
    Uses the configured generator (``AIMMS_REPAIR_GENERATOR``; default ``auto``
    prefers the AI service and falls back to the heuristic provider), validates
    the diagnosis against the versioned schema, records a provenance run, and
    advances DRAFT -> DIAGNOSED. Never raises on generation failure - it records
    the failure and leaves the packet re-generatable.
    """
    params = params or {}
    run_id = params.get('agent_run_id') or uuid.uuid4().hex

    # Idempotency: a previously-succeeded run is a no-op.
    existing = RepairPacketGenerationRun.objects.filter(agent_run_id=run_id).first()
    if existing and existing.status == RepairPacketGenerationRun.RunStatus.SUCCEEDED:
        return packet

    if existing:
        run = existing
        run.status = RepairPacketGenerationRun.RunStatus.RUNNING
        run.error = ''
        run.finished_at = None
        run.save(update_fields=['status', 'error', 'finished_at'])
    else:
        run = RepairPacketGenerationRun.objects.create(
            packet=packet,
            agent_run_id=run_id,
            status=RepairPacketGenerationRun.RunStatus.RUNNING,
        )

    packet.generation_status = GenerationStatus.RUNNING
    packet.save(update_fields=['generation_status', 'updated_at'])

    fault = params.get('fault_summary') or packet.fault_summary
    context = {
        'repair_packet_id': packet.pk,
        'reference': packet.reference,
        'machine_id': packet.machine_id,
        'criticality': packet.criticality,
        'thread_id': f'repair-{packet.pk}',
        'user_id': params.get('user_id'),
    }

    try:
        generator = get_generator(params.get('generator'))
        result = generator.generate(fault_summary=fault, context=context)
        validate_diagnosis(result.diagnosis)

        with transaction.atomic():
            packet.diagnosis = result.diagnosis
            packet.diagnosis_schema_version = DIAGNOSIS_SCHEMA_VERSION
            packet.agent_run_id = run_id
            packet.generation_status = GenerationStatus.SUCCEEDED
            if packet.status == PacketStatus.DRAFT:
                packet.status = PacketStatus.DIAGNOSED
            packet.save()

            _ensure_work_order_with_parts(
                packet,
                result,
                actor=_actor_from_id(params.get('user_id')),
                run_id=run_id,
            )
            gates_created = _create_safety_gates(packet, result)

            run.status = RepairPacketGenerationRun.RunStatus.SUCCEEDED
            run.provider = result.provider
            run.finished_at = timezone.now()
            run.result_summary = {
                'provider': result.provider,
                'confidence': result.confidence,
                'parts': len(result.parts),
                'gates_created': gates_created,
            }
            run.save()

            _record_event(
                packet,
                RepairPacketEvent.EventType.GENERATED,
                to_status=packet.status,
                metadata={'provider': result.provider, 'run_id': run_id},
            )
    except Exception as exc:  # fail safe: record + leave re-generatable
        run.status = RepairPacketGenerationRun.RunStatus.FAILED
        run.error = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error', 'finished_at'])
        packet.generation_status = GenerationStatus.FAILED
        packet.save(update_fields=['generation_status', 'updated_at'])
        _record_event(
            packet,
            RepairPacketEvent.EventType.GENERATION_FAILED,
            reason=str(exc),
            metadata={'run_id': run_id},
        )

    return packet


# --------------------------------------------------------------------------- #
# Safety gate actions
# --------------------------------------------------------------------------- #
def _check_gate_permission(gate: RepairPacketGate, user) -> tuple[bool, str]:
    """Check the optional Django permission attached to a gate."""
    if not gate.required_permission:
        return True, ''
    if (
        user
        and getattr(user, 'is_authenticated', False)
        and user.has_perm(gate.required_permission)
    ):
        return True, ''
    return False, f'Missing permission: {gate.required_permission}'


def add_gate_proof(
    gate: RepairPacketGate,
    proof_type: str,
    value: dict[str, Any] | None = None,
    user=None,
    lockout_point: LockoutPoint | None = None,
) -> SafetyEvidenceProof:
    """Attach structured field proof to a safety gate."""
    proof = SafetyEvidenceProof.objects.create(
        gate=gate,
        lockout_point=lockout_point,
        proof_type=proof_type,
        value=value or {},
        captured_by=user
        if (user and getattr(user, 'is_authenticated', False))
        else None,
    )
    _record_event(
        gate.packet,
        RepairPacketEvent.EventType.GATE_VERIFIED,
        actor=user,
        metadata={'gate_id': gate.pk, 'proof_id': proof.pk, 'proof_type': proof_type},
    )
    return proof


def confirm_gate(gate: RepairPacketGate, user=None, note: str = '') -> tuple[bool, str]:
    """Confirm a gate if permissions and required proof are satisfied."""
    ok, detail = _check_gate_permission(gate, user)
    if not ok:
        return False, detail
    if gate.requires_photo and not gate.has_required_photo():
        return False, 'Required photo proof missing'
    if gate.gate_type == 'loto':
        outstanding = gate.lockout_points.exclude(
            status=LockoutPoint.PointStatus.VERIFIED
        ).count()
        if outstanding:
            return False, f'{outstanding} lockout point(s) not verified'
    gate.confirm(user=user, note=note)
    _record_event(
        gate.packet,
        RepairPacketEvent.EventType.GATE_CONFIRMED,
        actor=user,
        reason=note,
        metadata={'gate_id': gate.pk},
    )
    return True, ''


def verify_gate(gate: RepairPacketGate, user=None, note: str = '') -> tuple[bool, str]:
    """Record second-person verification for a gate."""
    if (
        gate.confirmed_by_id
        and user
        and gate.confirmed_by_id == getattr(user, 'pk', None)
    ):
        return False, 'Verifier must be different from confirmer'
    gate.verify(user=user, note=note)
    _record_event(
        gate.packet,
        RepairPacketEvent.EventType.GATE_VERIFIED,
        actor=user,
        reason=note,
        metadata={'gate_id': gate.pk},
    )
    return True, ''


def waive_gate(
    gate: RepairPacketGate, user=None, reason: str = '', authority: str = ''
) -> tuple[bool, str]:
    """Waive a gate with explicit metadata.

    High-risk template waivers create a safety approval and remain blocking until
    that approval is resolved/executed. Lower-risk gates waive directly.
    """
    if not reason:
        return False, 'Waiver reason is required'
    if not authority:
        return False, 'Waiver authority is required'

    if gate.template and gate.template.risk_tier >= 3:
        approval = create_safety_gate_approval(gate, user, reason, authority)
        return False, f'Safety approval required: {approval.pk}'

    gate.waive(user=user, reason=reason, authority=authority)
    _record_event(
        gate.packet,
        RepairPacketEvent.EventType.GATE_WAIVED,
        actor=user,
        reason=reason,
        metadata={'gate_id': gate.pk, 'authority': authority},
    )
    return True, ''


def create_safety_gate_approval(
    gate: RepairPacketGate, user=None, reason: str = '', authority: str = ''
):
    """Create/link a high-risk safety-gate approval request."""
    from approvals.models import ActionType, Approval, compute_idempotency_key

    run_id = gate.packet.agent_run_id or f'repair-{gate.packet_id}'
    tool_call_id = f'safety-gate-{gate.pk}-waive'
    idempotency_key = compute_idempotency_key(run_id, tool_call_id)
    approval, _ = Approval.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            'action_type': ActionType.SAFETY_GATE,
            'summary': f'Waive safety gate {gate.name} on {gate.packet.reference}',
            'payload': {
                'packet_id': gate.packet_id,
                'gate_id': gate.pk,
                'action': 'waive',
                'reason': reason,
                'authority': authority,
            },
            'agent_run_id': run_id,
            'agent_checkpoint_id': 'repair-safety',
            'tool_call_id': tool_call_id,
            'risk_tier': gate.template.risk_tier if gate.template else 3,
            'assigned_to_user': user
            if (user and getattr(user, 'is_authenticated', False))
            else None,
        },
    )
    RepairPacketApprovalLink.objects.get_or_create(
        packet=gate.packet, approval=approval, defaults={'purpose': 'safety'}
    )
    return approval


def upsert_lockout_point(
    gate: RepairPacketGate, data: dict[str, Any], user=None
) -> LockoutPoint:
    """Create or update a lockout point and stamp status-specific metadata."""
    point_id = data.get('pk') or data.get('id')
    fields = {
        'energy_source': data.get('energy_source', LockoutPoint.EnergySource.OTHER),
        'isolation_device': data.get('isolation_device', ''),
        'lock_id': data.get('lock_id', ''),
        'tag_id': data.get('tag_id', ''),
        'status': data.get('status', LockoutPoint.PointStatus.IDENTIFIED),
        'note': data.get('note', ''),
    }
    if point_id:
        point = gate.lockout_points.get(pk=point_id)
        for key, value in fields.items():
            if value != '':
                setattr(point, key, value)
    else:
        point = LockoutPoint(gate=gate, **fields)

    if point.status in (
        LockoutPoint.PointStatus.LOCKED,
        LockoutPoint.PointStatus.VERIFIED,
    ):
        point.applied_by = (
            user
            if (user and getattr(user, 'is_authenticated', False))
            else point.applied_by
        )
    if point.status == LockoutPoint.PointStatus.VERIFIED:
        point.verified_by = (
            user
            if (user and getattr(user, 'is_authenticated', False))
            else point.verified_by
        )
        point.verified_at = timezone.now()
    if point.status == LockoutPoint.PointStatus.RESTORED:
        point.restored_at = timezone.now()
    point.save()
    _record_event(
        gate.packet,
        RepairPacketEvent.EventType.LOCKOUT_UPDATED,
        actor=user,
        metadata={
            'gate_id': gate.pk,
            'lockout_point_id': point.pk,
            'status': point.status,
        },
    )
    return point


# --------------------------------------------------------------------------- #
# Approvals + revalidation
# --------------------------------------------------------------------------- #
def required_approvals_granted(packet: RepairPacket) -> bool:
    """Whether every approval linked to the packet is approved/succeeded.

    Packets with no linked approvals are considered clear.
    """
    from approvals.models import ApprovalStatus

    for link in packet.approval_links.select_related('approval').all():
        if link.approval.status not in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.SUCCEEDED,
        ):
            return False
    return True


def _revalidate_parts(packet: RepairPacket) -> tuple[bool, str]:
    """Re-run stock allocation on the work order's parts; fail closed if short."""
    wo = packet.work_order
    if wo is None:
        return True, ''

    shortages: list[str] = []
    for cp in wo.work_order_parts.all():
        try:
            cp.check_and_allocate()
        except Exception:
            continue
        if cp.allocation_status in ('insufficient', 'partial'):
            shortages.append(cp.part.name)

    if shortages:
        return False, f'Parts no longer fully available: {", ".join(shortages)}'
    return True, ''


# Extensible pre-execution revalidation checks (Drift Protection #17).
# Future coverage (quote validity, HOLD status, spend thresholds) plugs in here.
REVALIDATION_CHECKS = [_revalidate_parts]


def revalidate(packet: RepairPacket) -> tuple[bool, str]:
    """Run all pre-execution revalidation checks; fail closed on the first miss."""
    for check in REVALIDATION_CHECKS:
        ok, msg = check(packet)
        if not ok:
            return False, msg
    return True, ''


# --------------------------------------------------------------------------- #
# Lifecycle transitions
# --------------------------------------------------------------------------- #
def advance_packet(
    packet: RepairPacket, to: str, user=None, reason: str = ''
) -> tuple[bool, str]:
    """Attempt a lifecycle transition, enforcing FSM + gates + revalidation.

    Runs under ``select_for_update`` so concurrent transition attempts on the
    same packet cannot race, and records an audit event on success.
    """
    if not to:
        return False, 'No target status provided'

    with transaction.atomic():
        locked = RepairPacket.objects.select_for_update().get(pk=packet.pk)
        from_status = locked.status

        if not is_valid_packet_transition(from_status, to):
            return False, f'Illegal transition {from_status} -> {to}'

        if to == PacketStatus.APPROVED:
            ok, msg = locked.can_advance()
            if not ok:
                return False, msg
            if not required_approvals_granted(locked):
                return False, 'Spend / procurement approval pending'

        if to == PacketStatus.EXECUTING:
            ok, msg = locked.can_advance()
            if not ok:
                return False, msg
            ok, msg = revalidate(locked)
            if not ok:
                return False, msg

        if to == PacketStatus.CLOSED:
            ok, msg = locked.can_return_to_service()
            if not ok:
                return False, msg

            # Closing a packet that owns a work order must not bypass structured
            # work-order closeout, parts reconciliation, readings or machine
            # maintenance history. Those callers use close_repair_packet(),
            # which drives both aggregates in one transaction.
            if locked.work_order_id is not None:
                return (
                    False,
                    'This packet owns a work order; close it through the '
                    'repair closeout so the work order and machine history are '
                    'written in the same transaction.',
                )

        locked.status = to
        locked.save(update_fields=['status', 'updated_at'])

        event_type = (
            RepairPacketEvent.EventType.CANCELED
            if to == PacketStatus.CANCELED
            else RepairPacketEvent.EventType.ADVANCED
        )
        _record_event(
            locked,
            event_type,
            from_status=from_status,
            to_status=to,
            actor=user,
            reason=reason,
        )

    # Reflect the new state on the caller's instance.
    packet.status = to
    return True, ''


class RepairCloseoutError(Exception):
    """The packet cannot be finalized as requested."""

    code = 'REPAIR_CLOSEOUT_INVALID'


class RepairStartError(Exception):
    """The repair cannot be started as requested."""

    code = 'REPAIR_START_INVALID'


def repair_start_readiness(packet, *, actor) -> dict:
    """Explain whether this repair can be started, and what is stopping it.

    Read-only, and deliberately verbose about *why*: the machine page turns
    "Start repair" into "Review blockers" from this answer, so a technician sees
    the unresolved LOTO point or the missing part rather than a disabled button
    with no explanation.
    """
    from tasks.services.finalization import PacketFinalization
    from tasks.services.readiness import evaluate_work_order_readiness

    blockers: list[dict] = []
    work_order = packet.work_order

    if work_order is None:
        blockers.append({
            'code': 'NO_WORK_ORDER',
            'message': 'This packet has no work order to start.',
            'source': 'repair_packet',
        })
        return {
            'packet_id': packet.pk,
            'packet_reference': packet.reference,
            'packet_status': packet.status,
            'work_order_id': None,
            'work_order_reference': None,
            'lifecycle_status': None,
            'lifecycle_version': None,
            'ready': False,
            'blockers': blockers,
        }

    if not is_valid_packet_transition(packet.status, PacketStatus.EXECUTING):
        blockers.append({
            'code': 'PACKET_NOT_STARTABLE',
            'message': (
                f'A packet in state "{packet.get_status_display()}" cannot start '
                'execution.'
            ),
            'source': 'repair_packet',
        })

    # Safety keeps precedence over everything below it.
    can_advance, gate_message = packet.can_advance()
    if not can_advance:
        blockers.append({
            'code': 'SAFETY_GATE_BLOCKED',
            'message': gate_message,
            'source': 'safety',
        })

    if work_order.lifecycle_status != WorkOrderLifecycle.READY:
        blockers.append({
            'code': 'WORK_ORDER_NOT_READY',
            'message': (
                f'The work order is {work_order.get_lifecycle_status_display()}; '
                'it must be marked ready before work starts.'
            ),
            'source': 'work_order',
        })

    readiness = evaluate_work_order_readiness(
        work_order,
        action='start',
        actor=actor,
        expected_version=work_order.lifecycle_version,
        # This packet owns the work order, so it is the path that may start it.
        # The preview must evaluate the same way the command will, or the button
        # would offer work the command then refuses.
        packet_finalization=PacketFinalization(packet_id=packet.pk),
    )
    blockers.extend(
        {
            'code': blocker.code,
            'message': blocker.message,
            'source': blocker.source,
            'metadata': blocker.metadata,
        }
        for blocker in readiness.blockers
    )

    return {
        'packet_id': packet.pk,
        'packet_reference': packet.reference,
        'packet_status': packet.status,
        'work_order_id': work_order.pk,
        'work_order_reference': work_order.reference,
        'lifecycle_status': work_order.lifecycle_status,
        'lifecycle_version': work_order.lifecycle_version,
        'ready': not blockers,
        'blockers': blockers,
    }


@transaction.atomic
def start_repair_packet(
    packet: RepairPacket,
    *,
    actor,
    expected_version: int,
    idempotency_key: str,
    reason: str = '',
):
    """Start a packet-owned repair: work order and packet move together.

    Starting is a lifecycle transition, not a board edit. The machine page never
    reaches around this to set a card's column: doing so would start work with
    the safety gates, parts readiness and assignment checks unevaluated.

    Returns the work-order ``CommandResult``. Replaying the same
    ``idempotency_key`` returns the original result without transitioning twice.
    """
    from tasks.services.finalization import PacketFinalization
    from tasks.services.work_orders import transition_work_order

    locked = RepairPacket.objects.select_for_update().get(pk=packet.pk)

    if locked.work_order_id is None:
        raise RepairStartError('This packet has no work order to start.')

    if locked.status != PacketStatus.EXECUTING:
        if not is_valid_packet_transition(locked.status, PacketStatus.EXECUTING):
            raise RepairStartError(
                f'Illegal transition {locked.status} -> {PacketStatus.EXECUTING}'
            )
        ok, message = locked.can_advance()
        if not ok:
            raise RepairStartError(message)

    from_status = locked.status

    # The work-order command re-evaluates readiness itself and raises
    # ReadinessBlocked with the authoritative blocker list, so the two aggregates
    # cannot disagree about whether this repair was startable.
    result = transition_work_order(
        work_order_id=locked.work_order_id,
        to_status=WorkOrderLifecycle.IN_PROGRESS,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        reason=reason,
        packet_finalization=PacketFinalization(packet_id=locked.pk),
    )

    if locked.status != PacketStatus.EXECUTING:
        locked.status = PacketStatus.EXECUTING
        locked.save(update_fields=['status', 'updated_at'])
        _record_event(
            locked,
            RepairPacketEvent.EventType.ADVANCED,
            from_status=from_status,
            to_status=PacketStatus.EXECUTING,
            actor=actor,
            reason=reason,
            metadata={
                'work_order_id': locked.work_order_id,
                'correlation_id': str(result.correlation_id),
            },
        )

    packet.status = locked.status
    return result


@transaction.atomic
def close_repair_packet(
    packet: RepairPacket,
    *,
    actor,
    closeout: dict[str, Any],
    expected_version: int,
    idempotency_key: str,
    reason: str = '',
):
    """Finalize a packet-owned repair: one closeout, one history row, one commit.

    Standalone work orders complete through
    ``tasks.services.closeout.complete_work_order``. Packet-owned work must reach
    the same place, or a repair would close with no structured closeout, no parts
    reconciliation, no acceptance readings and no row in the machine's
    Maintenance blade. This function is that shared path: it drives the canonical
    work-order completion *and* the packet's return-to-service transition inside
    a single transaction, so the two aggregates can never disagree.

    Returns the work-order ``CommandResult``. Replaying the same
    ``idempotency_key`` returns the original result without writing again.
    """
    from tasks.services.closeout import complete_work_order
    from tasks.services.finalization import PacketFinalization

    locked = RepairPacket.objects.select_for_update().get(pk=packet.pk)
    from_status = locked.status

    if locked.work_order_id is None:
        raise RepairCloseoutError(
            'This packet has no work order, so it cannot create maintenance '
            'history. Link or generate one first.'
        )

    if locked.status == PacketStatus.CLOSED:
        # Already returned to service; fall through to the work-order command so
        # its idempotency ledger decides whether this is a replay or a conflict.
        pass
    elif not is_valid_packet_transition(from_status, PacketStatus.CLOSED):
        raise RepairCloseoutError(
            f'Illegal transition {from_status} -> {PacketStatus.CLOSED}'
        )

    ok, message = locked.can_return_to_service()
    if not ok:
        raise RepairCloseoutError(message)

    result = complete_work_order(
        work_order_id=locked.work_order_id,
        actor=actor,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        closeout=closeout,
        packet_finalization=PacketFinalization(packet_id=locked.pk),
    )

    if locked.status != PacketStatus.CLOSED:
        locked.status = PacketStatus.CLOSED
        locked.save(update_fields=['status', 'updated_at'])
        _record_event(
            locked,
            RepairPacketEvent.EventType.RETURN_TO_SERVICE,
            from_status=from_status,
            to_status=PacketStatus.CLOSED,
            actor=actor,
            reason=reason,
            metadata={
                'work_order_id': locked.work_order_id,
                'correlation_id': str(result.correlation_id),
            },
        )

    # Reflect the new state on the caller's instance.
    packet.status = locked.status
    return result


def run_generation_by_id(packet_id: int, params: dict[str, Any] | None = None) -> None:
    """Offload-friendly wrapper: load the packet by id, then generate.

    Used by :func:`InvenTree.tasks.offload_task` for asynchronous generation.
    """
    try:
        packet = RepairPacket.objects.get(pk=packet_id)
    except RepairPacket.DoesNotExist:
        return
    run_repair_packet_workflow(packet, params or {})


# --------------------------------------------------------------------------- #
# Authorized diagnostic reads
# --------------------------------------------------------------------------- #

_DIAGNOSTIC_CAPABILITIES = frozenset({
    'diagnostics.machine.read',
    'diagnostics.packet.read',
    'diagnostics.maintenance.read',
    'diagnostics.manuals.read',
    'diagnostics.playbooks.read',
    'diagnostics.parts.read',
    'diagnostics.safety_p0.read',
    # Machine health is its own grant: reading a machine's dossier and reading
    # its live industrial telemetry are different levels of access, and a
    # deployment must be able to grant one without the other.
    'diagnostics.health.read',
})
_DIAGNOSTIC_ABSTENTION = 'No authorized citation-ready evidence was available.'
_DIAGNOSTIC_SAFETY_ROW_LIMIT = 100
#: One health read returns a bounded view, not a historian dump.
_DIAGNOSTIC_HEALTH_SIGNAL_LIMIT = 50
_DIAGNOSTIC_PLAYBOOK_STEP_LIMIT = 50


def diagnostic_rehydrate_actor(user_pk):
    """Reload an active authenticated actor for a diagnostic read."""
    from django.contrib.auth import get_user_model
    from django.core.exceptions import ValidationError

    user_model = get_user_model()
    try:
        actor = user_model.objects.get(pk=user_pk, is_active=True)
    except (user_model.DoesNotExist, TypeError, ValidationError, ValueError):
        return None
    return actor if getattr(actor, 'is_authenticated', False) else None


def _diagnostic_capabilities_for_actor(actor) -> frozenset[str]:
    """Resolve current diagnostic grants through one deployment-owned seam."""
    from django.conf import settings as django_settings
    from django.utils.module_loading import import_string

    resolver = getattr(django_settings, 'AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER', None)
    if resolver is None:
        from ai.core.config import get_settings

        resolver = get_settings().diagnostic_capability_resolver
    if isinstance(resolver, str):
        resolver = resolver.strip()
        if not resolver:
            return frozenset()
        try:
            resolver = import_string(resolver)
        except (ImportError, AttributeError):
            return frozenset()
    if not callable(resolver):
        return frozenset()
    try:
        values = resolver(actor)
    except Exception:
        return frozenset()
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        value
        for value in values
        if isinstance(value, str) and value in _DIAGNOSTIC_CAPABILITIES
    )


def _diagnostic_revision(record) -> str:
    """Return a stable optimistic-read revision for a maintenance root."""
    updated_at = getattr(record, 'updated_at', None)
    if updated_at is None:
        return ''
    return updated_at.isoformat()


def diagnostic_capabilities_for_actor(actor) -> frozenset[str]:
    """Public: the diagnostic capability grants currently held by an actor."""
    return _diagnostic_capabilities_for_actor(actor)


def list_diagnostic_record_roots(
    actor, *, machine_limit: int = 50, packet_limit: int = 50
) -> list[dict]:
    """Record roots this actor may read for a diagnostic turn.

    Returns the active machines in the actor's maintenance (customer) scope plus
    their non-terminal repair packets, each with its current optimistic-read
    revision. Fail-closed: an unauthenticated/unscoped actor or a scope error
    yields an empty list, so the reasoning path exposes no diagnostic tools.
    """
    from tasks.scope import ScopeError, scope_for_actor

    from assets.models import AssetMachine
    from repair.models import TERMINAL_PACKET_STATUSES

    try:
        scopes = scope_for_actor(actor)
    except ScopeError:
        return []
    customer_ids = {
        scope.customer_id for scope in scopes if scope.customer_id is not None
    }
    if not customer_ids:
        return []

    roots: list[dict] = []
    machine_ids: list[int] = []
    machines = (
        AssetMachine.objects
        .filter(customer_id__in=customer_ids, active=True)
        .only('pk', 'updated_at')
        .order_by('pk')[:machine_limit]
    )
    for machine in machines:
        revision = _diagnostic_revision(machine)
        if not revision:
            continue
        machine_ids.append(machine.pk)
        roots.append({
            'entity_type': 'machine',
            'entity_id': machine.pk,
            'expected_revision': revision,
            'linked_machine_id': None,
            'authorization_class': 'maintenance_scope',
        })

    if machine_ids:
        packets = (
            RepairPacket.objects
            .filter(machine_id__in=machine_ids)
            .exclude(status__in=TERMINAL_PACKET_STATUSES)
            .only('pk', 'machine_id', 'updated_at')
            .order_by('-updated_at')[:packet_limit]
        )
        for packet in packets:
            revision = _diagnostic_revision(packet)
            if not revision or not packet.machine_id:
                continue
            roots.append({
                'entity_type': 'repair_packet',
                'entity_id': packet.pk,
                'expected_revision': revision,
                'linked_machine_id': packet.machine_id,
                'authorization_class': 'maintenance_scope',
            })
    return roots


def _diagnostic_scoped_entity(actor, entity_type: str, entity_id: int):
    """Load only fields required to decide the entity ACL."""
    from tasks.scope import MaintenanceScope, ScopeError, scope_for_actor

    try:
        authorized_scopes = scope_for_actor(actor)
    except ScopeError:
        return None

    if entity_type == 'machine':
        from assets.models import AssetMachine

        try:
            entity = AssetMachine.objects.only('pk', 'client_id', 'updated_at').get(
                pk=entity_id
            )
        except AssetMachine.DoesNotExist:
            return None
        client_id = entity.client_id
    elif entity_type == 'repair_packet':
        try:
            entity = (
                RepairPacket.objects
                .select_related('machine')
                .only(
                    'pk',
                    'machine_id',
                    'machine__pk',
                    'machine__client_id',
                    'updated_at',
                )
                .get(pk=entity_id)
            )
        except RepairPacket.DoesNotExist:
            return None
        client_id = entity.machine.client_id if entity.machine_id else None
    else:
        return None

    if client_id is None:
        return None
    required_scope = MaintenanceScope(
        customer_id=None, site_key=None, client_id=client_id
    )
    if required_scope not in authorized_scopes:
        return None
    return entity


def authorize_diagnostic_read(
    *,
    actor,
    capability: str,
    entity_type: str,
    entity_id: int,
    expected_revision: str,
    linked_machine_id: int | None,
    check_id: str,
):
    """Authorize a root without retrieving any user-facing record content."""
    if (
        actor is None
        or not getattr(actor, 'is_authenticated', False)
        or not getattr(actor, 'is_active', False)
        or capability not in _DIAGNOSTIC_CAPABILITIES
    ):
        return None

    if capability not in _diagnostic_capabilities_for_actor(actor):
        return None

    entity = _diagnostic_scoped_entity(actor, entity_type, entity_id)
    if entity is None:
        return None
    current_revision = _diagnostic_revision(entity)
    if not current_revision or current_revision != expected_revision:
        return None

    actual_linked_machine_id = (
        entity.pk if entity_type == 'machine' else entity.machine_id
    )
    expected_linked_machine_id = None if entity_type == 'machine' else linked_machine_id
    if entity_type == 'repair_packet' and (
        expected_linked_machine_id is None
        or actual_linked_machine_id != expected_linked_machine_id
    ):
        return None

    return {
        'check_id': check_id,
        'actor_id': f'user:{actor.pk}',
        'capability': capability,
        'entity_type': entity_type,
        'entity_id': entity.pk,
        'current_revision': current_revision,
        'authorization_class': 'maintenance_scope',
        'scoped': True,
        'linked_machine_id': expected_linked_machine_id,
        'checked_at': timezone.now(),
    }


def _diagnostic_reauthorize(actor, authorization, expected_revision: str) -> bool:
    """Repeat the ACL and revision check immediately before content retrieval."""
    decision = authorize_diagnostic_read(
        actor=actor,
        capability=authorization.capability,
        entity_type=authorization.entity_type,
        entity_id=authorization.entity_id,
        expected_revision=expected_revision,
        linked_machine_id=authorization.linked_machine_id,
        check_id=authorization.check_id,
    )
    return decision is not None and decision['actor_id'] == authorization.actor_id


def _diagnostic_result(*evidence, reason: str = '') -> dict[str, Any]:
    """Return the strict transfer shape consumed by the AI facade."""
    return {
        'evidence': tuple(evidence),
        'abstention_reason': reason if not evidence else '',
    }


def _diagnostic_evidence(
    *,
    source_type: str,
    source_id,
    revision: str,
    locator: str,
    as_of,
    claim: str,
    untrusted: bool,
) -> dict[str, Any]:
    """Create a citation-ready service transfer object."""
    return {
        'source_type': source_type,
        'id': str(source_id),
        'revision': revision,
        'locator': locator,
        'as_of': as_of,
        'authorization_class': 'maintenance_scope',
        'claim': claim,
        'untrusted': untrusted,
    }


def read_diagnostic_machine_context(
    *, actor, authorization, machine_id: int, expected_revision: str
) -> dict[str, Any]:
    """Read an authorized machine snapshot after a repeated ACL check."""
    if (
        authorization.entity_type != 'machine'
        or authorization.entity_id != machine_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)

    from assets.models import AssetMachine

    try:
        machine = AssetMachine.objects.get(pk=machine_id)
    except AssetMachine.DoesNotExist:
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)
    claim = json.dumps(
        {
            'name': machine.name,
            'description': machine.description,
            'manufacturer': machine.manufacturer,
            'model': machine.model,
            'serial': machine.serial,
            'location': machine.location,
            'active': machine.active,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    return _diagnostic_result(
        _diagnostic_evidence(
            source_type='asset_machine',
            source_id=machine.pk,
            revision=_diagnostic_revision(machine),
            locator=f'/machines/{machine.pk}',
            as_of=machine.updated_at,
            claim=claim,
            untrusted=True,
        )
    )


def read_diagnostic_repair_packet(
    *, actor, authorization, repair_packet_id: int, expected_revision: str
) -> dict[str, Any]:
    """Read an authorized packet with all control and safety fields omitted."""
    if (
        authorization.entity_type != 'repair_packet'
        or authorization.entity_id != repair_packet_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)
    try:
        packet = RepairPacket.objects.only(
            'pk',
            'reference',
            'status',
            'fault_summary',
            'symptom',
            'criticality',
            'production_impact',
            'machine_id',
            'updated_at',
        ).get(pk=repair_packet_id)
    except RepairPacket.DoesNotExist:
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)
    claim = json.dumps(
        {
            'reference': packet.reference,
            'status': packet.status,
            'fault_summary': packet.fault_summary,
            'symptom': packet.symptom,
            'criticality': packet.criticality,
            'production_impact': packet.production_impact,
            'machine_id': packet.machine_id,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    return _diagnostic_result(
        _diagnostic_evidence(
            source_type='repair_packet_redacted',
            source_id=packet.pk,
            revision=_diagnostic_revision(packet),
            locator=f'/repair/packets/{packet.pk}',
            as_of=packet.updated_at,
            claim=claim,
            untrusted=True,
        )
    )


def read_diagnostic_maintenance_history(
    *, actor, authorization, machine_id: int, expected_revision: str, limit: int
) -> dict[str, Any]:
    """Read recent maintenance records only after machine authorization."""
    if (
        authorization.entity_type != 'machine'
        or authorization.entity_id != machine_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)

    from assets.models import AssetMaintenanceRecord

    records = AssetMaintenanceRecord.objects.filter(machine_id=machine_id).order_by(
        '-date', '-pk'
    )[:limit]
    evidence = []
    for record in records:
        claim = json.dumps(
            {
                'date': record.date.isoformat(),
                'summary': record.summary,
                'details': record.details,
                'performed_by': record.performed_by,
                'work_order_id': record.work_order_id,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        evidence.append(
            _diagnostic_evidence(
                source_type='asset_maintenance_record',
                source_id=record.pk,
                revision=_diagnostic_revision(record),
                locator=f'/machines/{machine_id}/maintenance/{record.pk}',
                as_of=record.updated_at,
                claim=claim,
                untrusted=True,
            )
        )
    return _diagnostic_result(*evidence, reason=_DIAGNOSTIC_ABSTENTION)


def read_diagnostic_health_summary(
    *, actor, authorization, machine_id: int, expected_revision: str
) -> dict[str, Any]:
    """Read the machine's current normalized condition.

    Every claim carries the freshness and quality of the data behind it, and a
    stale or unmapped machine is reported as such. A model summarising this must
    not be able to present hours-old telemetry as the current state, so the
    staleness travels with the observation rather than being left implicit.
    """
    if (
        authorization.entity_type != 'machine'
        or authorization.entity_id != machine_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)

    from assets.models import AssetMachine
    from machine_health.services.summary import health_summary, signal_rows

    machine = AssetMachine.objects.filter(pk=machine_id).first()
    if machine is None:
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)

    summary = health_summary(machine)
    now = timezone.now()

    claim = json.dumps(
        {
            'state': summary['state'],
            'configured': summary['configured'],
            'signal_count': summary['signal_count'],
            'stale_signal_count': summary['stale_signal_count'],
            'degraded_data': summary['degraded_data'],
            'active_anomaly_count': summary['active_anomaly_count'],
            'last_observed_at': (
                summary['last_observed_at'].isoformat()
                if summary['last_observed_at']
                else None
            ),
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    evidence = [
        _diagnostic_evidence(
            source_type='machine_health_summary',
            source_id=machine_id,
            revision=_diagnostic_revision(machine),
            locator=f'/machines/{machine_id}/health/',
            as_of=now,
            claim=claim,
            untrusted=False,
        )
    ]

    for row in signal_rows(machine, now=now)[:_DIAGNOSTIC_HEALTH_SIGNAL_LIMIT]:
        signal_claim = json.dumps(
            {
                'signal': row['display_name'],
                'value': row['value'],
                'unit': row['unit'],
                'quality': row['quality'],
                'stale': row['stale'],
                'state': row['state'],
                'observed_at': (
                    row['observed_at'].isoformat() if row['observed_at'] else None
                ),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        evidence.append(
            _diagnostic_evidence(
                source_type='machine_signal_state',
                source_id=row['binding_id'],
                revision=(row['observed_at'].isoformat() if row['observed_at'] else ''),
                locator=f'/machines/{machine_id}/health/signals/{row["binding_id"]}',
                as_of=row['observed_at'] or now,
                claim=signal_claim,
                # Values originate in an external control system; treat their
                # labels as untrusted content, not instructions.
                untrusted=True,
            )
        )

    return _diagnostic_result(*evidence, reason=_DIAGNOSTIC_ABSTENTION)


def read_diagnostic_health_anomalies(
    *, actor, authorization, machine_id: int, expected_revision: str, limit: int
) -> dict[str, Any]:
    """Read the machine's active anomalies with their detector provenance.

    The detector and its version travel with each anomaly so a summary can say
    *what* raised it. Detection is deterministic elsewhere; nothing read here
    lets a model raise, escalate or clear a condition.
    """
    if (
        authorization.entity_type != 'machine'
        or authorization.entity_id != machine_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)

    from assets.health_models import ACTIVE_ANOMALY_STATUSES, MachineAnomaly

    anomalies = MachineAnomaly.objects.filter(
        machine_id=machine_id,
        status__in=[status.value for status in ACTIVE_ANOMALY_STATUSES],
    ).order_by('-last_observed_at')[:limit]

    evidence = []
    for anomaly in anomalies:
        claim = json.dumps(
            {
                'title': anomaly.title,
                'severity': anomaly.severity,
                'status': anomaly.status,
                'alarm_code': anomaly.alarm_code,
                'detector': anomaly.detector,
                'detector_version': anomaly.detector_version,
                'evidence_summary': anomaly.evidence_summary,
                'first_observed_at': anomaly.first_observed_at.isoformat(),
                'last_observed_at': anomaly.last_observed_at.isoformat(),
                'work_order_id': anomaly.work_order_id,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        evidence.append(
            _diagnostic_evidence(
                source_type='machine_anomaly',
                source_id=anomaly.pk,
                revision=_diagnostic_revision(anomaly),
                locator=f'/machines/{machine_id}/health/anomalies/{anomaly.pk}',
                as_of=anomaly.updated_at,
                claim=claim,
                untrusted=True,
            )
        )

    return _diagnostic_result(*evidence, reason=_DIAGNOSTIC_ABSTENTION)


def read_diagnostic_approved_manuals(
    *,
    actor,
    authorization,
    machine_id: int,
    expected_revision: str,
    query: str,
    limit: int,
) -> dict[str, Any]:
    """Abstain until the attachment domain has explicit manual approval state."""
    del query, limit
    if (
        authorization.entity_type != 'machine'
        or authorization.entity_id != machine_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)
    return _diagnostic_result(
        reason='No explicitly approved manual source is configured for this machine.'
    )


def read_diagnostic_published_playbooks(
    *,
    actor,
    authorization,
    machine_id: int,
    expected_revision: str,
    query: str,
    limit: int,
) -> dict[str, Any]:
    """Read published procedures with an explicit applicability edge to the machine."""
    if (
        authorization.entity_type != 'machine'
        or authorization.entity_id != machine_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)

    from django.db.models import Q

    from tasks.procedure_models import ProcedureRevision, ProcedureRevisionStatus

    # Machines carry no customer identity, so the only procedures an asset can
    # prove membership of are the customer-less (internal) ones. Customer
    # procedures never leak through an applicability edge.
    revisions = (
        ProcedureRevision.objects
        .filter(
            status=ProcedureRevisionStatus.PUBLISHED,
            procedure__active=True,
            applicability_rules__machine_id=machine_id,
            procedure__customer__isnull=True,
        )
        .filter(
            Q(procedure__code__icontains=query)
            | Q(procedure__name__icontains=query)
            | Q(procedure__description__icontains=query)
            | Q(steps__title__icontains=query)
            | Q(steps__instruction__icontains=query)
        )
        .select_related('procedure')
        .distinct()[:limit]
    )
    evidence = []
    for revision in revisions:
        selected_steps = list(
            revision.steps.order_by('sequence')[: _DIAGNOSTIC_PLAYBOOK_STEP_LIMIT + 1]
        )
        steps_truncated = len(selected_steps) > _DIAGNOSTIC_PLAYBOOK_STEP_LIMIT
        steps = [
            {
                'sequence': step.sequence,
                'type': step.step_type,
                'title': step.title,
                'instruction': step.instruction,
            }
            for step in selected_steps[:_DIAGNOSTIC_PLAYBOOK_STEP_LIMIT]
        ]
        claim = json.dumps(
            {
                'code': revision.procedure.code,
                'name': revision.procedure.name,
                'description': revision.procedure.description,
                'work_order_type': revision.work_order_type,
                'steps': steps,
                'steps_truncated': steps_truncated,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        evidence.append(
            _diagnostic_evidence(
                source_type='published_procedure_revision',
                source_id=revision.pk,
                revision=(
                    f'{revision.revision}:{revision.content_hash}'
                    if revision.content_hash
                    else f'{revision.revision}:{revision.content_version}'
                ),
                locator=(
                    f'/maintenance/procedures/{revision.procedure_id}'
                    f'/revisions/{revision.pk}'
                ),
                as_of=revision.published_at,
                claim=claim,
                untrusted=True,
            )
        )
    return _diagnostic_result(*evidence, reason=_DIAGNOSTIC_ABSTENTION)


def read_diagnostic_parts_availability(
    *,
    actor,
    authorization,
    machine_id: int,
    expected_revision: str,
    part_ids,
    limit: int,
) -> dict[str, Any]:
    """Observe linked-part availability without reserving or allocating stock."""
    if (
        authorization.entity_type != 'machine'
        or authorization.entity_id != machine_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)

    from assets.models import MachinePart

    links = MachinePart.objects.filter(machine_id=machine_id).select_related('part')
    if part_ids:
        links = links.filter(part_id__in=part_ids)
    links = links.order_by('part_id')[:limit]
    observed_at = timezone.now()
    evidence = []
    for link in links:
        part = link.part
        claim = json.dumps(
            {
                'part_id': part.pk,
                'name': part.name,
                'ipn': part.IPN,
                'required_quantity': link.quantity,
                'available_quantity': str(part.available_stock),
                'units': part.units,
                'observation_only': True,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        evidence.append(
            _diagnostic_evidence(
                source_type='part_availability_observation',
                source_id=part.pk,
                revision=part.revision or str(part.pk),
                locator=f'/part/{part.pk}',
                as_of=observed_at,
                claim=claim,
                untrusted=True,
            )
        )
    return _diagnostic_result(*evidence, reason=_DIAGNOSTIC_ABSTENTION)


def read_diagnostic_live_safety_status(
    *, actor, authorization, repair_packet_id: int, expected_revision: str
) -> dict[str, Any]:
    """Return raw command-side status coverage without an operational inference."""
    if (
        authorization.entity_type != 'repair_packet'
        or authorization.entity_id != repair_packet_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)
    from django.db import connection

    with transaction.atomic():
        # PostgreSQL otherwise uses READ COMMITTED and the two bounded child
        # queries could observe different moments. This read-only transaction
        # fixes one repeatable snapshot without acquiring business-row locks.
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        try:
            packet = RepairPacket.objects.only('pk', 'status', 'updated_at').get(
                pk=repair_packet_id
            )
        except RepairPacket.DoesNotExist:
            return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)
        if _diagnostic_revision(packet) != expected_revision:
            return _diagnostic_result(reason=_DIAGNOSTIC_ABSTENTION)

        gates = list(
            packet.gates.order_by('sequence', 'pk').values('pk', 'gate_type', 'status')[
                : _DIAGNOSTIC_SAFETY_ROW_LIMIT + 1
            ]
        )
        points = list(
            LockoutPoint.objects
            .filter(gate__packet_id=packet.pk)
            .order_by('pk')
            .values('pk', 'gate_id', 'energy_source', 'status')[
                : _DIAGNOSTIC_SAFETY_ROW_LIMIT + 1
            ]
        )
        if (
            len(gates) > _DIAGNOSTIC_SAFETY_ROW_LIMIT
            or len(points) > _DIAGNOSTIC_SAFETY_ROW_LIMIT
        ):
            return _diagnostic_result(
                reason=(
                    'Raw command-side status exceeded the bounded safety reader; '
                    'check the authoritative safety surface.'
                )
            )

        observed_at = timezone.now()
        snapshot = {
            'packet_status': packet.status,
            'gate_statuses': gates,
            'lockout_point_statuses': points,
            'coverage': {'gate_count': len(gates), 'lockout_point_count': len(points)},
            'caveat': (
                'Raw recorded command-side states only; verify field conditions and '
                'authoritative controls before action.'
            ),
        }
        claim = json.dumps(snapshot, ensure_ascii=True, sort_keys=True)
        snapshot_revision = hashlib.sha256(
            json.dumps(
                {'packet_revision': _diagnostic_revision(packet), 'snapshot': snapshot},
                ensure_ascii=True,
                separators=(',', ':'),
                sort_keys=True,
            ).encode('utf-8')
        ).hexdigest()
        return _diagnostic_result(
            _diagnostic_evidence(
                source_type='repair_packet_command_status',
                source_id=packet.pk,
                revision=f'sha256:{snapshot_revision}',
                locator=f'/repair/packets/{packet.pk}/command-status',
                as_of=observed_at,
                claim=claim,
                untrusted=False,
            )
        )
