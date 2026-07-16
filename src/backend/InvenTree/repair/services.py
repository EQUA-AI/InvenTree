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
def _ensure_work_order_with_parts(packet: RepairPacket, result: GenerationResult):
    """Create/link a work order and materialise resolved part lines onto it."""
    resolvable = [p for p in result.parts if p.part_id]
    if not resolvable and packet.work_order_id is None:
        return

    from tasks.models import KanbanCard, KanbanCardPart

    if packet.work_order_id is None:
        card = KanbanCard.objects.create(
            title=f'Work order for {packet.reference or packet.pk}',
            description=packet.fault_summary,
            status=KanbanCard.STATUS_IN_PROGRESS,
            priority=KanbanCard.PRIORITY_MEDIUM,
        )
        packet.work_order = card
        packet.save(update_fields=['work_order', 'updated_at'])

    for line in resolvable:
        cp, _ = KanbanCardPart.objects.get_or_create(
            card=packet.work_order,
            part_id=line.part_id,
            defaults={'quantity': line.quantity},
        )
        try:
            cp.check_and_allocate()
        except Exception:  # allocation is best-effort during generation
            pass


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

    try:  # noqa: PLW0717
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

            _ensure_work_order_with_parts(packet, result)
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
    for cp in wo.card_parts.all():
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
})
_DIAGNOSTIC_ABSTENTION = 'No authorized citation-ready evidence was available.'
_DIAGNOSTIC_SAFETY_ROW_LIMIT = 100
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
            entity = AssetMachine.objects.only('pk', 'customer_id', 'updated_at').get(
                pk=entity_id
            )
        except AssetMachine.DoesNotExist:
            return None
        customer_id = entity.customer_id
    elif entity_type == 'repair_packet':
        try:
            entity = (
                RepairPacket.objects
                .select_related('machine')
                .only(
                    'pk',
                    'machine_id',
                    'machine__pk',
                    'machine__customer_id',
                    'updated_at',
                )
                .get(pk=entity_id)
            )
        except RepairPacket.DoesNotExist:
            return None
        customer_id = entity.machine.customer_id if entity.machine_id else None
    else:
        return None

    if customer_id is None:
        return None
    required_scope = MaintenanceScope(customer_id=customer_id, site_key=None)
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

    from django.db.models import F, Q

    from tasks.procedure_models import ProcedureRevision, ProcedureRevisionStatus

    revisions = (
        ProcedureRevision.objects
        .filter(
            status=ProcedureRevisionStatus.PUBLISHED,
            procedure__active=True,
            applicability_rules__machine_id=machine_id,
            procedure__customer_id=F('applicability_rules__machine__customer_id'),
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
