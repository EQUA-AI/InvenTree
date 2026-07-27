"""Structured work-order completion and asset-history writeback (section 14).

``complete_work_order`` runs one atomic completion boundary: validate readiness,
persist the canonical structured closeout, write exactly one linked
``AssetMaintenanceRecord``, close the Job Kit and release any non-consumed
reservations, and transition the work order to ``COMPLETED``. It is idempotent on
the shared work-order command ledger.
"""

import hashlib
import json
import uuid

from django.db import transaction
from django.utils import timezone

from assets.models import AssetMaintenanceRecord
from stock.models import StockItem
from tasks.jobkit_models import (
    ACTIVE_ALLOCATION_STATUSES,
    JobKit,
    JobKitAllocation,
    JobKitAllocationStatus,
    JobKitStatus,
)
from tasks.models import WorkOrderCloseout, WorkOrderLifecycle
from tasks.permissions import COMPLETE_WORKORDER, require_permission
from tasks.services.readiness import evaluate_work_order_readiness
from tasks.services.work_orders import (
    ReadinessBlocked,
    WorkOrderCommandError,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_no_packet,
    _require_scope,
    _require_version,
    _validate_transition,
)

REQUIRED_CLOSEOUT_FIELDS = ('action', 'result', 'verification_summary')

_ACTIVE_ALLOCATION_VALUES = [s.value for s in ACTIVE_ALLOCATION_STATUSES]


class CloseoutError(WorkOrderCommandError):
    """The structured closeout payload is incomplete or invalid."""

    code = 'CLOSEOUT_INVALID'


def _closeout_hash(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode()
    ).hexdigest()


def _closeout_details(closeout):
    parts = []
    if closeout.get('cause'):
        parts.append(f'Cause: {closeout["cause"]}')
    parts.append(f'Action: {closeout["action"]}')
    parts.append(f'Result: {closeout["result"]}')
    parts.append(f'Verification: {closeout["verification_summary"]}')
    return '\n'.join(parts)


def _close_kit(work_order, actor, now):
    """Close the kit and release any active, non-consumed reservations."""
    kit = JobKit.objects.select_for_update().filter(work_order=work_order).first()
    if kit is None:
        return
    active = JobKitAllocation.objects.select_for_update().filter(
        line__kit=kit, status__in=_ACTIVE_ALLOCATION_VALUES
    )
    for allocation in active:
        # Lock the stock row so availability stays coherent with the release.
        StockItem.objects.select_for_update().get(pk=allocation.stock_item_id)
        allocation.status = JobKitAllocationStatus.RELEASED
        allocation.disposed_at = now
        allocation.save(update_fields=['status', 'disposed_at'])
    kit.status = JobKitStatus.CLOSED
    kit.closed_at = now
    kit.version = kit.version + 1
    kit.save(update_fields=['status', 'closed_at', 'version', 'updated_at'])


def _validated_capture(work_order, capture_id, closeout):
    """Lock and validate a reviewed capture joining the completion boundary."""
    from django.conf import settings

    from tasks.closeout_models import CloseoutCapture, CloseoutCaptureStatus
    from tasks.services.closeout_capture import (
        CaptureError,
        DecisionRequired,
        _live_proposal,
        decisions_cover_required_fields,
    )

    if not getattr(settings, 'AIMMS_CLOSEOUT_WIZARD_ENABLED', False):
        raise CaptureError('The closeout wizard is disabled in this deployment')
    capture = (
        CloseoutCapture.objects
        .select_for_update(of=('self',))
        .select_related('current_revision')
        .filter(pk=capture_id, work_order=work_order)
        .first()
    )
    if capture is None:
        raise CaptureError('Capture does not belong to this work order')
    if capture.status != CloseoutCaptureStatus.REVIEWED:
        raise CaptureError(f'A {capture.status} capture cannot be consumed')
    proposal = (
        _live_proposal(capture.current_revision) if capture.current_revision else None
    )
    if proposal is None:
        raise DecisionRequired('The capture has no reviewed proposal')
    missing = decisions_cover_required_fields(proposal)
    if missing:
        raise DecisionRequired(
            f'Required fields lack reviewed decisions: {", ".join(missing)}'
        )
    decided = {
        row.field_path: row.final_value
        for row in proposal.decisions.all()
        if row.decision in {'accepted', 'edited'}
    }
    for name in REQUIRED_CLOSEOUT_FIELDS:
        if str(closeout.get(name, '')) != str(decided.get(name, '')):
            raise DecisionRequired(
                f'Closeout field {name!r} does not match its reviewed decision'
            )
    return capture


def _consume_capture(capture, closeout_obj):
    """Stamp the capture consumed atomically with the completion."""
    from tasks.closeout_models import CloseoutCaptureStatus

    capture.status = CloseoutCaptureStatus.CONSUMED
    capture.completed_closeout = closeout_obj
    capture.save(update_fields=['status', 'completed_closeout'])


def _create_closeout_effects(closeout_obj):
    """Create durable fan-out intents inside the completion transaction."""
    from django.conf import settings

    from tasks.closeout_models import CloseoutEffect, new_effect_key
    from tasks.services.closeout_effects import execute_pending_effects

    if not getattr(settings, 'AIMMS_CLOSEOUT_WIZARD_ENABLED', False):
        return
    effect_types = ['notification']
    if getattr(settings, 'AIMMS_CLOSEOUT_LEARNING_ENABLED', False):
        effect_types.append('memory_draft')
    for effect_type in effect_types:
        CloseoutEffect.objects.get_or_create(
            effect_key=new_effect_key(closeout_obj.pk, effect_type),
            defaults={
                'closeout': closeout_obj,
                'effect_type': effect_type,
                'payload_hash': closeout_obj.content_hash,
            },
        )
    if getattr(settings, 'AIMMS_CLOSEOUT_EFFECTS_ENABLED', False):
        closeout_id = closeout_obj.pk
        transaction.on_commit(lambda: execute_pending_effects(closeout_id=closeout_id))


