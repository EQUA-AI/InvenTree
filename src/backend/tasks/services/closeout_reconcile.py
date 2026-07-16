"""Closeout parts-usage reconciliation and readings (Feature #15).

Reconciliation compares what custody says happened with what the closeout
claims. It never moves stock: consumption stays with the existing Job Kit
custody commands (real ``take_stock`` removals), and walk-up usage must bind
to a real, already-created stock-tracking entry. Readings retain raw text and
normalize deterministically; ambiguity blocks promotion, never guesses.
"""

import uuid
from decimal import Decimal

from django.db import transaction

from tasks.closeout_models import (
    CloseoutPartUsage,
    CloseoutPartUsageState,
    CloseoutReading,
    CloseoutReadingEvidence,
    CloseoutReadingState,
    PartUsageDisposition,
)
from tasks.jobkit_models import (
    ACTIVE_ALLOCATION_STATUSES,
    JobKit,
    JobKitAllocation,
    JobKitAllocationStatus,
)
from tasks.permissions import require_permission
from tasks.services.closeout_capture import _require_wizard
from tasks.services.closeout_extraction import (
    NORMALIZATION_RULE_VERSION,
    normalize_reading,
)
from tasks.services.work_orders import (
    WorkOrderCommandError,
    _locked_work_order,
    _require_scope,
)

RECONCILE_CLOSEOUT_PARTS = 'tasks.reconcile_closeout_parts'
CAPTURE_CLOSEOUT = 'tasks.capture_closeout'
VERIFY_CLOSEOUT = 'tasks.verify_closeout'

_SEEDED_ALLOCATION_STATUSES = [
    *[status.value for status in ACTIVE_ALLOCATION_STATUSES],
    JobKitAllocationStatus.CONSUMED.value,
    JobKitAllocationStatus.RETURNED.value,
    JobKitAllocationStatus.EXCEPTION.value,
]


class PartVarianceUnresolved(WorkOrderCommandError):  # noqa: N818 - established command error name
    """A usage row differs from custody truth without a disposition."""

    code = 'PART_VARIANCE_UNRESOLVED'


class SerializedConsumeUnsupported(WorkOrderCommandError):  # noqa: N818 - established command error name
    """Serialized stock cannot be consumed by custody; handling is manual."""

    code = 'SERIALIZED_CONSUME_UNSUPPORTED'


class ReconciliationError(WorkOrderCommandError):
    """A reconciliation command could not be applied."""

    code = 'RECONCILIATION_INVALID'


class ReadingError(WorkOrderCommandError):
    """A reading command could not be applied."""

    code = 'READING_INVALID'


class NumericAmbiguityBlocking(WorkOrderCommandError):  # noqa: N818 - established command error name
    """An ambiguous numeric cannot be promoted without human correction."""

    code = 'NUMERIC_AMBIGUITY_BLOCKING'


def _sync_row_from_allocation(row: CloseoutPartUsage, allocation: JobKitAllocation):
    """Refresh custody-derived values on one row; never touches stock."""
    row.part_id = allocation.line.selected_part_id
    row.stock_item_id = allocation.stock_item_id
    row.planned_quantity = allocation.line.required_quantity
    row.issued_quantity = allocation.quantity
    if allocation.status == JobKitAllocationStatus.CONSUMED:
        row.used_quantity = allocation.quantity
        row.stock_tracking_id = allocation.stock_tracking_id
        if not row.disposition:
            row.disposition = PartUsageDisposition.CONSUMED
            row.state = CloseoutPartUsageState.RECONCILED
    elif allocation.status == JobKitAllocationStatus.RETURNED:
        if not row.disposition:
            row.used_quantity = Decimal('0')
            row.disposition = PartUsageDisposition.RETURNED
            row.state = CloseoutPartUsageState.RECONCILED
    elif allocation.status == JobKitAllocationStatus.EXCEPTION:
        row.state = CloseoutPartUsageState.BLOCKED


