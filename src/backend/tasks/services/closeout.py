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
from tasks.services.finalization import is_packet_finalization
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


def completion_request_hash(
    *,
    work_order_id,
    actor,
    expected_version,
    closeout,
    capture_id=None,
    packet_intent=None,
):
    """Identify the exact completion, including its packet-owned intent."""
    payload = {
        'work_order_id': work_order_id,
        'expected_version': expected_version,
        'closeout_hash': _closeout_hash(dict(closeout or {})),
    }
    if capture_id is not None:
        payload['capture_id'] = capture_id
    if packet_intent is not None:
        payload['packet_intent'] = packet_intent
    return _canonical_hash('complete_work_order', actor, payload)


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
    packet_finalization=None,
    packet_intent=None,
):
    """Complete a work order with structured closeout and asset-history writeback.

    This is the one finalization path for *both* standalone and Repair
    Packet-owned work. A packet's work order normally refuses to complete here
    (``PACKET_OWNS_LIFECYCLE``); ``packet_finalization`` is set only by
    ``repair.services.close_repair_packet``, which drives this function and the
    packet's return-to-service transition inside a single transaction. That is
    what stops closing a packet from bypassing structured closeout, parts
    reconciliation, readings and machine-history creation.

    ``packet_finalization`` is a capability token naming the packet it speaks
    for, not a flag - a request-borne value cannot satisfy it, and a token
    minted for one packet cannot finalize another's work order. See
    :mod:`tasks.services.finalization`.

    It suppresses exactly one check - packet ownership.
    Permission, scope, version, readiness, open children and the required
    closeout fields are enforced identically on both paths.
    """
    work_order = _locked_work_order(work_order_id)
    closeout = dict(closeout or {})
    closeout_hash = _closeout_hash(closeout)
    request_hash = completion_request_hash(
        work_order_id=work_order_id,
        actor=actor,
        expected_version=expected_version,
        closeout=closeout,
        capture_id=capture_id,
        packet_intent=packet_intent,
    )
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, COMPLETE_WORKORDER)
    _require_scope(actor, work_order)
    if not is_packet_finalization(packet_finalization, work_order):
        _require_no_packet(work_order)
    _validate_transition(work_order.lifecycle_status, WorkOrderLifecycle.COMPLETED)

    # A parent cannot be closed out while a child card is still open (§5.10): the
    # child (e.g. a procurement task for missing parts) is part of the work.
    from tasks.services.scheduling import incomplete_children

    incomplete = list(incomplete_children(work_order.pk).values_list('pk', flat=True))
    if incomplete:
        raise CloseoutError(
            f'Cannot close out while child cards are open: {incomplete}'
        )

    missing = [
        name
        for name in REQUIRED_CLOSEOUT_FIELDS
        if not str(closeout.get(name, '')).strip()
    ]
    if missing:
        raise CloseoutError(f'Closeout requires: {", ".join(missing)}')

    from tasks.workorder_serializers import CompleteCommandSerializer

    serializer = CompleteCommandSerializer(
        data={
            **closeout,
            'expected_version': expected_version,
            'idempotency_key': idempotency_key,
        }
    )
    if not serializer.is_valid():
        raise CloseoutError(str(serializer.errors))
    closeout = {
        name: value
        for name, value in serializer.validated_data.items()
        if name in closeout
    }

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
    previous_closeout = WorkOrderCloseout.objects.filter(work_order=work_order).first()
    if (
        previous_closeout
        and work_order.events.filter(event_type='RECURRENCE_REOPENED').exists()
    ):
        if (
            previous_closeout.verified_at is not None
            or previous_closeout.verified_by_id is not None
        ):
            raise CloseoutError(
                'Accepted closure remains immutable; create linked work.'
            )
        from django.forms.models import model_to_dict

        from tasks.services.recurrence import reopen_blocker

        if reopen_blocker(work_order):
            raise CloseoutError(
                'A retained closure revision is required for dependent provenance.'
            )

        retained = model_to_dict(previous_closeout)
        # The normal immutable save guard remains in place. Only this locked,
        # audited reclosure path can replace an unaccepted execution result.
        values = {
            key: closeout.get(key, default)
            for key, default in [
                ('cause', ''),
                ('action', ''),
                ('result', ''),
                ('verification_summary', ''),
                ('downtime_minutes', None),
                ('follow_up_required', False),
                ('follow_up', ''),
            ]
        }
        values.update(
            completed_by=actor,
            completed_at=now,
            content_hash=closeout_hash,
            version=previous_closeout.version + 1,
        )
        WorkOrderCloseout.objects.filter(
            pk=previous_closeout.pk, verified_at__isnull=True, verified_by__isnull=True
        ).update(**values)
        work_order.events.create(
            event_type='RECLOSURE_RETAINED',
            from_status=work_order.lifecycle_status,
            to_status=work_order.lifecycle_status,
            actor=actor,
            reason='Previous unaccepted closure retained before reclosure',
            correlation_id=correlation_id or uuid.uuid4(),
            metadata={
                'previous_closeout': json.loads(json.dumps(retained, default=str))
            },
        )
        previous_closeout.refresh_from_db()
        closeout_obj = previous_closeout
    else:
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

    # Closeout is the *only* path that moves work into the board's terminal
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
        result_metadata={
            'closeout_id': closeout_obj.pk,
            **({'packet_id': packet_intent['packet_id']} if packet_intent else {}),
        },
    )
