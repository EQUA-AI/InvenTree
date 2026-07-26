"""Canonical repair work-package draft schema and atomic create command.

One audited command creates a maintenance work package, whatever raised it: the
Maintenance workspace button, a machine Repair action, a health anomaly, or an
approved AI proposal. Every one of those paths builds the same versioned
``RepairWorkPackageDraft`` and calls :func:`create_repair_work_package`; none of
them writes ``KanbanCard`` or ``RepairPacket`` directly.

The command creates a *planned* work package. Starting the repair is a separate,
readiness-gated lifecycle transition and is deliberately not reachable from here.

Scope note: this is Phase 1 of the plan in
``LocalDocs/MaintenanceHealthRepairWorkflowPlan.md``. The draft fields that
depend on the machine-health read model (anomaly id, evidence snapshot ids,
preliminary results) are accepted and validated structurally but are not yet
resolved against health records - those arrive with the Health blade. The
provenance they carry is persisted so nothing is lost in the meantime.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction

from tasks.models import KanbanCard, WorkOrderType
from tasks.services import scheduling

from assets.models import AssetMachine
from part.models import Part

from .models import PacketStatus, RepairPacket, RepairPacketEvent
from .services import resolve_safety_gates

DRAFT_SCHEMA_VERSION = 1

ORIGIN_MANUAL = 'manual'
ORIGIN_ANOMALY = 'anomaly'
ORIGIN_CHAT = 'chat'
VALID_ORIGINS = frozenset({ORIGIN_MANUAL, ORIGIN_ANOMALY, ORIGIN_CHAT})

VALID_PRIORITIES = frozenset({
    KanbanCard.PRIORITY_LOW,
    KanbanCard.PRIORITY_MEDIUM,
    KanbanCard.PRIORITY_HIGH,
})
VALID_WORK_ORDER_TYPES = frozenset(WorkOrderType.values)
VALID_CRITICALITIES = frozenset({'low', 'medium', 'high', 'critical'})

# A work package is a planning artefact, not a bulk import. Bounding the parts
# list keeps one request from fanning out into an unbounded stock check.
MAX_PARTS = 50


class WorkPackageError(Exception):
    """The work-package draft is invalid or cannot be executed."""

    code = 'WORK_PACKAGE_INVALID'


class UnknownMachine(WorkPackageError):  # noqa: N818 - matches sibling error names
    """The draft names a machine that does not exist."""

    code = 'UNKNOWN_MACHINE'


class UnknownPart(WorkPackageError):  # noqa: N818 - matches sibling error names
    """The draft names a part that does not exist."""

    code = 'UNKNOWN_PART'


@dataclass(frozen=True)
class WorkPackageResult:
    """Outcome of one work-package creation."""

    work_order_id: int
    work_order_reference: str
    repair_packet_id: int | None
    repair_packet_reference: str
    replayed: bool
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Serialize for an API response."""
        return {
            'work_order_id': self.work_order_id,
            'work_order_reference': self.work_order_reference,
            'repair_packet_id': self.repair_packet_id,
            'repair_packet_reference': self.repair_packet_reference,
            'replayed': self.replayed,
            'warnings': list(self.warnings),
        }


def _text(payload, key, *, default='', limit=None):
    value = payload.get(key, default)
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise WorkPackageError(f'{key} must be a string.')
    value = value.strip()
    return value[:limit] if limit else value


def _choice(payload, key, allowed, default):
    value = payload.get(key) or default
    if value not in allowed:
        raise WorkPackageError(f'{key} must be one of: {", ".join(sorted(allowed))}.')
    return value


def _positive_int(payload, key):
    value = payload.get(key)
    if value in (None, ''):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkPackageError(f'{key} must be an integer.')
    if value <= 0:
        raise WorkPackageError(f'{key} must be positive.')
    return value