@transaction.atomic
def refresh_closeout_reconciliation(*, work_order_id, actor, correlation_id=None):
    """Idempotently re-derive usage rows from custody truth.

    Creates one row per seeded allocation, re-flags rows whose custody state
    drifted, and appends a counts-only ``RECONCILIATION_REFRESHED`` event. It
    never mutates stock or allocations.
    """
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_scope(actor, work_order)

    kit = JobKit.objects.filter(work_order=work_order).first()
    created = updated = flagged = 0
    if kit is not None:
        allocations = (
            JobKitAllocation.objects
            .select_for_update()
            .select_related('line')
            .filter(line__kit=kit, status__in=_SEEDED_ALLOCATION_STATUSES)
        )
        for allocation in allocations:
            row, was_created = CloseoutPartUsage.objects.get_or_create(
                work_order=work_order, allocation=allocation, defaults={'source': 'kit'}
            )
            if was_created:
                created += 1
            drifted = (
                not was_created
                and row.state == CloseoutPartUsageState.RECONCILED
                and (
                    row.issued_quantity != allocation.quantity
                    or (
                        allocation.status == JobKitAllocationStatus.CONSUMED
                        and row.stock_tracking_id != allocation.stock_tracking_id
                    )
                )
            )
            if drifted:
                row.state = CloseoutPartUsageState.PENDING
                row.disposition = ''
                flagged += 1
            _sync_row_from_allocation(row, allocation)
            row.version = row.version + 1 if not was_created else row.version
            row.save()
            if not was_created:
                updated += 1

    from tasks.models import WorkOrderEvent

    WorkOrderEvent.objects.create(
        work_order=work_order,
        event_type='RECONCILIATION_REFRESHED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        actor=actor,
        reason='',
        correlation_id=correlation_id or uuid.uuid4(),
        metadata={'created': created, 'updated': updated, 'flagged': flagged},
    )
    return {'created': created, 'updated': updated, 'flagged': flagged}


@transaction.atomic
def add_walkup_usage(
    *, work_order_id, actor, stock_item_id, used_quantity, stock_tracking_id, reason=''
):
    """Record walk-up usage bound to a real, existing stock transaction.

    The stock movement itself must already exist (an authorized stock
    adjustment created the tracking entry); this command only binds it into
    the reconciliation view (FR-CO-006).
    """
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, RECONCILE_CLOSEOUT_PARTS)
    _require_scope(actor, work_order)

    from stock.models import StockItem, StockItemTracking

    stock_item = StockItem.objects.filter(pk=stock_item_id).first()
    if stock_item is None:
        raise ReconciliationError('Walk-up stock item does not exist')
    tracking = StockItemTracking.objects.filter(
        pk=stock_tracking_id, item=stock_item
    ).first()
    if tracking is None:
        raise ReconciliationError(
            'Walk-up usage requires a real stock-tracking entry for that item'
        )
    quantity = Decimal(str(used_quantity))
    if quantity <= 0:
        raise ReconciliationError('Walk-up quantity must be positive')

    return CloseoutPartUsage.objects.create(
        work_order=work_order,
        part_id=stock_item.part_id,
        stock_item=stock_item,
        used_quantity=quantity,
        disposition=PartUsageDisposition.CONSUMED,
        variance_reason=reason,
        stock_tracking_id=tracking.pk,
        source='walkup',
        resolved_by=actor,
        state=CloseoutPartUsageState.RECONCILED,
    )


@transaction.atomic
def add_narrative_candidate(*, work_order_id, actor, candidate_text):
    """Surface an unresolved narrative part mention for explicit binding."""
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_scope(actor, work_order)
    text = (candidate_text or '').strip()
    if not text:
        raise ReconciliationError('A candidate requires its narrative text')
    return CloseoutPartUsage.objects.create(
        work_order=work_order,
        source='narrative',
        candidate_text=text[:255],
        state=CloseoutPartUsageState.BLOCKED,
    )


_VARIANCE_DISPOSITIONS = {
    PartUsageDisposition.RETURNED,
    PartUsageDisposition.SCRAPPED,
    PartUsageDisposition.SPARE_INSTALLED,
    PartUsageDisposition.CORRECTION,
}


