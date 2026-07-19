"""Deterministic Job Kit build service (planning layer).

``build_job_kit`` rolls a work order's immutable primary Procedure application
snapshot up into planned ``JobKitLine`` rows (design contract section 12.1). It is
deterministic and fully idempotent against a fixed application: re-running against
the same primary application creates no duplicate lines. This is the *planning*
layer only -- it performs no stock reservation and touches no allocation
accounting (that is the later ME5 stock-gate slice).
"""

import uuid
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from stock.models import StockItem
from tasks.jobkit_models import (
    ACTIVE_ALLOCATION_STATUSES,
    JobKit,
    JobKitLine,
    JobKitShortage,
    JobKitStatus,
    JobKitSubstitution,
    JobKitSubstitutionStatus,
)
from tasks.models import FulfillmentMode, ProcedureResourceRequirement
from tasks.permissions import (
    APPROVE_JOBKIT_SUBSTITUTION,
    MANAGE_JOBKIT,
    RESERVE_JOBKIT,
    require_permission,
)
from tasks.services.procedure_execution import _command_replay, _record_command
from tasks.services.stock_allocation import StockOverAllocation, mutate_stock_allocation
from tasks.services.work_orders import (
    WorkOrderCommandError,
    _canonical_hash,
    _locked_work_order,
    _require_scope,
    _require_version,
)

_ACTIVE_STATUS_VALUES = [s.value for s in ACTIVE_ALLOCATION_STATUSES]
# Shortage states that still represent an unmet need (not resolved/canceled).
_OPEN_SHORTAGE_STATES = ['open', 'requested', 'ordered', 'partial']
# Allocation states that count as fulfilling a line's required quantity.
_FULFILLED_STATUS_VALUES = ['reserved', 'staged', 'issued', 'consumed']


class JobKitError(WorkOrderCommandError):
    """Base error for Job Kit domain commands."""

    code = 'JOB_KIT_ERROR'


class JobKitBuildError(JobKitError):
    """The Job Kit could not be built from the work order's state."""

    code = 'JOB_KIT_BUILD_ERROR'


class JobKitStaleVersion(JobKitError):  # noqa: N818 - established command error name
    """The supplied Job Kit version did not match current state."""

    code = 'JOB_KIT_STALE_VERSION'


class JobKitStateError(JobKitError):
    """The Job Kit is not in an editable state for this operation."""

    code = 'JOB_KIT_NOT_EDITABLE'


class JobKitLineError(JobKitError):
    """The targeted line cannot be edited through this path."""

    code = 'JOB_KIT_LINE_INVALID'


class JobKitVerificationError(JobKitError):
    """A Right-Part Finder precondition blocked the substitution effect.

    The instance ``code`` preserves the stable RPF consumer code
    (spec section 13.3) so the API envelope can surface it unchanged.
    """

    code = 'PART_VERIFICATION_REQUIRED'

    def __init__(self, message='', *, code=''):
        """Preserve the stable consumer code on the instance."""
        super().__init__(message)
        if code:
            self.code = code


# States in which planned lines may still be edited/added manually.
EDITABLE_KIT_STATUSES = frozenset({'draft', 'short'})

# States from which stock reservation may run (ready re-reserve is a no-op).
RESERVABLE_KIT_STATUSES = frozenset({'draft', 'short', 'ready'})

# Fields a manual line author may set or later amend.
_MANUAL_LINE_FIELDS = frozenset({
    'kind',
    'required_quantity',
    'required',
    'fulfillment_mode',
    'substitution_policy',
    'requires_scan',
    'note',
})


def _locked_kit(work_order_id):
    """Lock the Job Kit row for a work order, or fail closed if absent."""
    kit = JobKit.objects.select_for_update().filter(work_order_id=work_order_id).first()
    if kit is None:
        raise JobKitStateError('Work order has no Job Kit; build it first')
    return kit


def _require_kit_version(kit, expected_version):
    if expected_version is not None and kit.version != expected_version:
        raise JobKitStaleVersion(
            f'Expected Job Kit version {expected_version}, current {kit.version}'
        )


def _require_editable(kit):
    if kit.status not in EDITABLE_KIT_STATUSES:
        raise JobKitStateError(f'Job Kit in state {kit.status!r} cannot be edited')