def _validate_parts(raw):
    """Normalize the requested part lines without touching the database."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkPackageError('parts must be a list.')
    if len(raw) > MAX_PARTS:
        raise WorkPackageError(f'A work package may request at most {MAX_PARTS} parts.')

    lines = []
    seen = set()

    for entry in raw:
        if not isinstance(entry, dict):
            raise WorkPackageError('Each part line must be an object.')

        part_id = entry.get('part_id', entry.get('part'))
        if isinstance(part_id, bool) or not isinstance(part_id, int):
            raise WorkPackageError('Each part line needs an integer part_id.')
        if part_id in seen:
            raise WorkPackageError(f'Part {part_id} is listed more than once.')
        seen.add(part_id)

        try:
            quantity = Decimal(str(entry.get('quantity', 1)))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise WorkPackageError(
                f'Quantity for part {part_id} is not a number.'
            ) from exc
        if quantity <= 0:
            raise WorkPackageError(f'Quantity for part {part_id} must be positive.')

        lines.append({
            'part_id': part_id,
            'quantity': quantity,
            'reason': _text(entry, 'reason', limit=255),
        })

    return lines


def validate_draft(payload) -> dict:
    """Validate and normalize a ``RepairWorkPackageDraft`` (schema v1).

    Returns a canonical dict. Raises :class:`WorkPackageError` with a stable code
    for anything the server will not accept. Nothing here trusts a caller-supplied
    id beyond its shape; existence is proven inside the create command, under the
    same transaction that writes.
    """
    if not isinstance(payload, dict):
        raise WorkPackageError('The work package draft must be an object.')

    version = payload.get('schema_version', DRAFT_SCHEMA_VERSION)
    if version != DRAFT_SCHEMA_VERSION:
        raise WorkPackageError(
            f'Unsupported work package schema version {version!r}; '
            f'expected {DRAFT_SCHEMA_VERSION}.'
        )

    machine_id = payload.get('machine_id', payload.get('machine'))
    if isinstance(machine_id, bool) or not isinstance(machine_id, int):
        raise WorkPackageError('A work package requires an integer machine_id.')

    title = _text(payload, 'title', limit=200)
    if not title:
        raise WorkPackageError('A work package requires a title.')

    fault = payload.get('fault') or {}
    if not isinstance(fault, dict):
        raise WorkPackageError('fault must be an object.')

    planning = payload.get('planning') or {}
    if not isinstance(planning, dict):
        raise WorkPackageError('planning must be an object.')

    source = payload.get('source') or {}
    if not isinstance(source, dict):
        raise WorkPackageError('source must be an object.')

    create_packet = payload.get('create_repair_packet')
    work_order_type = _choice(
        payload, 'work_order_type', VALID_WORK_ORDER_TYPES, WorkOrderType.CORRECTIVE
    )
    if create_packet is None:
        # Corrective work gets a packet by default; planning/administrative work
        # does not need a fault-to-fix aggregate.
        create_packet = work_order_type == WorkOrderType.CORRECTIVE

    return {
        'schema_version': DRAFT_SCHEMA_VERSION,
        'machine_id': machine_id,
        'origin': _choice(payload, 'origin', VALID_ORIGINS, ORIGIN_MANUAL),
        'title': title,
        'description': _text(payload, 'description'),
        'work_order_type': work_order_type,
        'priority': _choice(
            payload, 'priority', VALID_PRIORITIES, KanbanCard.PRIORITY_MEDIUM
        ),
        'create_repair_packet': bool(create_packet),
        'fault': {
            'summary': _text(fault, 'summary'),
            'symptom': _text(fault, 'symptom', limit=255),
            'production_impact': _text(fault, 'production_impact'),
            'criticality': _choice(fault, 'criticality', VALID_CRITICALITIES, 'medium'),
        },
        'parts': _validate_parts(payload.get('parts')),
        'planning': {
            'assignee': _text(planning, 'assignee', limit=255),
            'due_date': planning.get('due_date') or None,
            'estimated_minutes': _positive_int(planning, 'estimated_minutes'),
        },
        'source': {
            'anomaly_id': source.get('anomaly_id'),
            'thread_id': _text(source, 'thread_id', limit=128),
            'source_turn_ids': list(source.get('source_turn_ids') or []),
            'evidence_snapshot_ids': list(source.get('evidence_snapshot_ids') or []),
        },
    }


def _packet_reference(packet) -> str:
    return packet.reference if packet else ''


@transaction.atomic
def create_repair_work_package(
    *, actor, draft, idempotency_key: str, correlation_id: uuid.UUID | None = None
) -> WorkPackageResult:
    """Atomically create one machine-linked work order and optional repair packet.

    Commits everything or nothing: the work order, the packet, required-part
    lines and the safety gates resolved for the packet all land in one
    transaction, so a partially-created work package can never be observed.

    Idempotent on ``idempotency_key`` through the shared work-order command
    ledger; a replay returns the existing aggregate and reports ``replayed``.
    """
    draft = validate_draft(draft)
    warnings: list[str] = []

    machine = AssetMachine.objects.filter(pk=draft['machine_id']).first()
    if machine is None:
        raise UnknownMachine(f'No machine {draft["machine_id"]}.')

    if draft['parts']:
        known = set(
            Part.objects.filter(
                pk__in=[line['part_id'] for line in draft['parts']]
            ).values_list('pk', flat=True)
        )
        missing = sorted({line['part_id'] for line in draft['parts']} - known)
        if missing:
            raise UnknownPart(
                'Unknown part ids: ' + ', '.join(str(pk) for pk in missing)
            )

    planning = draft['planning']
    command = scheduling.create_work_order(
        actor=actor,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        title=draft['title'],
        machine_id=machine.pk,
        description=draft['description'] or draft['fault']['summary'],
        priority=draft['priority'],
        work_order_type=draft['work_order_type'],
        assignee=planning['assignee'],
        due_date=planning['due_date'],
    )
    replayed = bool(command.metadata.get('replayed'))

    work_order = KanbanCard.objects.get(pk=command.work_order_id)

    # The work order inherits the asset's customer so scope resolution has one
    # answer; ``estimated_minutes`` is planning metadata the create command does
    # not accept as a lifecycle field.
    updates = {}
    if work_order.customer_id != machine.customer_id:
        updates['customer_id'] = machine.customer_id
    if planning['estimated_minutes'] and not work_order.estimated_minutes:
        updates['estimated_minutes'] = planning['estimated_minutes']
    if updates:
        for field_name, value in updates.items():
            setattr(work_order, field_name, value)
        work_order.save(update_fields=[*updates, 'updated_at'])

    if not work_order.reference:
        work_order.reference = f'WO-{work_order.pk:06d}'
        work_order.save(update_fields=['reference', 'updated_at'])

    scheduling.materialise_required_parts(
        work_order_id=work_order.pk,
        lines=[(line['part_id'], line['quantity']) for line in draft['parts']],
    )

    packet = None
    if draft['create_repair_packet']:
        packet = getattr(work_order, 'repair_packet', None)
        if packet is None:
            fault = draft['fault']
            packet = RepairPacket.objects.create(
                machine=machine,
                work_order=work_order,
                fault_summary=fault['summary'] or draft['description'],
                symptom=fault['symptom'],
                production_impact=fault['production_impact'],
                criticality=fault['criticality'],
                status=PacketStatus.DRAFT,
            )
            RepairPacketEvent.objects.create(
                packet=packet,
                event_type=RepairPacketEvent.EventType.CREATED,
                to_status=packet.status,
                actor=actor if getattr(actor, 'pk', None) else None,
                reason=f'Created from a {draft["origin"]} work package',
                metadata={
                    'origin': draft['origin'],
                    'work_order_id': work_order.pk,
                    'schema_version': DRAFT_SCHEMA_VERSION,
                    'source': draft['source'],
                },
            )
            # Gate resolution is deterministic template matching, not AI: it can
            # only add required gates, never mark one satisfied.
            resolve_safety_gates(packet, actor)
        else:
            warnings.append('A repair packet already existed for this work order.')

    return WorkPackageResult(
        work_order_id=work_order.pk,
        work_order_reference=work_order.reference or '',
        repair_packet_id=packet.pk if packet else None,
        repair_packet_reference=_packet_reference(packet),
        replayed=replayed,
        warnings=warnings,
    )