@transaction.atomic
def resolve_part_usage(
    *,
    work_order_id,
    row_id,
    actor,
    disposition,
    reason='',
    used_quantity=None,
    expected_row_version=None,
):
    """Resolve one usage row against custody truth with an explicit disposition."""
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, RECONCILE_CLOSEOUT_PARTS)
    _require_scope(actor, work_order)

    # Lock only the usage row (of=('self',)); allocation is a nullable FK and
    # PostgreSQL rejects FOR UPDATE across its outer join.
    row = (
        CloseoutPartUsage.objects
        .select_for_update(of=('self',))
        .select_related('allocation__line')
        .filter(pk=row_id, work_order=work_order)
        .first()
    )
    if row is None:
        raise ReconciliationError('Usage row does not belong to this work order')
    if expected_row_version is not None and row.version != expected_row_version:
        raise ReconciliationError('The usage row changed since it was loaded')
    if disposition not in PartUsageDisposition.values:
        raise ReconciliationError(f'Unknown disposition: {disposition!r}')

    allocation = row.allocation
    if disposition == PartUsageDisposition.CONSUMED:
        if allocation is None:
            raise ReconciliationError(
                'Kit consumption resolves through the allocation row'
            )
        if allocation.status != JobKitAllocationStatus.CONSUMED:
            raise PartVarianceUnresolved(
                'Consumption must be performed by the custody consume command first'
            )
        if allocation.stock_tracking_id is None:
            raise PartVarianceUnresolved(
                'A consumed quantity requires its stock-tracking entry'
            )
        row.used_quantity = allocation.quantity
        row.stock_tracking_id = allocation.stock_tracking_id
    elif disposition == PartUsageDisposition.SERIALIZED_MANUAL:
        if not reason.strip():
            raise SerializedConsumeUnsupported(
                'Serialized handling requires an explicit reason'
            )
        if used_quantity is not None:
            row.used_quantity = Decimal(str(used_quantity))
    elif disposition == PartUsageDisposition.DISMISSED:
        if row.source != 'narrative':
            raise ReconciliationError('Only narrative candidates can be dismissed')
        if not reason.strip():
            raise ReconciliationError('Dismissing a candidate requires a reason')
        row.used_quantity = Decimal('0')
    else:
        if used_quantity is not None:
            row.used_quantity = Decimal(str(used_quantity))
        if row.used_quantity is None:
            row.used_quantity = Decimal('0')
        issued = row.issued_quantity
        if issued is not None and row.used_quantity != issued and not reason.strip():
            raise PartVarianceUnresolved(
                'Quantity variance requires a disposition reason'
            )
        if disposition not in _VARIANCE_DISPOSITIONS:
            raise ReconciliationError(
                f'Disposition {disposition!r} cannot resolve this row'
            )

    row.disposition = disposition
    row.variance_reason = reason
    row.resolved_by = actor
    row.state = CloseoutPartUsageState.RECONCILED
    row.version += 1
    row.save()
    return row


def unresolved_usage_rows(work_order):
    """Usage rows still blocking reconciliation, split by kind."""
    rows = CloseoutPartUsage.objects.filter(work_order=work_order).exclude(
        state=CloseoutPartUsageState.RECONCILED
    )
    candidates = [row for row in rows if row.source == 'narrative']
    variances = [row for row in rows if row.source != 'narrative']
    return variances, candidates


@transaction.atomic
def record_reading(
    *,
    work_order_id,
    actor,
    label,
    raw_text,
    unit='',
    phase='after',
    required=False,
    expected_min=None,
    expected_max=None,
    step_execution_id=None,
    source_spans=None,
    evidence_attachment_ids=None,
):
    """Record one closeout reading with deterministic normalization."""
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_scope(actor, work_order)

    label = (label or '').strip()
    if not label:
        raise ReadingError('A reading label is required')
    if phase not in {'before', 'after'}:
        raise ReadingError('Reading phase must be before or after')

    step_execution = None
    if step_execution_id is not None:
        from tasks.models import WorkOrderStepExecution

        step_execution = WorkOrderStepExecution.objects.filter(
            pk=step_execution_id, application__work_order=work_order
        ).first()
        if step_execution is None:
            raise ReadingError('Step execution does not belong to this work order')

    value, warnings = normalize_reading(raw_text)
    expected_min = Decimal(str(expected_min)) if expected_min is not None else None
    expected_max = Decimal(str(expected_max)) if expected_max is not None else None

    if value is None:
        state = CloseoutReadingState.PENDING
    elif (expected_min is not None and value < expected_min) or (
        expected_max is not None and value > expected_max
    ):
        state = CloseoutReadingState.FAILED
    else:
        state = CloseoutReadingState.VERIFIED

    reading = CloseoutReading.objects.create(
        work_order=work_order,
        step_execution=step_execution,
        label=label,
        phase=phase,
        raw_text=(raw_text or '')[:64],
        source_spans=source_spans or [],
        warnings=warnings,
        value=value,
        unit=(unit or '').strip(),
        expected_min=expected_min,
        expected_max=expected_max,
        required=required,
        normalization_rule_version=NORMALIZATION_RULE_VERSION,
        verification_state=state,
        recorded_by=actor,
    )
    for attachment_id in evidence_attachment_ids or []:
        link_reading_evidence(reading=reading, attachment_id=attachment_id, actor=actor)
    return reading