def _require_reservable(kit):
    if kit.status not in RESERVABLE_KIT_STATUSES:
        raise JobKitStateError(f'Job Kit in state {kit.status!r} cannot be reserved')


@transaction.atomic
def add_manual_line(
    *,
    work_order_id,
    actor,
    kind,
    part_id,
    required_quantity,
    fulfillment_mode,
    expected_version=None,
    substitution_policy='none',
    requires_scan=False,
    note='',
):
    """Append one authorized manual line to an editable Job Kit."""
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, MANAGE_JOBKIT)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)
    _require_kit_version(kit, expected_version)
    _require_editable(kit)
    if required_quantity is None or required_quantity <= 0:
        raise JobKitLineError('Required quantity must be greater than zero')

    next_sequence = (kit.lines.aggregate(m=models.Max('sequence'))['m'] or 0) + 1
    line = JobKitLine.objects.create(
        kit=kit,
        sequence=next_sequence,
        kind=kind,
        requested_part_id=part_id,
        selected_part_id=part_id,
        required_quantity=required_quantity,
        fulfillment_mode=fulfillment_mode,
        substitution_policy=substitution_policy,
        requires_scan=requires_scan,
        note=note,
        source='manual',
    )
    kit.version = kit.version + 1
    kit.save(update_fields=['version', 'updated_at'])
    return line


def _locked_manual_line(kit, line_id):
    line = JobKitLine.objects.select_for_update().filter(pk=line_id, kit=kit).first()
    if line is None:
        raise JobKitLineError('Line does not belong to this Job Kit')
    if line.source != 'manual':
        raise JobKitLineError('Only manual lines can be edited or removed')
    return line


@transaction.atomic
def update_manual_line(
    *, work_order_id, line_id, actor, expected_version=None, **fields
):
    """Amend an editable manual line's mutable planning fields."""
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, MANAGE_JOBKIT)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)
    _require_kit_version(kit, expected_version)
    _require_editable(kit)
    line = _locked_manual_line(kit, line_id)

    updates = {k: v for k, v in fields.items() if k in _MANUAL_LINE_FIELDS}
    if 'required_quantity' in updates and (
        updates['required_quantity'] is None or updates['required_quantity'] <= 0
    ):
        raise JobKitLineError('Required quantity must be greater than zero')
    for name, value in updates.items():
        setattr(line, name, value)
    line.version = line.version + 1
    line.save(update_fields=[*list(updates), 'version', 'updated_at'])
    kit.version = kit.version + 1
    kit.save(update_fields=['version', 'updated_at'])
    return line


@transaction.atomic
def remove_manual_line(*, work_order_id, line_id, actor, expected_version=None):
    """Remove an editable manual line from a Job Kit."""
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, MANAGE_JOBKIT)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)
    _require_kit_version(kit, expected_version)
    _require_editable(kit)
    line = _locked_manual_line(kit, line_id)
    line.delete()
    kit.version = kit.version + 1
    kit.save(update_fields=['version', 'updated_at'])
    return kit