@transaction.atomic
def complete_work_order(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    closeout,
    capture_id=None,
    correlation_id=None,
    packet_finalization=False,
):
    """Complete a work order with structured closeout and asset-history writeback.

    This is the one finalization path for *both* standalone and Repair
    Packet-owned work. A packet's work order normally refuses to complete here
    (``PACKET_OWNS_LIFECYCLE``); ``packet_finalization`` is set only by
    ``repair.services.close_repair_packet``, which drives this function and the
    packet's return-to-service transition inside a single transaction. That is
    what stops closing a packet from bypassing structured closeout, parts
    reconciliation, readings and machine-history creation.

    ``packet_finalization`` suppresses exactly one check - packet ownership.
    Permission, scope, version, readiness, open children and the required
    closeout fields are enforced identically on both paths.
    """
    work_order = _locked_work_order(work_order_id)
    closeout = dict(closeout or {})
    closeout_hash = _closeout_hash(closeout)
    payload = {
        'work_order_id': work_order_id,
        'expected_version': expected_version,
        'closeout_hash': closeout_hash,
    }
    if capture_id is not None:
        # Only widen the canonical payload when the new argument is used, so
        # pre-existing stored command hashes keep replaying byte-identically.
        payload['capture_id'] = capture_id
    request_hash = _canonical_hash('complete_work_order', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, COMPLETE_WORKORDER)
    _require_scope(actor, work_order)
    if not packet_finalization:
        _require_no_packet(work_order)
    _validate_transition(work_order.lifecycle_status, WorkOrderLifecycle.COMPLETED)

    # A parent cannot be closed out while a child card is still open (§5.10): the
    # child (e.g. a procurement task for missing parts) is part of the work.
    incomplete = [
        child.pk
        for child in work_order.children.exclude(
            lifecycle_status__in=[
                WorkOrderLifecycle.COMPLETED,
                WorkOrderLifecycle.CANCELED,
            ]
        )
    ]
    if incomplete:
        raise CloseoutError(
            f'Cannot close out while child work orders are open: {incomplete}'
        )

    missing = [
        name
        for name in REQUIRED_CLOSEOUT_FIELDS
        if not str(closeout.get(name, '')).strip()
    ]
    if missing:
        raise CloseoutError(f'Closeout requires: {", ".join(missing)}')

    readiness = evaluate_work_order_readiness(
        work_order,
        action='complete',
        actor=actor,
        expected_version=expected_version,
        packet_finalization=packet_finalization,
    )
    if not readiness.ready:
        raise ReadinessBlocked(readiness)

    capture = None
    if capture_id is not None:
        capture = _validated_capture(work_order, capture_id, closeout)

    now = timezone.now()
    closeout_obj, _created = WorkOrderCloseout.objects.update_or_create(
        work_order=work_order,
        defaults={
            'cause': closeout.get('cause', ''),
            'action': closeout['action'],
            'result': closeout['result'],
            'verification_summary': closeout['verification_summary'],
            'downtime_minutes': closeout.get('downtime_minutes'),
            'follow_up_required': bool(closeout.get('follow_up_required', False)),
            'follow_up': closeout.get('follow_up', ''),
            'completed_by': actor,
            'completed_at': now,
            'content_hash': closeout_hash,
        },
    )

    if work_order.machine_id:
        performed_by = actor.get_full_name() or actor.get_username()
        AssetMaintenanceRecord.objects.update_or_create(
            work_order=work_order,
            defaults={
                'machine_id': work_order.machine_id,
                'date': now.date(),
                'summary': (work_order.title or closeout['action'])[:255],
                'details': _closeout_details(closeout),
                'performed_by': performed_by[:255],
            },
        )

    if capture is not None:
        _consume_capture(capture, closeout_obj)
    _create_closeout_effects(closeout_obj)

    _close_kit(work_order, actor, now)

    from_status = work_order.lifecycle_status
    work_order.lifecycle_status = WorkOrderLifecycle.COMPLETED
    work_order.actual_completed_at = now
    work_order.lifecycle_version = work_order.lifecycle_version + 1

    save_fields = [
        'lifecycle_status',
        'actual_completed_at',
        'lifecycle_version',
        'updated_at',
    ]

    # Closeout is the *only* path that moves a card into the board's terminal
    # (done) column (§5.8), so the board can never show open work that is
    # actually closed out. Done in the same transaction as completion.
    from tasks.models import KanbanColumn

    terminal_key = KanbanColumn.terminal_key()
    if terminal_key and work_order.status != terminal_key:
        work_order.status = terminal_key
        save_fields.append('status')

    work_order.save(update_fields=save_fields)

    return _append_result(
        work_order=work_order,
        actor=actor,
        command='complete_work_order',
        event_type='COMPLETED',
        from_status=from_status,
        to_status=WorkOrderLifecycle.COMPLETED,
        reason=closeout.get('reason', ''),
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={'closeout_id': closeout_obj.pk},
    )
