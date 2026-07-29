"""Authorized, allow-listed maintenance read projections for the AI rails.

The single module every AI surface goes through to read work orders and their
repair state. Mirrors ``assets/ai_read.py`` and inherits its three rules:

**The page is not the source.** Every field is re-read from the ORM under the
acting user's authority on every call; browser content never selects a record.

**The REST layer is not the source either.** ``repair.api`` authorizes on the
``work_order`` role with no tenant narrowing (``RepairPacket.objects.all()``),
and ``kanban_tools`` historically read ``WorkOrder.objects.all()``. Everything
below reads models directly behind ``tasks.scope`` -- the same predicate the
canonical work-order API applies.

**Projections are allow-lists, not filters.** Each returns a literal dict of
named fields; deliberate exclusions are listed in ``EXCLUDED_FIELDS`` and
pinned by tests. Operator- and machine-authored free text is prompt-injection
surface and is wrapped in the shared fence markers.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from .models import WorkOrder
from .scope import (
    ScopeError,
    require_machine_scope,
    require_work_order_scope,
    work_order_scope_filter,
)

#: Bounds on every list projection: a prompt gets a readable page, never a dump.
MAX_SEARCH_RESULTS = 25
MAX_FINDINGS = 25
MAX_SCOPE_LINES = 25
MAX_PARTS = 50
MAX_OPEN_REPAIRS = 10
MAX_TEXT_CHARS = 2000

#: Fields deliberately withheld from every projection, with the reason. Pinned
#: by ``tasks/tests/test_ai_read.py`` so removing an exclusion is a decision.
EXCLUDED_FIELDS = {
    'WorkOrder.customer': 'tenant identity, mirrors the Client.name exclusion',
    'WorkOrder.description': 'free text; the reviewed snapshot omits it',
    'WorkOrder.service_quote': 'commercial value',
    'WorkOrder.company_contact_phone': 'personal data',
    'RepairPacket.diagnosis': 'bulk generated JSON; only generation_status shows',
    'RepairPacketEvidence.*': 'operator-captured evidence values',
    'RepairPacketGate.evidence': 'gate readings and photos',
    'RepairPacket.agent_run_id': 'internal generation coordinates',
    'RepairPacket.created_by': 'author identity, not maintenance data',
    'Approval.*': 'approvals payload travels its own governed rail',
    'Attachment.attachment': 'file body / storage path',
}

#: Fence markers, byte-identical across every AIMMS surface. Redeclared rather
#: than imported for the same reason as ``assets/ai_read.py``: this module is
#: loaded by a Django app and must not couple to the ``ai`` package.
UNTRUSTED_CONTENT_BEGIN = '[UNTRUSTED-CONTENT-BEGIN]'
UNTRUSTED_CONTENT_END = '[UNTRUSTED-CONTENT-END]'
_ESCAPED_UNTRUSTED_MARKER = '[UNTRUSTED-CONTENT-MARKER-ESCAPED]'


def fence(value: str | None, *, limit: int = MAX_TEXT_CHARS) -> str:
    """Wrap stored free text so a model reads it as data, never instructions."""
    text = (value or '').strip()
    if not text:
        return ''
    if len(text) > limit:
        text = f'{text[:limit]}…'
    text = text.replace(UNTRUSTED_CONTENT_BEGIN, _ESCAPED_UNTRUSTED_MARKER)
    text = text.replace(UNTRUSTED_CONTENT_END, _ESCAPED_UNTRUSTED_MARKER)
    return f'{UNTRUSTED_CONTENT_BEGIN}\n{text}\n{UNTRUSTED_CONTENT_END}'


def _iso(value) -> str | None:
    """Render a datetime/date for a prompt without assuming a timezone."""
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def maintenance_ai_read_enabled() -> bool:
    """Whether the maintenance AI read surface is switched on (fail closed).

    Enforced here rather than only in the capability guard, because the guard
    covers one workflow while this module is importable from anywhere.
    """
    return bool(getattr(settings, 'AIMMS_MAINTENANCE_AI_READ_ENABLED', False))


def authorized_work_order(user, work_order_id):
    """Load one work order scope-safely; denial never discloses existence."""
    if not maintenance_ai_read_enabled():
        return None
    if not getattr(user, 'is_authenticated', False):
        return None

    try:
        pk = int(work_order_id)
    except (TypeError, ValueError):
        return None

    work_order = (
        WorkOrder.objects.select_related('machine', 'assigned_to').filter(pk=pk).first()
    )
    if work_order is None:
        return None
    try:
        require_work_order_scope(user, work_order)
    except ScopeError:
        return None
    return work_order


def work_orders_in_scope(user, *, query: str | None = None, limit: int = 10):
    """Return the actor's work orders, optionally narrowed by a search hint.

    The hint is only a hint: the authority is ``work_order_scope_filter``, so
    an unmatched or foreign reference yields nothing rather than reaching
    another tenant's job.
    """
    if not maintenance_ai_read_enabled():
        return []
    if not getattr(user, 'is_authenticated', False):
        return []

    try:
        predicate = work_order_scope_filter(user)
    except ScopeError:
        return []

    rows = WorkOrder.objects.filter(predicate).select_related('machine')
    if query:
        from django.db.models import Q

        term = str(query).strip()[:100]
        if term:
            rows = rows.filter(
                Q(reference__icontains=term)
                | Q(title__icontains=term)
                | Q(machine__name__icontains=term)
            )
    bounded = max(1, min(int(limit or 10), MAX_SEARCH_RESULTS))
    return list(rows.order_by('-created_at')[:bounded])


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------


def work_order_row(work_order) -> dict[str, Any]:
    """A disambiguating identity line for reference resolution."""
    return {
        'work_order_id': work_order.pk,
        'reference': work_order.reference or '',
        'title': fence(work_order.title, limit=255),
        'lifecycle_status': work_order.lifecycle_status,
        'work_order_type': work_order.work_order_type,
        'priority': work_order.priority,
        'machine': fence(work_order.machine.name, limit=255)
        if work_order.machine_id
        else None,
        'due_date': _iso(work_order.due_date),
    }


def work_order_overview(work_order) -> dict[str, Any]:
    """The reviewed snapshot plus schedule, machine link and part readiness."""
    parts = []
    lines = work_order.work_order_parts.select_related('part').order_by('pk')
    for line in lines[:MAX_PARTS]:
        allocation = line.check_and_allocate(persist=False)
        parts.append({
            'part_id': line.part_id,
            'part_name': fence(line.part.name, limit=255),
            'quantity': float(line.quantity),
            'quantity_available': allocation['quantity_available'],
            'allocation_status': allocation['allocation_status'],
        })

    return {
        'work_order_id': work_order.pk,
        'reference': work_order.reference or '',
        'title': fence(work_order.title, limit=255),
        'lifecycle_status': work_order.lifecycle_status,
        'work_order_type': work_order.work_order_type,
        'priority': work_order.priority,
        'lifecycle_version': work_order.lifecycle_version,
        'machine': {
            'machine_id': work_order.machine_id,
            'name': fence(work_order.machine.name, limit=255),
        }
        if work_order.machine_id
        else None,
        'assigned_to': (
            work_order.assigned_to.get_username() if work_order.assigned_to_id else None
        ),
        'due_date': _iso(work_order.due_date),
        'scheduled_start': _iso(work_order.scheduled_start),
        'scheduled_end': _iso(work_order.scheduled_end),
        'estimated_minutes': work_order.estimated_minutes,
        'parts': parts,
        'parts_truncated': lines.count() > MAX_PARTS,
    }


def work_order_readiness(user, work_order, *, action: str = 'start') -> dict[str, Any]:
    """The live readiness evaluator envelope, unchanged.

    The tool reports only what the active check registry actually emitted; it
    never infers declared-but-unregistered blockers.
    """
    from dataclasses import asdict

    from .services.readiness import evaluate_work_order_readiness

    readiness = evaluate_work_order_readiness(work_order, action=action, actor=user)
    envelope = asdict(readiness)
    envelope['evaluated_at'] = readiness.evaluated_at.isoformat()
    return envelope


def work_order_repair_state(work_order) -> dict[str, Any]:
    """The linked repair packet: identity, findings, approved scope, gates.

    ``repair`` is imported at call time: it already imports ``tasks``, and a
    module-level import here would be circular.
    """
    from repair.models import ApprovedRepairScope, RepairPacket

    packet = RepairPacket.objects.filter(work_order=work_order).order_by('pk').first()
    if packet is None:
        return {'work_order_id': work_order.pk, 'packet': None}

    findings = [
        {
            'finding_key': finding.finding_key,
            'sequence': finding.sequence,
            'category': finding.category,
            'observation': fence(finding.observation),
            'value': float(finding.value) if finding.value is not None else None,
            'unit': finding.unit or None,
            'evidence_source': fence(finding.evidence_source, limit=255),
            'observed_at': _iso(finding.observed_at),
            'verification': finding.verification,
        }
        for finding in packet.findings.order_by('sequence', 'pk')[:MAX_FINDINGS]
    ]

    scope_row = None
    current_scope = (
        ApprovedRepairScope.objects
        .filter(packet=packet, superseded_at__isnull=True)
        .order_by('-version')
        .first()
    )
    if current_scope is not None:
        scope_row = {
            'version': current_scope.version,
            'verified_cause': fence(current_scope.verified_cause),
            'scope_lines': [
                fence(str(line.get('action', '')), limit=500)
                for line in (current_scope.scope_lines or [])[:MAX_SCOPE_LINES]
            ],
            'failure_codes': list(current_scope.failure_codes or []),
            'crew_size': current_scope.crew_size,
            'planned_elapsed_minutes': current_scope.planned_elapsed_minutes,
            'approved_at': _iso(current_scope.approved_at),
        }

    gates = packet.gates.order_by('sequence')
    # unsatisfied_blocking_gates() yields (gate, reason) pairs; can_advance()
    # returns (bool, message). Both are unpacked here so a blocked packet
    # serializes as honest JSON -- a (False, "...") tuple is truthy.
    unsatisfied = [
        fence(gate.name, limit=255)
        for gate, _reason in packet.unsatisfied_blocking_gates()
    ]
    can_advance, _blocked_reason = packet.can_advance()

    return {
        'work_order_id': work_order.pk,
        'packet': {
            'packet_id': packet.pk,
            'reference': packet.reference or '',
            'status': packet.status,
            'criticality': packet.criticality,
            'generation_status': packet.generation_status,
            'fault_summary': fence(packet.fault_summary),
            'symptom': fence(packet.symptom),
            'production_impact': fence(packet.production_impact),
        },
        'findings': findings,
        'findings_truncated': packet.findings.count() > MAX_FINDINGS,
        'approved_scope': scope_row,
        'gates': {
            'total': gates.count(),
            'unsatisfied_blocking': unsatisfied,
            'can_advance': can_advance,
        },
    }


def open_repairs_for_machine(user, machine) -> dict[str, Any]:
    """Non-terminal repairs for one authorized machine, with start readiness.

    The REST ``MachineOpenRepairs`` view does no tenant check; this projection
    exists so the AI rail never inherits that gap.
    """
    from repair.models import PacketStatus, RepairPacket
    from repair.services import repair_start_readiness

    terminal = (PacketStatus.CLOSED, PacketStatus.CANCELED)
    packets = (
        RepairPacket.objects
        .filter(machine=machine)
        .exclude(status__in=terminal)
        .select_related('work_order')
        .order_by('-created_at')[:MAX_OPEN_REPAIRS]
    )

    repairs = []
    for packet in packets:
        readiness = repair_start_readiness(packet, actor=user)
        repairs.append({
            'packet_id': packet.pk,
            'reference': packet.reference or '',
            'status': packet.status,
            'criticality': packet.criticality,
            'fault_summary': fence(packet.fault_summary),
            'work_order_id': packet.work_order_id,
            'work_order_reference': (
                packet.work_order.reference if packet.work_order_id else None
            ),
            'ready': readiness.get('ready', False),
            'blockers': [
                {
                    'code': blocker.get('code'),
                    'message': fence(str(blocker.get('message', '')), limit=500),
                }
                for blocker in readiness.get('blockers', [])
            ],
        })

    return {'machine_id': machine.pk, 'repairs': repairs, 'total': len(repairs)}


def authorized_machine(user, machine_id):
    """Load one machine under the maintenance flag; denial stays silent.

    Deliberately gated on this module's own kill switch rather than borrowing
    ``assets.ai_read``'s, so the two surfaces switch independently.
    """
    if not maintenance_ai_read_enabled():
        return None
    if not getattr(user, 'is_authenticated', False):
        return None

    from assets.models import AssetMachine

    try:
        pk = int(machine_id)
    except (TypeError, ValueError):
        return None
    machine = AssetMachine.objects.filter(pk=pk).first()
    if machine is None:
        return None
    try:
        require_machine_scope(user, machine)
    except ScopeError:
        return None
    return machine


__all__ = [
    'EXCLUDED_FIELDS',
    'MAX_FINDINGS',
    'MAX_OPEN_REPAIRS',
    'MAX_PARTS',
    'MAX_SEARCH_RESULTS',
    'authorized_machine',
    'authorized_work_order',
    'fence',
    'maintenance_ai_read_enabled',
    'open_repairs_for_machine',
    'work_order_overview',
    'work_order_readiness',
    'work_order_repair_state',
    'work_order_row',
    'work_orders_in_scope',
]