@transaction.atomic
def build_job_kit(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    correlation_id=None,  # gitleaks:allow
):
    """Build or refresh the planned Job Kit for a work order.

    Locks the work order, its primary procedure application, and the kit; requires
    version/permission/scope; and deterministically materialises one procedure
    line per snapshot resource. Manual lines are preserved untouched. Idempotent
    on the shared work-order command ledger.
    """
    work_order = _locked_work_order(work_order_id)
    payload = {'work_order_id': work_order_id, 'expected_version': expected_version}
    request_hash = _canonical_hash('build_job_kit', actor, payload)
    replay = _command_replay(work_order, 'build_job_kit', idempotency_key, request_hash)
    if replay:
        return JobKit.objects.get(pk=replay.metadata['kit_id'])

    _require_version(work_order, expected_version)
    require_permission(actor, MANAGE_JOBKIT)
    _require_scope(actor, work_order)

    application = (
        work_order.procedure_applications
        .select_for_update()
        .select_related('revision')
        .filter(primary=True)
        .first()
    )
    if application is None:
        raise JobKitBuildError(
            'Work order has no applied procedure to build a Job Kit from'
        )

    kit, _created = JobKit.objects.select_for_update().get_or_create(
        work_order=work_order, defaults={'created_by': actor}
    )

    resources = application.snapshot.get('resources', [])
    created_lines = 0
    for index, resource in enumerate(resources, start=1):
        requirement_id = (
            ProcedureResourceRequirement.objects
            .filter(revision_id=application.revision_id, key=resource['key'])
            .values_list('pk', flat=True)
            .first()
        )
        if requirement_id is None:
            # Every snapshot resource is derived from a real requirement on the
            # pinned revision; a missing trace means corrupt state -> fail closed.
            raise JobKitBuildError(
                'Procedure resource cannot be traced to its source requirement'
            )
        _line, line_created = JobKitLine.objects.get_or_create(
            kit=kit,
            source='procedure',
            source_requirement_id=requirement_id,
            defaults={
                'sequence': index,
                'kind': resource['kind'],
                'requested_part_id': resource['part_id'],
                'selected_part_id': resource['part_id'],
                'required_quantity': Decimal(str(resource['quantity'])),
                'required': resource['required'],
                'fulfillment_mode': resource['fulfillment_mode'],
                'substitution_policy': resource['substitution_policy'],
                'requires_scan': resource['requires_scan'],
                'source_snapshot': resource,
            },
        )
        if line_created:
            created_lines += 1

    hash_changed = kit.source_application_hash != application.snapshot_hash
    kit.source_application_hash = application.snapshot_hash
    if kit.built_at is None:
        kit.built_at = timezone.now()
    if hash_changed:
        kit.version = kit.version + 1
    kit.save(
        update_fields=['source_application_hash', 'built_at', 'version', 'updated_at']
    )

    _record_command(
        work_order=work_order,
        actor=actor,
        command='build_job_kit',
        event_type='JOB_KIT_BUILT',
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        correlation_id=correlation_id,
        metadata={
            'kit_id': kit.pk,
            'application_id': application.pk,
            'line_count': kit.lines.count(),
            'created_lines': created_lines,
            'source_application_hash': application.snapshot_hash,
        },
    )
    return kit


def _active_reserved_for_line(line):
    """Return the quantity already actively reserved against a line."""
    total = line.allocations.filter(status__in=_ACTIVE_STATUS_VALUES).aggregate(
        q=models.Sum('quantity')
    )['q']
    return total or Decimal('0')


def _fulfilled_for_line(line):
    """Return the quantity fulfilling a line (active reservations + consumed)."""
    total = line.allocations.filter(status__in=_FULFILLED_STATUS_VALUES).aggregate(
        q=models.Sum('quantity')
    )['q']
    return total or Decimal('0')


def _candidate_stock(line):
    """Return deterministic in-stock candidates for a line's selected part.

    First-valid-committed-reservation-wins under the shared row-lock order; there
    is no silent preemption. Ordering is FIFO by primary key.
    """
    return (
        StockItem.objects
        .filter(part_id=line.selected_part_id)
        .filter(StockItem.IN_STOCK_FILTER)
        .order_by('pk')
    )


def _reconcile_line_shortage(line, shortfall):
    """Replace this line's open shortage with the current unmet quantity."""
    line.shortages.filter(status='open').delete()
    if shortfall > 0:
        JobKitShortage.objects.create(
            line=line,
            quantity=shortfall,
            status='open',
            reason='Insufficient unallocated stock at reservation',
        )


def _derive_kit_status(kit):
    """Derive kit readiness from authoritative shortages against required lines."""
    has_unmet = JobKitShortage.objects.filter(
        line__kit=kit, line__required=True, status__in=_OPEN_SHORTAGE_STATES
    ).exists()
    return JobKitStatus.SHORT if has_unmet else JobKitStatus.READY