def link_reading_evidence(*, reading, attachment_id, actor):
    """Bind one real attachment row as evidence; bare metadata is not proof."""
    from common.models import Attachment

    attachment = Attachment.objects.filter(pk=attachment_id).first()
    if attachment is None:
        raise ReadingError('Evidence attachment does not exist')
    return CloseoutReadingEvidence.objects.create(
        reading=reading, attachment=attachment, linked_by=actor
    )


READING_DISPOSITIONS = ('retest', 'deviation', 'supervisor_review')


@transaction.atomic
def disposition_reading(*, work_order_id, reading_id, actor, disposition, reason):
    """Resolve a failed or ambiguous reading with an explicit disposition."""
    _require_wizard()
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, CAPTURE_CLOSEOUT)
    _require_scope(actor, work_order)

    reading = (
        CloseoutReading.objects
        .select_for_update()
        .filter(pk=reading_id, work_order=work_order)
        .first()
    )
    if reading is None:
        raise ReadingError('Reading does not belong to this work order')
    if reading.verification_state not in {
        CloseoutReadingState.FAILED,
        CloseoutReadingState.PENDING,
    }:
        raise ReadingError(
            f'A {reading.verification_state} reading cannot be dispositioned'
        )
    if disposition not in READING_DISPOSITIONS:
        raise ReadingError(f'Unknown reading disposition: {disposition!r}')
    if not (reason or '').strip():
        raise ReadingError('A reading disposition requires a reason')
    if disposition == 'supervisor_review':
        require_permission(actor, VERIFY_CLOSEOUT)
    if disposition == 'deviation':
        from tasks.models import WorkOrderDeviation

        WorkOrderDeviation.objects.create(
            work_order=work_order,
            category='closeout_reading',
            reason=reason,
            actor=actor,
        )

    reading.verification_state = CloseoutReadingState.DISPOSITIONED
    reading.disposition_reason = reason
    reading.save(update_fields=['verification_state', 'disposition_reason'])

    replacement = None
    if disposition == 'retest':
        replacement = CloseoutReading.objects.create(
            work_order=work_order,
            step_execution=reading.step_execution,
            label=reading.label,
            phase=reading.phase,
            raw_text='',
            value=None,
            unit=reading.unit,
            expected_min=reading.expected_min,
            expected_max=reading.expected_max,
            required=reading.required,
            normalization_rule_version=NORMALIZATION_RULE_VERSION,
            verification_state=CloseoutReadingState.PENDING,
            recorded_by=actor,
        )
    return reading, replacement


def unresolved_required_readings(work_order):
    """Required readings still blocking completion (pending or failed)."""
    return list(
        CloseoutReading.objects.filter(
            work_order=work_order,
            required=True,
            verification_state__in=[
                CloseoutReadingState.PENDING,
                CloseoutReadingState.FAILED,
            ],
        )
    )


def promote_reading_candidate(*, raw_text):
    """Guard direct promotion of an extracted reading candidate.

    Ambiguous numerics fail with ``NUMERIC_AMBIGUITY_BLOCKING`` until a human
    corrects the source or supplies an unambiguous manual value (FR-CO-009).
    """
    value, warnings = normalize_reading(raw_text)
    if value is None or 'numeric_ambiguity' in warnings:
        raise NumericAmbiguityBlocking(
            'The reading numeric is ambiguous; correct the source or enter it manually'
        )
    return value
