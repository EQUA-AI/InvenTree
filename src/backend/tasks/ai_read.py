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

#: Work-order timestamps a date window may mean (S7, §7.3). An allow-list so
#: the kwarg can never smuggle an ORM path; ``ai_analytics`` shares it.
PAGE_DATE_FIELDS = frozenset({'created_at', 'actual_completed_at', 'scheduled_start'})

#: Fields deliberately withheld from every projection, with the reason. Pinned
#: by ``tasks/tests/test_ai_read.py`` so removing an exclusion is a decision.
EXCLUDED_FIELDS = {
    'WorkOrder.customer': 'tenant identity, mirrors the Client.name exclusion',
    # A16/Q14 (S5b): 'WorkOrder.description' left this table by owner
    # decision — projected FENCED and capped in work_order_overview.
    # Embedded instructions remain untrusted data; the fence is the control.
    'WorkOrder.service_quote': 'commercial value',
    'WorkOrder.company_contact_phone': 'personal data',
    'RepairPacket.diagnosis': 'bulk generated JSON; only generation_status shows',
    'RepairPacketEvidence.*': 'operator-captured evidence values',
    'RepairPacketGate.evidence': 'gate readings and photos',
    'RepairPacket.agent_run_id': 'internal generation coordinates',
    'RepairPacket.created_by': 'author identity, not maintenance data',
    'Approval.*': 'approvals payload travels its own governed rail',
    'Attachment.attachment': 'file body / storage path',
    'AssetMaintenanceRecord.performed_by': 'free-text identity; role labels only',
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
    return work_orders_page(user, query=query, limit=limit)['rows']


def work_orders_page(
    user,
    *,
    query: str | None = None,
    limit: int = 10,
    scope_machine_ids=None,
    scope_date_from: str | None = None,
    scope_date_to: str | None = None,
    enforce: bool = False,
    date_field: str = 'created_at',
) -> dict[str, Any]:
    """The page-shaped work-order list with honest coverage (S5, §7.4).

    ``population_count`` is a real server-side COUNT over the authorized,
    filtered population; ``rows`` is the bounded display page. The two are
    never conflated — a page of 25 from 400 reports 400/25, not "25".

    The ``scope_*`` kwargs carry the thread's analysis scope (plain values —
    this module never imports ``ai.*``): with ``enforce`` the machine/date
    narrowing joins the query AFTER the authorization predicate; without it
    the page only counts how many returned rows fall outside the scope
    (shadow evidence). ``None`` scope means no analysis narrowing at all.

    ``date_field`` selects which event timestamp the date window means (S7,
    §7.3 domain defaults) from a validated allow-list; an unknown value falls
    back to ``created_at``. The applied field is always echoed in
    ``applied_filters`` so the answer can name it.
    """
    empty = {
        'rows': [],
        'population_count': 0,
        'returned_count': 0,
        'complete_population': True,
        'display_truncated': False,
        'out_of_scope_count': 0,
        'applied_filters': {},
        'high_watermark': None,
    }
    if not maintenance_ai_read_enabled():
        return empty
    if not getattr(user, 'is_authenticated', False):
        return empty

    try:
        predicate = work_order_scope_filter(user)
    except ScopeError:
        return empty

    if date_field not in PAGE_DATE_FIELDS:
        date_field = 'created_at'

    rows = WorkOrder.objects.filter(predicate).select_related('machine', 'assigned_to')
    applied_filters: dict[str, Any] = {'date_field': date_field}
    if query:
        from django.db.models import Q

        term = str(query).strip()[:100]
        if term:
            rows = rows.filter(
                Q(reference__icontains=term)
                | Q(title__icontains=term)
                | Q(machine__name__icontains=term)
            )
            applied_filters['query_applied'] = True
    scope_ids = (
        None if scope_machine_ids is None else {int(pk) for pk in scope_machine_ids}
    )
    if scope_ids is not None and enforce:
        # Analysis-scope narrowing is applied ON TOP of authorization,
        # never instead of it (the scope is narrowing, not authority).
        rows = rows.filter(machine_id__in=scope_ids)
        applied_filters['machine_ids'] = sorted(scope_ids)
        if scope_date_from:
            rows = rows.filter(**{f'{date_field}__date__gte': scope_date_from})
            applied_filters['from'] = scope_date_from
        if scope_date_to:
            rows = rows.filter(**{f'{date_field}__date__lt': scope_date_to})
            applied_filters['to'] = scope_date_to

    from django.db.models import Max

    population_count = rows.count()
    high_watermark = rows.aggregate(Max('updated_at'))['updated_at__max']
    bounded = max(1, min(int(limit or 10), MAX_SEARCH_RESULTS))
    page = list(rows.order_by('-created_at')[:bounded])

    out_of_scope = 0
    if scope_ids is not None and not enforce:
        out_of_scope = sum(
            1
            for wo in page
            if wo.machine_id is not None and wo.machine_id not in scope_ids
        )

    return {
        'rows': page,
        'population_count': population_count,
        'returned_count': len(page),
        'complete_population': len(page) == population_count,
        'display_truncated': len(page) < population_count,
        'out_of_scope_count': out_of_scope,
        'applied_filters': applied_filters,
        'high_watermark': _iso(high_watermark),
    }


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------


def _identity_label(identity, user_obj) -> str | None:
    """Render an FK identity per the S5b rule (Q15/A16).

    Role label by default; a caller-supplied ``identity(kind, subject)``
    pseudonymizer where sequence distinction matters. Never the username.
    """
    if user_obj is None:
        return None
    if identity is None:
        return 'technician'
    return identity('user', user_obj.pk)


def work_order_row(work_order, *, identity=None) -> dict[str, Any]:
    """A disambiguating identity line for reference resolution.

    ``board_status`` (the kanban column) and ``lifecycle_status`` (the
    governed execution machine) are DIFFERENT fields that both read as
    "status" in conversation. Projecting only the lifecycle made the AI
    answer "nothing is in progress" while the board showed five cards in
    the In Progress column (live finding, 2026-08-06) — the reader needs
    both to answer either meaning honestly.

    S5b additions: ``machine_id`` (a label alone was a dead end for the
    analytics rail) and the four analytics timestamps; ``assigned_to``
    became a role label/pseudonym (Q15 — identities are omitted by default).
    """
    return {
        'work_order_id': work_order.pk,
        'reference': work_order.reference or '',
        'title': fence(work_order.title, limit=255),
        'board_status': work_order.status,
        'lifecycle_status': work_order.lifecycle_status,
        'work_order_type': work_order.work_order_type,
        'priority': work_order.priority,
        'assigned': work_order.assigned_to_id is not None,
        'assigned_to': _identity_label(
            identity, work_order.assigned_to if work_order.assigned_to_id else None
        ),
        'machine_id': work_order.machine_id,
        'machine': fence(work_order.machine.name, limit=255)
        if work_order.machine_id
        else None,
        'due_date': _iso(work_order.due_date),
        'created_at': _iso(work_order.created_at),
        'updated_at': _iso(work_order.updated_at),
        'actual_started_at': _iso(work_order.actual_started_at),
        'actual_completed_at': _iso(work_order.actual_completed_at),
    }


def work_order_history(
    user, work_order, *, limit: int = 15, identity=None
) -> list[dict[str, Any]] | None:
    """Append-only audit events for one already-authorized work order.

    Requires ``tasks.view_workorder_audit`` on top of row scope — the same
    grant the REST audit surface enforces. A missing grant returns ``None``
    so the caller's refusal stays indistinguishable from a missing record.
    Event metadata is deliberately not projected: it can carry free-form
    payloads that were never written for model consumption.
    """
    from tasks.permissions import VIEW_WORKORDER_AUDIT
    from tasks.workorder_models import WorkOrderEvent

    if not getattr(user, 'is_authenticated', False):
        return None
    if not user.has_perm(VIEW_WORKORDER_AUDIT):
        return None

    bounded = max(1, min(int(limit), 50))
    events = (
        WorkOrderEvent.objects
        .filter(work_order=work_order)
        .select_related('actor')
        .order_by('-created_at')[:bounded]
    )
    return [
        {
            'event_type': event.event_type,
            'from_status': event.from_status or None,
            'to_status': event.to_status or None,
            # S5b (Q15): pseudonym/role, never the username. Event sequences
            # are where DISTINCTION matters ("the same person moved it
            # twice"), so callers pass a thread-stable pseudonymizer.
            'actor': _identity_label(identity, event.actor if event.actor_id else None),
            'reason': fence(event.reason, limit=500) if event.reason else None,
            'created_at': _iso(event.created_at),
        }
        for event in events
    ]


def work_order_history_page(
    user, work_order, *, limit: int = 15, identity=None
) -> dict[str, Any] | None:
    """The audit history with honest coverage (S5); ``None`` = not available."""
    from tasks.workorder_models import WorkOrderEvent

    events = work_order_history(user, work_order, limit=limit, identity=identity)
    if events is None:
        return None
    population_count = WorkOrderEvent.objects.filter(work_order=work_order).count()
    return {
        'events': events,
        'population_count': population_count,
        'returned_count': len(events),
        'display_truncated': population_count > len(events),
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
        # A16/Q14 (S5b): the owner-approved exposure — fenced and capped;
        # embedded instructions in the narrative remain untrusted data.
        'description': fence(work_order.description) or None,
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
        'assigned': work_order.assigned_to_id is not None,
        'assigned_to': _identity_label(
            None, work_order.assigned_to if work_order.assigned_to_id else None
        ),
        'due_date': _iso(work_order.due_date),
        'created_at': _iso(work_order.created_at),
        'updated_at': _iso(work_order.updated_at),
        'actual_started_at': _iso(work_order.actual_started_at),
        'actual_completed_at': _iso(work_order.actual_completed_at),
        'scheduled_start': _iso(work_order.scheduled_start),
        'scheduled_end': _iso(work_order.scheduled_end),
        'estimated_minutes': work_order.estimated_minutes,
        'parts': parts,
        'parts_truncated': lines.count() > MAX_PARTS,
    }


def work_order_closeout(work_order) -> dict[str, Any] | None:
    """The EFFECTIVE structured closeout, evidence stages separated (S5b).

    Built exclusively on ``closeout_amend.effective_closeout()`` so applied
    amendments supersede the base row — the raw pre-amendment row is never
    projected. Every narrative field is fenced and capped; stages are never
    filled from one another ("component replaced" is an action, not proof of
    cause; "done" is an administrative status, not proof of sustained
    operation). ``verified`` distinguishes reviewed evidence from an
    unreviewed writeup; compliance/causal conclusions require it.
    """
    from .workorder_models import WorkOrderCloseout

    try:
        closeout = work_order.structured_closeout
    except WorkOrderCloseout.DoesNotExist:
        return None
    from .services.closeout_amend import effective_closeout_overview

    fields = effective_closeout_overview(closeout)
    return {
        'work_order_id': work_order.pk,
        'cause': fence(str(fields.get('cause') or '')) or None,
        'action': fence(str(fields.get('action') or '')) or None,
        'result': fence(str(fields.get('result') or '')) or None,
        'verification_summary': fence(str(fields.get('verification_summary') or ''))
        or None,
        'downtime_minutes': fields.get('downtime_minutes'),
        'follow_up_required': bool(fields.get('follow_up_required')),
        'follow_up': fence(str(fields.get('follow_up') or '')) or None,
        'completed_at': _iso(closeout.completed_at),
        'verified': closeout.verified_at is not None,
        'verified_at': _iso(closeout.verified_at),
        'amended': fields['amended'],
        'amendment_count': fields['amendment_count'],
        'version': closeout.version,
        'content_hash': closeout.content_hash or None,
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
    rows = (
        RepairPacket.objects
        .filter(machine=machine)
        .exclude(status__in=terminal)
        .select_related('work_order')
    )
    population_count = rows.count()
    packets = rows.order_by('-created_at')[:MAX_OPEN_REPAIRS]

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

    # S5 coverage vocabulary: 'total' was the truncated page length.
    return {
        'machine_id': machine.pk,
        'repairs': repairs,
        'population_count': population_count,
        'returned_count': len(repairs),
        'display_truncated': population_count > len(repairs),
    }


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
    'PAGE_DATE_FIELDS',
    'authorized_machine',
    'authorized_work_order',
    'fence',
    'maintenance_ai_read_enabled',
    'open_repairs_for_machine',
    'work_order_closeout',
    'work_order_history_page',
    'work_order_overview',
    'work_order_readiness',
    'work_order_repair_state',
    'work_order_row',
    'work_orders_in_scope',
    'work_orders_page',
]