@transaction.atomic
def reserve_job_kit(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    correlation_id=None,  # gitleaks:allow
):
    """Atomically reserve stock for every required, consumable Job Kit line.

    Locks the work order, kit, and required lines; reserves stock through the
    shared :func:`mutate_stock_allocation` authority (which holds each stock row
    lock until this transaction commits); records a shortage for any unmet
    remainder; and derives kit readiness. Idempotent on the command ledger.
    """
    work_order = _locked_work_order(work_order_id)
    payload = {'work_order_id': work_order_id, 'expected_version': expected_version}
    request_hash = _canonical_hash('reserve_job_kit', actor, payload)
    replay = _command_replay(
        work_order, 'reserve_job_kit', idempotency_key, request_hash
    )
    if replay:
        return JobKit.objects.get(pk=replay.metadata['kit_id'])

    _require_version(work_order, expected_version)
    require_permission(actor, RESERVE_JOBKIT)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)
    _require_reservable(kit)

    required_lines = list(
        kit.lines
        .select_for_update()
        .filter(required=True, fulfillment_mode=FulfillmentMode.RESERVE_CONSUME)
        .order_by('sequence', 'pk')
    )

    reservations_made = 0
    for line in required_lines:
        needed = Decimal(line.required_quantity) - _active_reserved_for_line(line)
        if needed <= 0:
            _reconcile_line_shortage(line, Decimal('0'))
            continue
        for candidate in _candidate_stock(line):
            if needed <= 0:
                break
            take = min(needed, Decimal(candidate.unallocated_quantity()))
            if take <= 0:
                continue
            try:
                mutate_stock_allocation(
                    stock_item_id=candidate.pk,
                    line_id=line.pk,
                    requested_quantity=take,
                    actor=actor,
                    idempotency_key=f'{idempotency_key}:{line.pk}:{candidate.pk}',
                )
            except StockOverAllocation:
                # A competing writer took it first; try the next candidate.
                continue
            needed -= take
            reservations_made += 1
        _reconcile_line_shortage(line, max(needed, Decimal('0')))

    kit.status = _derive_kit_status(kit)
    kit.version = kit.version + 1
    kit.save(update_fields=['status', 'version', 'updated_at'])

    _record_command(
        work_order=work_order,
        actor=actor,
        command='reserve_job_kit',
        event_type='JOB_KIT_RESERVED',
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        correlation_id=correlation_id,
        metadata={
            'kit_id': kit.pk,
            'reservations_made': reservations_made,
            'status': kit.status,
        },
    )
    return kit


def _locked_line(kit, line_id):
    line = JobKitLine.objects.select_for_update().filter(pk=line_id, kit=kit).first()
    if line is None:
        raise JobKitLineError('Line does not belong to this Job Kit')
    return line


@transaction.atomic
def propose_substitution(
    *, work_order_id, line_id, proposed_part_id, actor, basis=None, reason=''
):
    """Propose a governed alternate part for a line; never sets selected_part."""
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, MANAGE_JOBKIT)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)
    _require_reservable(kit)
    line = _locked_line(kit, line_id)
    if line.substitution_policy == 'none':
        raise JobKitLineError('Substitution is not permitted for this line')
    if proposed_part_id == line.requested_part_id:
        raise JobKitLineError('Proposed part matches the requested part')
    return JobKitSubstitution.objects.create(
        line=line,
        requested_part_id=line.requested_part_id,
        proposed_part_id=proposed_part_id,
        basis=basis or {},
        status=JobKitSubstitutionStatus.PROPOSED,
        proposed_by=actor,
        reason=reason,
    )


