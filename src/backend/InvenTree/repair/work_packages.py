"""Canonical repair work-package draft schema and atomic create command.

One audited command creates a maintenance work package, whatever raised it: the
Maintenance workspace button, a machine Repair action, a health anomaly, or an
approved AI proposal. Every one of those paths builds the same versioned
``RepairWorkPackageDraft`` and calls :func:`create_repair_work_package`; none of
them writes ``WorkOrder`` or ``RepairPacket`` directly.

The command creates a *planned* work package. Starting the repair is a separate,
readiness-gated lifecycle transition and is deliberately not reachable from here.

Before anything is written the command checks for open repair work on the same
asset. Two work orders for one fault split the technician's attention, the parts
reservation and the audit trail, so the default is to stop and show what already
exists; proceeding anyway requires an explicit, attributed override.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.dateparse import parse_date

from tasks.models import WorkOrder, WorkOrderCommand, WorkOrderType
from tasks.services import scheduling, work_orders

from assets.models import AssetMachine
from part.models import Part

from .models import (
    PacketStatus,
    RepairPacket,
    RepairPacketEvent,
    RepairPacketHealthEvidence,
)
from .services import resolve_safety_gates

DRAFT_SCHEMA_VERSION = 1

ORIGIN_MANUAL = 'manual'
ORIGIN_ANOMALY = 'anomaly'
ORIGIN_CHAT = 'chat'
VALID_ORIGINS = frozenset({ORIGIN_MANUAL, ORIGIN_ANOMALY, ORIGIN_CHAT})

VALID_PRIORITIES = frozenset({
    WorkOrder.PRIORITY_LOW,
    WorkOrder.PRIORITY_MEDIUM,
    WorkOrder.PRIORITY_HIGH,
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


class InvalidAnomalySource(WorkPackageError):  # noqa: N818
    """A requested source cannot be owned by this work package."""

    code = 'INVALID_ANOMALY_SOURCE'


class DuplicateRepairConflict(WorkPackageError):  # noqa: N818 - matches sibling error names
    """Open repair work already exists for this machine.

    Carries the existing links so the caller can show what is already open
    instead of just refusing.
    """

    code = 'DUPLICATE_OPEN_REPAIR'

    def __init__(self, message: str, *, duplicates):
        """Record the conflicting repairs alongside the message."""
        super().__init__(message)
        self.duplicates = duplicates


@dataclass(frozen=True)
class WorkPackageResult:
    """Outcome of one work-package creation."""

    work_order_id: int
    work_order_reference: str
    repair_packet_id: int | None
    repair_packet_reference: str
    replayed: bool
    warnings: list[str] = field(default_factory=list)
    machine_id: int | None = None
    anomaly_id: int | None = None
    assigned_to_id: int | None = None
    idempotency_key: str = ''
    lifecycle_version: int | None = None

    def as_dict(self) -> dict:
        """Serialize for an API response."""
        return {
            'work_order_id': self.work_order_id,
            'work_order_reference': self.work_order_reference,
            'repair_packet_id': self.repair_packet_id,
            'repair_packet_reference': self.repair_packet_reference,
            'replayed': self.replayed,
            'warnings': list(self.warnings),
            'machine_id': self.machine_id,
            'anomaly_id': self.anomaly_id,
            'assigned_to_id': self.assigned_to_id,
            'idempotency_key': self.idempotency_key,
            'lifecycle_version': self.lifecycle_version,
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


def _identifier_list(source, key):
    values = source.get(key) or []
    if (
        not isinstance(values, list)
        or len(values) > 100
        or any(
            isinstance(value, (dict, list, bool)) or not isinstance(value, (str, int))
            for value in values
        )
    ):
        raise WorkPackageError(f'{key} must be a bounded identifier list.')
    return values


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
        if not quantity.is_finite() or quantity <= 0:
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
    due_date = planning.get('due_date') or None
    if due_date is not None:
        try:
            valid_date = parse_date(due_date) if isinstance(due_date, str) else None
        except ValueError:
            valid_date = None
        if valid_date is None:
            raise WorkPackageError('due_date must be a valid ISO date.')
    occupational = {}
    if 'assigned_to' in planning:
        occupational['assigned_to'] = _positive_int(planning, 'assigned_to')
    if 'required_trades' in planning:
        codes = planning['required_trades']
        if (
            not isinstance(codes, list)
            or len(codes) > 20
            or any(not isinstance(code, str) for code in codes)
            or len(codes) != len(set(codes))
        ):
            raise WorkPackageError(
                'required_trades must be a unique list of at most 20 trade codes.'
            )
        occupational['required_trades'] = codes
    for flag in ('trade_requirements_confirmed', 'auto_assign'):
        if flag in planning:
            if not isinstance(planning[flag], bool):
                raise WorkPackageError(f'{flag} must be a boolean.')
            occupational[flag] = planning[flag]
    if 'override_reason' in planning:
        occupational['override_reason'] = _text(planning, 'override_reason', limit=1000)
    assigned_to_id = _positive_int(planning, 'assigned_to_id')
    if assigned_to_id is not None and occupational.get('assigned_to') not in (
        None,
        assigned_to_id,
    ):
        raise WorkPackageError('The assigned staff identities must agree.')
    if occupational.get('auto_assign') and (
        assigned_to_id is not None or occupational.get('assigned_to') is not None
    ):
        raise WorkPackageError(
            'Choose automatic assignment or an explicit user, not both.'
        )

    source = payload.get('source') or {}
    if not isinstance(source, dict):
        raise WorkPackageError('source must be an object.')
    if payload.get('duplicate_override') and not _text(
        payload, 'duplicate_override_reason'
    ):
        raise WorkPackageError('A reason is required to create a separate repair.')

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
            payload, 'priority', VALID_PRIORITIES, WorkOrder.PRIORITY_MEDIUM
        ),
        'create_repair_packet': bool(create_packet),
        # Proceeding past an open repair is a deliberate, attributed act.
        'duplicate_override': bool(payload.get('duplicate_override', False)),
        'duplicate_override_reason': _text(
            payload, 'duplicate_override_reason', limit=500
        ),
        'fault': {
            'summary': _text(fault, 'summary'),
            'symptom': _text(fault, 'symptom', limit=255),
            'production_impact': _text(fault, 'production_impact'),
            'criticality': _choice(fault, 'criticality', VALID_CRITICALITIES, 'medium'),
        },
        'parts': _validate_parts(payload.get('parts')),
        'planning': {
            'assignee': _text(planning, 'assignee', limit=255),
            'assigned_to_id': assigned_to_id,
            **occupational,
            'due_date': due_date,
            'estimated_minutes': _positive_int(planning, 'estimated_minutes'),
        },
        'source': {
            'anomaly_id': _positive_int(source, 'anomaly_id'),
            'ai_draft_token': _text(source, 'ai_draft_token', limit=24000),
            'thread_id': _text(source, 'thread_id', limit=128),
            'source_turn_ids': _identifier_list(source, 'source_turn_ids'),
            'evidence_snapshot_ids': _identifier_list(source, 'evidence_snapshot_ids'),
        },
    }


def _packet_reference(packet) -> str:
    return packet.reference if packet else ''


def _link_anomaly(anomaly_id, *, machine, work_order, packet, actor) -> str:
    """Link the originating health anomaly and capture its evidence.

    Missing, cross-machine or already-owned evidence rolls back the whole
    aggregate. A successful intake must actually own its requested source.

    Returns a warning string, or '' when there was nothing to do.
    """
    if not anomaly_id:
        return ''

    # Imported here so ``repair`` does not take a module-level dependency on the
    # health app; the FK the other way is declared as a string reference.
    from assets.health_models import MachineAnomaly, SnapshotReason
    from machine_health.services.snapshots import capture_anomaly_evidence

    anomaly = MachineAnomaly.objects.select_for_update().filter(pk=anomaly_id).first()
    if anomaly is None or anomaly.machine_id != machine.pk:
        raise InvalidAnomalySource('The source anomaly is not on this machine.')
    if (
        anomaly.detector == 'test_alarm_ui'
        and anomaly.metrics.get('synthetic') is True
        and not anomaly.is_active
    ):
        raise InvalidAnomalySource(
            'This test event has ended. Trigger a new reviewed exercise instead.'
        )
    if anomaly.work_order_id not in (None, work_order.pk) or (
        anomaly.repair_packet_id is not None
        and anomaly.repair_packet_id != getattr(packet, 'pk', None)
    ):
        raise InvalidAnomalySource(
            'This event already belongs to another work package. Open its owning work instead.'
        )
    if anomaly.work_order_id == work_order.pk and anomaly.repair_packet_id == getattr(
        packet, 'pk', None
    ):
        return ''

    updates = {'work_order': work_order}
    if packet is not None:
        updates['repair_packet'] = packet
    for field_name, value in updates.items():
        setattr(anomaly, field_name, value)
    anomaly.save(update_fields=[*updates, 'updated_at'])

    # Freeze what the signals read at the moment the repair was authorized, so
    # the decision stays reconstructable after the live values move on.
    snapshots = capture_anomaly_evidence(
        anomaly, reason=SnapshotReason.ANOMALY_REPAIR, actor=actor
    )

    if packet is not None:
        # A typed link, not just ids in the diagnosis JSON: this is what keeps
        # the cited snapshots joinable and protected from deletion.
        for snapshot in snapshots:
            RepairPacketHealthEvidence.objects.get_or_create(
                packet=packet,
                snapshot=snapshot,
                defaults={
                    'relation': RepairPacketHealthEvidence.RELATION_UNKNOWN,
                    'observation': (
                        f'Captured when the repair was raised from anomaly '
                        f'{anomaly.pk}.'
                    ),
                    'created_by': actor if getattr(actor, 'pk', None) else None,
                },
            )

    return ''


def _prior_command_work_order(idempotency_key: str):
    """Return the work-order id a previous create under this key produced.

    ``None`` means this is a first attempt. Used to tell a retry apart from a
    genuinely new request before duplicate control runs.
    """
    from tasks.models import WorkOrderCommand

    prior = WorkOrderCommand.objects.filter(
        command='create', idempotency_key=idempotency_key
    ).first()
    return prior.work_order_id if prior else None


def find_duplicate_repairs(machine, *, anomaly_id=None, exclude_work_order_id=None):
    """Return open repairs on this machine that a new one may be duplicating.

    Two work orders for the same fault split a technician's attention, the parts
    reservation and the audit trail. The server checks before creating rather
    than leaving it to whoever notices second: same anomaly first, then any other
    open packet-backed repair on the asset.
    """
    from assets.health_models import MachineAnomaly

    candidates = []

    if anomaly_id:
        linked = (
            MachineAnomaly.objects
            .filter(pk=anomaly_id, machine=machine)
            .exclude(work_order__isnull=True)
            .select_related('work_order', 'repair_packet')
            .first()
        )
        if linked and linked.work_order_id != exclude_work_order_id:
            candidates.append({
                'reason': 'ANOMALY_ALREADY_LINKED',
                'work_order_id': linked.work_order_id,
                'work_order_reference': linked.work_order.reference or '',
                'title': linked.work_order.title,
                'status': linked.work_order.lifecycle_status,
                'repair_packet_id': linked.repair_packet_id,
                'anomaly_id': linked.pk,
            })

    open_packets = (
        RepairPacket.objects
        .filter(machine=machine, work_order__isnull=False)
        .exclude(status__in=[PacketStatus.CLOSED, PacketStatus.CANCELED])
        .exclude(work_order_id=exclude_work_order_id)
        .select_related('work_order')
    )
    for packet in open_packets:
        candidates.append({
            'reason': 'OPEN_REPAIR_ON_MACHINE',
            'work_order_id': packet.work_order_id,
            'work_order_reference': packet.work_order.reference or '',
            'title': packet.work_order.title,
            'status': packet.work_order.lifecycle_status,
            'repair_packet_id': packet.pk,
            'repair_packet_reference': packet.reference,
        })

    packet_work_ids = {row['work_order_id'] for row in candidates}
    for work in (
        WorkOrder.objects
        .filter(
            machine=machine,
            work_order_type=WorkOrderType.CORRECTIVE,
            repair_packet__isnull=True,
        )
        .exclude(lifecycle_status__in=['completed', 'canceled'])
        .exclude(status=WorkOrder.STATUS_DONE)
        .exclude(pk__in=packet_work_ids)
        .exclude(pk=exclude_work_order_id)
    ):
        candidates.append({
            'reason': 'OPEN_REPAIR_ON_MACHINE',
            'work_order_id': work.pk,
            'work_order_reference': work.reference or '',
            'title': work.title,
            'status': work.lifecycle_status,
            'repair_packet_id': None,
            'repair_packet_reference': '',
        })

    return candidates


@transaction.atomic
def create_repair_work_package(
    *,
    actor,
    draft,
    idempotency_key: str,
    correlation_id: uuid.UUID | None = None,
    allow_assignment_override: bool = False,
) -> WorkPackageResult:
    """Atomically create one machine-linked work order and optional repair packet.

    Commits everything or nothing: the work order, the packet, required-part
    lines and the safety gates resolved for the packet all land in one
    transaction, so a partially-created work package can never be observed.

    Idempotent on ``idempotency_key`` through the shared work-order command
    ledger; a replay returns the existing aggregate and reports ``replayed``.
    """
    draft = validate_draft(draft)
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 128
    ):
        raise WorkPackageError(
            'Use a non-empty idempotency key of at most 128 characters.'
        )
    warnings: list[str] = []

    machine = AssetMachine.objects.filter(pk=draft['machine_id']).first()
    if machine is None:
        raise UnknownMachine(f'No machine {draft["machine_id"]}.')
    # Intake can be called directly by chat, approvals or generation. Check
    # the current creation boundary before returning duplicate repair links or
    # looking up any other draft metadata. The canonical create command repeats
    # this check under its locks immediately before a write or replay.
    machine = scheduling._authorize_creation(actor, machine.pk, lock=True)
    package_key = (
        'wp2:' + hashlib.sha256(f'{actor.pk}:{idempotency_key}'.encode()).hexdigest()
    )
    package_digest = work_orders._canonical_hash('work_package.v2', actor, draft)
    prior_package = WorkOrderCommand.objects.filter(
        command='create_work_package', idempotency_key=package_key
    ).first()
    if prior_package is not None:
        if prior_package.request_hash != package_digest:
            raise scheduling.IdempotencyConflict(
                'This request key was already used for a different work package.'
            )
        work_order = WorkOrder.objects.get(pk=prior_package.work_order_id)
        work_orders._require_scope(actor, work_order)
        package_event = work_order.events.filter(pk=prior_package.result_ref).first()
        prior_metadata = (
            (package_event.metadata.get('result_metadata') or {})
            if package_event
            else {}
        )
        return _creation_receipt(
            work_order,
            draft,
            idempotency_key,
            replayed=True,
            warnings=prior_metadata.get('warnings', []),
        )
    from .work_intake import reviewed_ai_draft

    ai_review = reviewed_ai_draft(actor, draft)

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

    # A retry of a request that already succeeded is not a duplicate - it is the
    # same repair. Checking the idempotency ledger first is what keeps a dropped
    # response from turning into a conflict the caller cannot resolve.
    is_replay_of = _prior_command_work_order(idempotency_key)

    # Duplicate control runs before anything is written. An authorized user may
    # still proceed, but only by saying so explicitly with a reason - the default
    # is to stop and show them the repair that already exists.
    if is_replay_of is None:
        duplicates = find_duplicate_repairs(
            machine, anomaly_id=draft['source'].get('anomaly_id')
        )
        if duplicates and not draft['duplicate_override']:
            raise DuplicateRepairConflict(
                'This machine already has open repair work.', duplicates=duplicates
            )
        if duplicates:
            from tasks.permissions import ASSIGN_WORKORDER, require_permission

            require_permission(actor, ASSIGN_WORKORDER)
            warnings.append(
                f'Created alongside {len(duplicates)} existing open repair(s): '
                f'{draft["duplicate_override_reason"] or "no reason given"}'
            )

    planning = draft['planning']
    assignee = None
    selected_id = planning['assigned_to_id'] or planning.get('assigned_to')
    if selected_id is not None:
        from .work_intake import assignment_candidate

        assignee = assignment_candidate(actor, machine, selected_id)
    if is_replay_of is not None and (
        selected_id is not None or draft['source']['ai_draft_token']
    ):
        raise scheduling.IdempotencyConflict(
            'This legacy request cannot be changed to include typed assignment or an AI draft.'
        )
    command = scheduling.create_work_order(
        actor=actor,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        title=draft['title'],
        machine_id=machine.pk,
        description=draft['description'] or draft['fault']['summary'],
        priority=draft['priority'],
        work_order_type=draft['work_order_type'],
        assignee=(assignee.get_full_name().strip() or assignee.get_username())
        if assignee is not None
        else planning['assignee'],
        due_date=planning['due_date'],
        allow_assignment_override=allow_assignment_override,
        **{
            key: planning[key]
            for key in (
                'assigned_to',
                'required_trades',
                'trade_requirements_confirmed',
                'auto_assign',
                'override_reason',
            )
            if key in planning and assignee is None
        },
    )
    assignment = (command.metadata or {}).get('assignment', {})
    replayed = bool(command.metadata.get('replayed'))

    work_order = WorkOrder.objects.get(pk=command.work_order_id)
    if replayed:
        return _creation_receipt(
            work_order,
            draft,
            idempotency_key,
            replayed=True,
            warnings=[
                'Legacy receipt returned read-only; full package content was not recorded.'
            ],
        )
    if assignee is not None:
        assignment_result = work_orders.assign_work_order(
            work_order_id=work_order.pk,
            assigned_to=assignee,
            actor=actor,
            expected_version=work_order.lifecycle_version,
            idempotency_key=f'package-assign-{uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key)}',
            correlation_id=command.correlation_id,
            reason='Typed assignment reviewed during work-package creation.',
            required_trades=planning.get('required_trades'),
            trade_requirements_confirmed=planning.get('trade_requirements_confirmed'),
            override_reason=planning.get('override_reason', ''),
            allow_override=allow_assignment_override,
        )
        assignment = assignment_result.metadata or {}
        work_order.refresh_from_db()
    if assignment.get('code') not in (None, 'ASSIGNMENT_QUALIFIED'):
        warnings.append(f'Assignment review required: {assignment["code"]}')

    # Scope flows from the asset's client via ``scope_for_work_order``; no field
    # sync is needed. ``estimated_minutes`` is planning metadata the create
    # command does not accept as a lifecycle field.
    if planning['estimated_minutes'] and not work_order.estimated_minutes:
        work_order.estimated_minutes = planning['estimated_minutes']
        work_order.save(update_fields=['estimated_minutes', 'updated_at'])

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

    anomaly_warning = _link_anomaly(
        draft['source'].get('anomaly_id'),
        machine=machine,
        work_order=work_order,
        packet=packet,
        actor=actor,
    )
    if anomaly_warning:
        warnings.append(anomaly_warning)

    from tasks.work_execution.exercise_verification import exercise_source

    exercise_profile = (
        'P08-MOTOR-COOL-RTS-EX' if exercise_source(work_order) is not None else None
    )
    work_orders._append_result(
        work_order=work_order,
        actor=actor,
        command='create_work_package',
        event_type='WORK_PACKAGE_CREATED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason=draft['duplicate_override_reason'],
        correlation_id=command.correlation_id,
        idempotency_key=package_key,
        request_hash=package_digest,
        result_metadata={
            'hash_version': 2,
            'exercise_profile': exercise_profile,
            'source_anomaly_id': draft['source']['anomaly_id'],
            'assigned_to_id': work_order.assigned_to_id,
            'repair_packet_id': packet.pk if packet else None,
            'origin': draft['origin'],
            'ai_review': ai_review,
            'warnings': warnings,
            'assignment': assignment,
        },
    )
    return _creation_receipt(work_order, draft, idempotency_key, warnings=warnings)


def _creation_receipt(
    work_order, draft, idempotency_key, *, replayed=False, warnings=None
):
    packet = getattr(work_order, 'repair_packet', None)
    anomaly_id = draft['source']['anomaly_id']
    if (
        anomaly_id is not None
        and not work_order.anomalies.filter(pk=anomaly_id).exists()
    ):
        anomaly_id = None
    return WorkPackageResult(
        work_order_id=work_order.pk,
        work_order_reference=work_order.reference or '',
        repair_packet_id=packet.pk if packet else None,
        repair_packet_reference=_packet_reference(packet),
        replayed=replayed,
        warnings=warnings or [],
        machine_id=work_order.machine_id,
        anomaly_id=anomaly_id,
        assigned_to_id=work_order.assigned_to_id,
        idempotency_key=idempotency_key,
        lifecycle_version=work_order.lifecycle_version,
    )