def _require_part_verification(
    *, substitution, actor, work_order, confirmed_verification_id
):
    """Enforce the Right-Part Finder precondition for critical categories.

    Disabled deployments (the default) change nothing. When
    ``AIMMS_RPF_JOBKIT_ENFORCEMENT`` is set, a substitution whose requested
    part belongs to a configured critical category requires a current,
    exactly-bound confirmed verification; the RPF service revalidates under
    its own locks and records one immutable use (spec section 13.2).
    """
    from django.conf import settings as django_settings

    if not getattr(django_settings, 'AIMMS_RPF_JOBKIT_ENFORCEMENT', False):
        return None

    critical = set(getattr(django_settings, 'AIMMS_RPF_CRITICAL_CATEGORY_IDS', []))
    category = substitution.requested_part.category
    if category is None:
        raise JobKitVerificationError(
            'A current part verification is required because the requested part '
            'category is unresolved',
            code='PART_VERIFICATION_REQUIRED',
        )
    # A configured critical category covers its whole subtree (spec 13.1:
    # critical selectors may include the category tree).
    ancestor_ids = {row.pk for row in category.get_ancestors(include_self=True)}
    if not ancestor_ids & critical:
        return None

    if not confirmed_verification_id:
        raise JobKitVerificationError(
            'A current part verification is required before this substitution',
            code='PART_VERIFICATION_REQUIRED',
        )

    from part.verification.errors import (
        VerificationCommandError,
        VerificationScopeError,
    )
    from part.verification.schema import ConsumerCodes, HashDomains, hash_canonical
    from part.verification.scope import VerificationScope
    from part.verification.services import validate_and_bind_use
    from tasks.scope import scope_for_work_order

    scope = scope_for_work_order(work_order)

    command_payload = {
        'consumer': 'job_kit',
        'action': 'substitution_decide',
        'substitution': substitution.pk,
        'work_order': work_order.pk,
        'job_kit_line': substitution.line_id,
        'requested_part': substitution.requested_part_id,
        'proposed_part': substitution.proposed_part_id,
        'decision': confirmed_verification_id,
    }

    try:
        return validate_and_bind_use(
            decision_id=confirmed_verification_id,
            actor=actor,
            consumer_kind='job_kit',
            consumer_model='tasks.jobkitsubstitution',
            consumer_object_id=str(substitution.pk),
            consumer_action='substitution_decide',
            expected_purpose='job_kit_substitution',
            expected_work_order_id=work_order.pk,
            expected_job_kit_line_id=substitution.line_id,
            expected_requested_part_id=substitution.requested_part_id,
            expected_selected_part_id=substitution.proposed_part_id,
            expected_scope=VerificationScope(
                customer_id=scope.customer_id, site_key=scope.site_key
            ),
            command_hash=hash_canonical(HashDomains.COMMAND, command_payload),
            idempotency_key=f'jobkit-substitution-{substitution.pk}',
        )
    except VerificationCommandError as exc:
        code = exc.code
        if isinstance(exc, VerificationScopeError) or code.startswith('RPF_SCOPE_'):
            code = ConsumerCodes.PART_VERIFICATION_SCOPE_MISMATCH
        raise JobKitVerificationError(str(exc), code=code) from exc


@transaction.atomic
def decide_substitution(
    *,
    work_order_id,
    substitution_id,
    actor,
    approve,
    reason='',
    confirmed_verification_id=None,
):
    """Approve or reject a proposed substitution under separation of duties.

    Approval is the only authorized path that sets the line's ``selected_part``.
    It is refused while the line holds active reservations for the old part,
    and, for configured critical categories, without a current exact part
    verification.
    """
    work_order = _locked_work_order(work_order_id)
    require_permission(actor, APPROVE_JOBKIT_SUBSTITUTION)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)
    substitution = (
        JobKitSubstitution.objects
        .select_for_update()
        .select_related('line')
        .filter(pk=substitution_id, line__kit=kit)
        .first()
    )
    if substitution is None:
        raise JobKitLineError('Substitution does not belong to this work order')
    if substitution.status != JobKitSubstitutionStatus.PROPOSED:
        raise JobKitStateError('Only proposed substitutions can be decided')
    if substitution.proposed_by_id == actor.pk:
        raise JobKitLineError('The proposer cannot decide their own substitution')

    now = timezone.now()
    if approve:
        _require_reservable(kit)
        line = substitution.line
        if line.allocations.filter(status__in=_ACTIVE_STATUS_VALUES).exists():
            raise JobKitStateError(
                'Release active reservations before substituting the part'
            )
        _require_part_verification(
            substitution=substitution,
            actor=actor,
            work_order=work_order,
            confirmed_verification_id=confirmed_verification_id,
        )
        line.selected_part_id = substitution.proposed_part_id
        line.version = line.version + 1
        line.save(update_fields=['selected_part', 'version', 'updated_at'])
        substitution.status = JobKitSubstitutionStatus.APPROVED
    else:
        substitution.status = JobKitSubstitutionStatus.REJECTED
    substitution.decided_by = actor
    substitution.decided_at = now
    if reason:
        substitution.reason = reason
    substitution.save(update_fields=['status', 'decided_by', 'decided_at', 'reason'])
    return substitution


@transaction.atomic
def release_allocation(*, work_order_id, allocation_id, actor, correlation_id=None):
    """Release one active Job Kit reservation, freeing the stock it held.

    Locks the work order, kit, allocation, and stock row; marks the allocation
    released (terminal, no longer counted); re-derives the affected line's
    shortage and the kit's readiness.
    """
    from stock.models import StockItem as _StockItem
    from tasks.jobkit_models import JobKitAllocation, JobKitAllocationStatus

    work_order = _locked_work_order(work_order_id)
    require_permission(actor, RESERVE_JOBKIT)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)

    allocation = (
        JobKitAllocation.objects
        .select_for_update()
        .select_related('line')
        .filter(pk=allocation_id, line__kit=kit)
        .first()
    )
    if allocation is None:
        raise JobKitLineError('Allocation does not belong to this work order')
    if allocation.status not in _ACTIVE_STATUS_VALUES:
        raise JobKitStateError('Only active reservations can be released')

    # Lock the stock row so availability stays coherent with the release.
    _StockItem.objects.select_for_update().get(pk=allocation.stock_item_id)
    allocation.status = JobKitAllocationStatus.RELEASED
    allocation.disposed_at = timezone.now()
    allocation.save(update_fields=['status', 'disposed_at'])

    line = allocation.line
    if line.required and line.fulfillment_mode == FulfillmentMode.RESERVE_CONSUME:
        shortfall = Decimal(line.required_quantity) - _active_reserved_for_line(line)
        _reconcile_line_shortage(line, max(shortfall, Decimal('0')))

    kit.status = _derive_kit_status(kit)
    kit.version = kit.version + 1
    kit.save(update_fields=['status', 'version', 'updated_at'])
    return allocation


@transaction.atomic
def reconcile_job_kit(*, work_order_id, actor, correlation_id=None):
    """Safely re-derive kit shortages and status from authoritative allocations.

    Idempotent and safe to rerun (design contract 12.7): it recomputes each
    required line's shortage from current fulfillment, flags source-application
    drift, re-derives kit status, and appends an audit event. It never rewrites
    historical allocations or snapshots.
    """
    from tasks.models import WorkOrderEvent

    work_order = _locked_work_order(work_order_id)
    require_permission(actor, MANAGE_JOBKIT)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)
    if kit.status in (JobKitStatus.CLOSED, JobKitStatus.CANCELED):
        return kit

    primary_hash = (
        work_order.procedure_applications
        .filter(primary=True)
        .values_list('snapshot_hash', flat=True)
        .first()
    )
    source_drift = bool(
        kit.source_application_hash
        and primary_hash
        and primary_hash != kit.source_application_hash
    )

    for line in kit.lines.select_for_update().filter(
        required=True, fulfillment_mode=FulfillmentMode.RESERVE_CONSUME
    ):
        shortfall = Decimal(line.required_quantity) - _fulfilled_for_line(line)
        _reconcile_line_shortage(line, max(shortfall, Decimal('0')))

    kit.status = _derive_kit_status(kit)
    kit.version = kit.version + 1
    kit.save(update_fields=['status', 'version', 'updated_at'])

    WorkOrderEvent.objects.create(
        work_order=work_order,
        event_type='JOB_KIT_RECONCILED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        actor=actor,
        correlation_id=correlation_id or uuid.uuid4(),
        metadata={'kit_id': kit.pk, 'status': kit.status, 'source_drift': source_drift},
    )
    return kit


@transaction.atomic
def link_po_to_shortage(*, work_order_id, shortage_id, purchase_order_line_id, actor):
    """Link a real purchase-order line to a shortage (procurement handoff).

    ``ordered`` requires a real ``PurchaseOrderLineItem``; a stub effect can never
    mark a shortage ordered (FR-JK-013).
    """
    from order.models import PurchaseOrderLineItem

    work_order = _locked_work_order(work_order_id)
    require_permission(actor, MANAGE_JOBKIT)
    _require_scope(actor, work_order)
    kit = _locked_kit(work_order_id)
    shortage = (
        JobKitShortage.objects
        .select_for_update()
        .filter(pk=shortage_id, line__kit=kit)
        .first()
    )
    if shortage is None:
        raise JobKitLineError('Shortage does not belong to this work order')
    po_line = PurchaseOrderLineItem.objects.filter(pk=purchase_order_line_id).first()
    if po_line is None:
        raise JobKitLineError('Purchase order line not found')

    shortage.purchase_order_line = po_line
    shortage.status = 'ordered'
    shortage.save(update_fields=['purchase_order_line', 'status'])
    return shortage
