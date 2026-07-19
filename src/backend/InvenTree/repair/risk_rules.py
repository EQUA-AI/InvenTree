"""Deterministic Risk Radar rule library.

Rules are pure batch evaluators: they receive an already scope-proven
queryset plus immutable configuration and yield bounded pages of
:class:`RiskCandidate` rows. Rules never touch finding state — the engine
in ``risk_services`` owns upsert/resolve/supersede semantics.

Honest-source discipline: work-order rules consume only blockers actually
emitted by the live ``READINESS_CHECKS`` registry; packet safety rules use
the lifecycle-owner services ``can_advance()`` / ``can_return_to_service()``
rather than the divergent gate projection. Declared-but-unemitted readiness
constants are not facts, so ``WO_BLOCKED_PARTS`` ships dormant until the kit
checks are registered, and the RFQ / external-sync codes are reserved for
their companion features.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from django.conf import settings
from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Max,
    Min,
    Q,
    QuerySet,
)
from django.utils import timezone

CADENCE_MINUTES_15 = 'minutes_15'
CADENCE_HOURLY = 'hourly'
CADENCE_DAILY = 'daily'

CADENCES = (CADENCE_MINUTES_15, CADENCE_HOURLY, CADENCE_DAILY)

WATERMARK_FULL_SNAPSHOT = 'full_snapshot'

DEFAULT_PAGE_SIZE = 200

# Deep links may target only routes that actually exist in the frontend
# router today. Job Kit, approval-record, and return-to-service surfaces do
# not exist yet, so no rule may advertise them (RR-ADR-008).
ACTION_ROUTES = {
    'repair_packet': '/repair/packets/{id}/',
    'work_order': '/tasks/work-orders/{id}',
    'purchase_order': '/purchasing/purchase-order/{id}/',
    'asset_machine': '/machines/machine/{id}/',
    'part': '/part/{id}/',
}


def make_action_link(label: str, target_kind: str, target_id) -> dict | None:
    """Build an action link only when a governed frontend route exists."""
    route = ACTION_ROUTES.get(target_kind)
    if route is None:
        return None
    return {
        'label': label,
        'target_kind': target_kind,
        'target_id': str(target_id),
        'route': route.format(id=target_id),
    }


@dataclass(frozen=True)
class RiskCandidate:
    """One evaluated risky condition, staged before promotion.

    ``fingerprint_parts`` is the condition discriminator only; the engine
    prepends scope, rule code, rule version and the fingerprint schema
    version. Mutable display text must never appear in it (FR-RR-005).
    """

    fingerprint_parts: tuple[str, ...]
    source_model: str
    source_id: str
    title: str
    summary: str
    severity_factors: dict[str, Any]
    evidence: dict[str, Any]
    source_as_of: datetime
    condition_started_at: datetime
    due_at: datetime | None = None
    action_links: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class RiskEvaluationPage:
    """A bounded page of candidates; only the final page may be complete."""

    candidates: tuple[RiskCandidate, ...]
    source_as_of: datetime
    next_watermark: dict
    complete: bool


class RiskRule:
    """Base class for deterministic rules (RR-ADR-002).

    Subclasses declare stable metadata and implement :meth:`evaluate`.
    Every MVP rule resolves by absence and therefore declares the
    ``full_snapshot`` watermark strategy: it may emit ``complete=True``
    only after covering its entire scoped source.
    """

    code = ''
    version = 1
    category = ''
    cadence = CADENCE_HOURLY
    watermark_strategy = WATERMARK_FULL_SNAPSHOT
    source_kind = ''
    critical_rule = False
    severity_base = 'medium'
    default_config: dict[str, Any] = {}

    def evaluate(
        self, *, queryset: QuerySet, scope, config: dict, watermark: dict, actor=None
    ) -> Iterator[RiskEvaluationPage]:
        """Yield bounded candidate pages for the scoped queryset."""
        raise NotImplementedError

    def _snapshot_pages(
        self, candidates: list[RiskCandidate], as_of: datetime
    ) -> Iterator[RiskEvaluationPage]:
        """Yield full-snapshot pages; the final page carries completeness."""
        watermark = {'strategy': self.watermark_strategy, 'as_of': as_of.isoformat()}
        if not candidates:
            yield RiskEvaluationPage(
                candidates=(),
                source_as_of=as_of,
                next_watermark=watermark,
                complete=True,
            )
            return
        for start in range(0, len(candidates), DEFAULT_PAGE_SIZE):
            page = candidates[start : start + DEFAULT_PAGE_SIZE]
            yield RiskEvaluationPage(
                candidates=tuple(page),
                source_as_of=as_of,
                next_watermark=watermark,
                complete=start + DEFAULT_PAGE_SIZE >= len(candidates),
            )

    def _factors(self, **extra) -> dict[str, Any]:
        """Return the documented severity factor tuple for a candidate."""
        factors = {'base': self.severity_base}
        factors.update(extra)
        return factors


def normalize_datetime(value: datetime | None) -> datetime | None:
    """Normalize a datetime to the active timezone convention.

    InvenTree runs with ``USE_TZ`` enabled in production and disabled under
    test, so radar timestamps must match whichever convention is active
    rather than assuming aware UTC.
    """
    if value is None:
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, UTC) if settings.USE_TZ else value
    return value if settings.USE_TZ else timezone.make_naive(value, UTC)


_aware = normalize_datetime


def _date_to_datetime(value) -> datetime:
    """Convert a date to a UTC-midnight datetime in the active convention."""
    naive = datetime.combine(value, time.min)
    return timezone.make_aware(naive, UTC) if settings.USE_TZ else naive


def _age_hours(started: datetime, now: datetime) -> float:
    """Return the age of a condition in hours, never negative."""
    return max((now - started).total_seconds() / 3600.0, 0.0)


# Only genuine lifecycle-transition events may reset a status-entry clock.
# Both source apps also stamp ``to_status`` with the *current* (unchanged)
# status on audit events (ASSIGNED, closeout captures, GENERATED, ...), so
# matching on ``to_status`` alone would let unrelated activity suppress
# stall detection indefinitely.
_WORK_ORDER_TRANSITION_EVENTS = Q(event_type__in=('TRANSITION', 'COMPLETED'))

_PACKET_TRANSITION_EVENTS = Q(event_type__in=('created', 'advanced', 'canceled'))


def _latest_transitions(
    model, fk_name: str, ids: list, to_status: str, event_filter: Q | None = None
) -> dict:
    """Return ``{fk_id: latest created_at}`` for events entering a status."""
    rows = model.objects.filter(**{f'{fk_name}__in': ids, 'to_status': to_status})
    if event_filter is not None:
        rows = rows.filter(event_filter)
    rows = rows.exclude(from_status=F('to_status'))
    rows = rows.values(fk_name).annotate(latest=Max('created_at'))
    return {row[fk_name]: row['latest'] for row in rows}


class ApprovalSlaBreachRule(RiskRule):
    """Approvals sitting in review past the configured SLA.

    SLA thresholds do not exist anywhere in the approvals app — they are
    radar rule configuration, not discovered facts. The clock starts at the
    latest ``opened`` event of the current review episode (falling back to
    ``created_at`` for eventless rows) and the fingerprint binds the
    approval id, so one review episode is one finding.
    """

    code = 'APPROVAL_SLA_BREACH'
    category = 'approvals'
    cadence = CADENCE_HOURLY
    source_kind = 'approval'
    severity_base = 'high'
    default_config = {'sla_hours': 24}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for in-review approvals past SLA."""
        from approvals.models import ApprovalStatus

        now = timezone.now()
        sla_hours = float(config.get('sla_hours', 24))
        rows = (
            queryset
            .filter(status=ApprovalStatus.IN_REVIEW)
            .annotate(
                opened_at=Max(
                    'events__timestamp', filter=Q(events__event_type='opened')
                )
            )
            .order_by('created_at')
        )
        candidates: list[RiskCandidate] = []
        for approval in rows.iterator():
            opened = _aware(approval.opened_at) or _aware(approval.created_at)
            if opened is None:
                continue
            due_at = opened + timedelta(hours=sla_hours)
            if now < due_at:
                continue
            age = _age_hours(opened, now)
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(approval.pk),),
                    source_model='approvals.Approval',
                    source_id=str(approval.pk),
                    title='Approval in review past SLA',
                    summary=(
                        f'Approval has been in review for {age:.0f}h '
                        f'(SLA {sla_hours:.0f}h): {approval.summary or ""}'.strip()
                    ),
                    severity_factors=self._factors(
                        due_breached=True,
                        age_hours=round(age, 2),
                        risk_tier=approval.risk_tier,
                    ),
                    evidence={
                        'approval_id': str(approval.pk),
                        'status': approval.status,
                        'action_type': approval.action_type,
                        'opened_at': opened.isoformat(),
                        'risk_tier': approval.risk_tier,
                    },
                    source_as_of=now,
                    condition_started_at=opened,
                    due_at=due_at,
                )
            )
        yield from self._snapshot_pages(candidates, now)


class ApprovalRevalidationFailedRule(RiskRule):
    """Approvals stuck in changes-requested after a failed revalidation.

    Matches when the approval status is ``changes_requested`` and its
    latest relevant event is ``revalidation_failed`` or ``resume_failed``;
    a later ``revised`` / ``opened`` / ``approved`` event (or any terminal
    status, excluded by the status filter) clears it (§4.4).
    """

    code = 'APPROVAL_REVALIDATION_FAILED'
    category = 'approvals'
    cadence = CADENCE_HOURLY
    source_kind = 'approval'
    severity_base = 'high'
    default_config = {}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for revalidation-failed approvals."""
        from approvals.models import ApprovalStatus

        now = timezone.now()
        rows = (
            queryset
            .filter(status=ApprovalStatus.CHANGES_REQUESTED)
            .annotate(
                last_failed=Max(
                    'events__timestamp',
                    filter=Q(
                        events__event_type__in=['revalidation_failed', 'resume_failed']
                    ),
                ),
                last_cleared=Max(
                    'events__timestamp',
                    filter=Q(events__event_type__in=['revised', 'opened', 'approved']),
                ),
            )
            .order_by('created_at')
        )
        candidates: list[RiskCandidate] = []
        for approval in rows.iterator():
            failed_at = _aware(approval.last_failed)
            cleared_at = _aware(approval.last_cleared)
            if failed_at is None:
                continue
            if cleared_at is not None and cleared_at >= failed_at:
                continue
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(approval.pk),),
                    source_model='approvals.Approval',
                    source_id=str(approval.pk),
                    title='Approval revalidation failed',
                    summary=(
                        'Approval requires changes after a failed revalidation: '
                        f'{approval.summary or ""}'
                    ).strip(': '),
                    severity_factors=self._factors(
                        age_hours=round(_age_hours(failed_at, now), 2),
                        risk_tier=approval.risk_tier,
                    ),
                    evidence={
                        'approval_id': str(approval.pk),
                        'status': approval.status,
                        'action_type': approval.action_type,
                        'revalidation_failed_at': failed_at.isoformat(),
                        'risk_tier': approval.risk_tier,
                    },
                    source_as_of=now,
                    condition_started_at=failed_at,
                )
            )
        yield from self._snapshot_pages(candidates, now)


class JobKitShortageAgingRule(RiskRule):
    """Open job-kit shortages past the configured episode age threshold.

    The reconciler deletes and recreates open shortage rows, so identity
    binds the stable ``JobKitLine.key`` (a UUID that survives rebuilds)
    plus the open-shortage condition — never the churned shortage-row pk.
    The rule emits *every* open shortage; the engine's ``open_min_age_hours``
    gate decides when a new episode is old enough to open, and an
    already-open episode stays alive across delete/recreate churn because
    its fingerprint is present in every complete scan (no false
    resolution when the reconciler resets ``created_at``).
    """

    code = 'JOBKIT_SHORTAGE_AGING'
    category = 'parts'
    cadence = CADENCE_HOURLY
    source_kind = 'job_kit_shortage'
    severity_base = 'medium'
    default_config = {'open_min_age_hours': 24}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for every open shortage (engine gates opening)."""
        now = timezone.now()
        age_hours = float(config.get('open_min_age_hours', 24))
        rows = (
            queryset
            .filter(status='open')
            .select_related(
                'line', 'line__kit', 'line__kit__work_order', 'line__requested_part'
            )
            .order_by('created_at')
        )
        candidates: list[RiskCandidate] = []
        for shortage in rows.iterator():
            started = _aware(shortage.created_at)
            if started is None:
                continue
            line = shortage.line
            work_order = line.kit.work_order
            part_name = getattr(line.requested_part, 'name', '') or ''
            links = [make_action_link('Open work order', 'work_order', work_order.pk)]
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(line.key), 'open'),
                    source_model='tasks.JobKitLine',
                    source_id=str(line.key),
                    title=f'Job kit shortage aging: {part_name}'.strip(': '),
                    summary=(
                        f'Open shortage of {shortage.quantity} for kit line '
                        f'{line.sequence} on work order '
                        f'{work_order.reference or work_order.pk}'
                    ),
                    severity_factors=self._factors(
                        due_breached=_age_hours(started, now) >= age_hours,
                        age_hours=round(_age_hours(started, now), 2),
                    ),
                    evidence={
                        'job_kit_line_key': str(line.key),
                        'shortage_quantity': str(shortage.quantity),
                        'shortage_status': shortage.status,
                        'work_order_id': work_order.pk,
                        'work_order_reference': work_order.reference or '',
                        'purchase_order_line_id': shortage.purchase_order_line_id,
                    },
                    source_as_of=now,
                    condition_started_at=started,
                    due_at=started + timedelta(hours=age_hours),
                    action_links=[link for link in links if link],
                )
            )
        yield from self._snapshot_pages(candidates, now)


class PoLateRule(RiskRule):
    """Unreceived purchase-order lines past their target date.

    One finding per unreceived line, fingerprinted by line id. The due date
    is the line ``target_date``, falling back explicitly to the order
    ``target_date``; a row with neither date is ineligible.
    """

    code = 'PO_LATE'
    category = 'procurement'
    cadence = CADENCE_HOURLY
    source_kind = 'purchase_order_line'
    severity_base = 'medium'
    default_config = {}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for late, still-open PO lines."""
        from order.status_codes import PurchaseOrderStatusGroups

        now = timezone.now()
        today = now.date()
        rows = (
            queryset
            .filter(
                order__status__in=PurchaseOrderStatusGroups.OPEN,
                received__lt=F('quantity'),
            )
            .select_related('order', 'order__supplier')
            .order_by('pk')
        )
        candidates: list[RiskCandidate] = []
        for line in rows.iterator():
            due_date = line.target_date or line.order.target_date
            if due_date is None or due_date >= today:
                continue
            due_at = _date_to_datetime(due_date)
            supplier = getattr(line.order.supplier, 'name', '') or ''
            links = [
                make_action_link('Open purchase order', 'purchase_order', line.order.pk)
            ]
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(line.pk),),
                    source_model='order.PurchaseOrderLineItem',
                    source_id=str(line.pk),
                    title=f'Purchase order line late ({line.order.reference})',
                    summary=(
                        f'{line.received}/{line.quantity} received; target date '
                        f'{due_date.isoformat()} has passed'
                        + (f' (supplier {supplier})' if supplier else '')
                    ),
                    severity_factors=self._factors(
                        due_breached=True, age_hours=round(_age_hours(due_at, now), 2)
                    ),
                    evidence={
                        'purchase_order_id': line.order.pk,
                        'purchase_order_reference': line.order.reference,
                        'line_id': line.pk,
                        'quantity': str(line.quantity),
                        'received': str(line.received),
                        'target_date': due_date.isoformat(),
                        'supplier': supplier,
                    },
                    source_as_of=now,
                    condition_started_at=due_at,
                    due_at=due_at,
                    action_links=[link for link in links if link],
                )
            )
        yield from self._snapshot_pages(candidates, now)


def _packet_status_entered(packets: list) -> dict:
    """Return ``{packet_id: entered_at}`` for each packet's current status."""
    from repair.models import RepairPacketEvent

    entered: dict[int, datetime] = {}
    by_status: dict[str, list[int]] = {}
    for packet in packets:
        by_status.setdefault(packet.status, []).append(packet.pk)
    for status, ids in by_status.items():
        latest = _latest_transitions(
            RepairPacketEvent, 'packet_id', ids, status, _PACKET_TRANSITION_EVENTS
        )
        entered.update(latest)
    for packet in packets:
        if packet.pk not in entered or entered[packet.pk] is None:
            entered[packet.pk] = packet.created_at
    return entered


class WoBlockedSafetyRule(RiskRule):
    """Packets whose safety state blocks progress or return to service.

    Consumes the lifecycle-owner services ``can_advance()`` and
    ``can_return_to_service()`` — not the divergent
    ``unsatisfied_blocking_gates()`` projection — and links to the live
    packet surface instead of freezing gate state as truth. The standalone
    readiness registry does not emit safety codes yet, so packets are the
    only authoritative safety source today.
    """

    code = 'WO_BLOCKED_SAFETY'
    category = 'safety'
    cadence = CADENCE_MINUTES_15
    source_kind = 'repair_packet'
    critical_rule = True
    severity_base = 'critical'
    default_config = {}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for safety-blocked packets."""
        from repair.models import PacketStatus

        now = timezone.now()
        packets = list(
            queryset
            .filter(status__in=[PacketStatus.APPROVED, PacketStatus.EXECUTING])
            .select_related('machine', 'work_order')
            .order_by('pk')
        )
        entered = _packet_status_entered(packets)
        candidates: list[RiskCandidate] = []
        for packet in packets:
            conditions: list[tuple[str, str, str]] = []
            ok, reason = packet.can_advance()
            if not ok:
                conditions.append(('advance', 'Safety gate blocks progress', reason))
            elif packet.status == PacketStatus.EXECUTING:
                rts_ok, rts_reason = packet.can_return_to_service()
                if not rts_ok:
                    conditions.append(('rts', 'Return to service blocked', rts_reason))
            started = _aware(entered.get(packet.pk)) or now
            for discriminator, title, reason_snapshot in conditions:
                links = [
                    make_action_link(
                        'Open packet safety panel', 'repair_packet', packet.pk
                    )
                ]
                if packet.work_order_id:
                    links.append(
                        make_action_link(
                            'Open work order', 'work_order', packet.work_order_id
                        )
                    )
                candidates.append(
                    RiskCandidate(
                        fingerprint_parts=(str(packet.pk), discriminator),
                        source_model='repair.RepairPacket',
                        source_id=str(packet.pk),
                        title=f'{title}: {packet.reference or packet.pk}',
                        summary=reason_snapshot,
                        severity_factors=self._factors(
                            criticality=packet.criticality,
                            age_hours=round(_age_hours(started, now), 2),
                        ),
                        evidence={
                            'packet_id': packet.pk,
                            'packet_reference': packet.reference or '',
                            'packet_status': packet.status,
                            'blocked_action': discriminator,
                            'reason_snapshot': reason_snapshot,
                            'status_entered_at': started.isoformat(),
                        },
                        source_as_of=now,
                        condition_started_at=started,
                        action_links=[link for link in links if link],
                    )
                )
        yield from self._snapshot_pages(candidates, now)


# Readiness-backed rules consume only codes the live registry emits; the
# mapping from lifecycle state to the next natural action mirrors the live
# ``_ACTION_STATES`` table in ``tasks/services/readiness.py``.
_READINESS_ACTION_FOR_STATUS = {
    'ready': 'start',
    'on_hold': 'resume',
    'in_progress': 'verify',
    'verifying': 'complete',
}


class _ReadinessBlockerRule(RiskRule):
    """Shared evaluator for rules that consume live readiness blockers.

    Consuming the authoritative readiness service is inherently per-record
    (the registry checks are not expressible as one queryset), so this
    evaluator streams bounded chunks — yielding an (incomplete) page after
    each chunk keeps the engine's lease heartbeat running during long
    evaluations instead of materializing the whole scope first.
    """

    source_kind = 'work_order'
    cadence = CADENCE_MINUTES_15
    blocker_codes: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    chunk_size = 100

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield chunked candidate pages for matching live blockers."""
        from tasks.models import WorkOrderEvent

        now = timezone.now()
        watermark_value = {
            'strategy': self.watermark_strategy,
            'as_of': now.isoformat(),
        }
        work_orders = list(
            queryset
            .filter(lifecycle_status__in=self.statuses)
            .select_related('machine', 'customer', 'assigned_to')
            .order_by('pk')
        )
        for start in range(0, len(work_orders), self.chunk_size):
            chunk = work_orders[start : start + self.chunk_size]
            entered: dict[int, datetime] = {}
            by_status: dict[str, list[int]] = {}
            for wo in chunk:
                by_status.setdefault(wo.lifecycle_status, []).append(wo.pk)
            for status, ids in by_status.items():
                entered.update(
                    _latest_transitions(
                        WorkOrderEvent,
                        'work_order_id',
                        ids,
                        status,
                        _WORK_ORDER_TRANSITION_EVENTS,
                    )
                )
            yield RiskEvaluationPage(
                candidates=tuple(self._chunk_candidates(chunk, entered, now, actor)),
                source_as_of=now,
                next_watermark=watermark_value,
                complete=False,
            )
        yield RiskEvaluationPage(
            candidates=(),
            source_as_of=now,
            next_watermark=watermark_value,
            complete=True,
        )

    def _chunk_candidates(self, chunk, entered, now, actor):
        """Build candidates for one bounded chunk of work orders."""
        from tasks.services.readiness import evaluate_work_order_readiness

        candidates: list[RiskCandidate] = []
        for wo in chunk:
            action = _READINESS_ACTION_FOR_STATUS.get(wo.lifecycle_status)
            if action is None:
                continue
            readiness = evaluate_work_order_readiness(wo, action=action, actor=actor)
            started = _aware(entered.get(wo.pk)) or _aware(wo.created_at) or now
            for blocker in readiness.blockers:
                if blocker.code not in self.blocker_codes:
                    continue
                links = [make_action_link('Open work order', 'work_order', wo.pk)]
                candidates.append(
                    RiskCandidate(
                        fingerprint_parts=(str(wo.pk), blocker.code),
                        source_model='tasks.KanbanCard',
                        source_id=str(wo.pk),
                        title=f'{blocker.code}: {wo.reference or wo.pk}',
                        summary=blocker.message,
                        severity_factors=self._factors(
                            age_hours=round(_age_hours(started, now), 2)
                        ),
                        evidence={
                            'work_order_id': wo.pk,
                            'work_order_reference': wo.reference or '',
                            'lifecycle_status': wo.lifecycle_status,
                            'blocked_action': action,
                            'blocker_code': blocker.code,
                            'blocker_message': blocker.message,
                            'readiness_policy_version': readiness.policy_version,
                        },
                        source_as_of=now,
                        condition_started_at=started,
                        action_links=[link for link in links if link],
                    )
                )
        return candidates


class WoBlockedAssignmentRule(_ReadinessBlockerRule):
    """Work orders blocked by missing assignee or asset (live codes only)."""

    code = 'WO_BLOCKED_ASSIGNMENT'
    category = 'operations'
    severity_base = 'medium'
    blocker_codes = ('ASSIGNEE_REQUIRED', 'ASSET_REQUIRED')
    statuses = ('ready', 'on_hold', 'in_progress', 'verifying')
    default_config = {}


class WoBlockedProcedureRule(_ReadinessBlockerRule):
    """Work orders blocked by required or failed procedure steps."""

    code = 'WO_BLOCKED_PROCEDURE'
    category = 'operations'
    severity_base = 'high'
    blocker_codes = ('STEP_REQUIRED', 'STEP_FAILED')
    statuses = ('in_progress', 'verifying')
    default_config = {}


class PacketStalledRule(RiskRule):
    """Packets sitting in one lifecycle state past the stall threshold.

    Measures from the latest packet event that entered the current status
    (creation time for an eventless legacy row) and fingerprints the packet
    id plus status, so a lifecycle move starts a fresh episode.
    """

    code = 'PACKET_STALLED'
    category = 'operations'
    cadence = CADENCE_HOURLY
    source_kind = 'repair_packet'
    severity_base = 'medium'
    default_config = {'stall_hours': 48}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for stalled packets."""
        from repair.models import TERMINAL_PACKET_STATUSES

        now = timezone.now()
        stall_hours = float(config.get('stall_hours', 48))
        packets = list(
            queryset
            .exclude(status__in=TERMINAL_PACKET_STATUSES)
            .select_related('machine')
            .order_by('pk')
        )
        entered = _packet_status_entered(packets)
        candidates: list[RiskCandidate] = []
        for packet in packets:
            started = _aware(entered.get(packet.pk)) or now
            age = _age_hours(started, now)
            if age < stall_hours:
                continue
            links = [make_action_link('Open packet', 'repair_packet', packet.pk)]
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(packet.pk), packet.status),
                    source_model='repair.RepairPacket',
                    source_id=str(packet.pk),
                    title=(
                        f'Packet stalled in {packet.status}: '
                        f'{packet.reference or packet.pk}'
                    ),
                    summary=(
                        f'No lifecycle progress for {age:.0f}h '
                        f'(threshold {stall_hours:.0f}h)'
                    ),
                    severity_factors=self._factors(
                        criticality=packet.criticality,
                        age_hours=round(age, 2),
                        due_breached=True,
                    ),
                    evidence={
                        'packet_id': packet.pk,
                        'packet_reference': packet.reference or '',
                        'packet_status': packet.status,
                        'status_entered_at': started.isoformat(),
                    },
                    source_as_of=now,
                    condition_started_at=started,
                    due_at=started + timedelta(hours=stall_hours),
                    action_links=[link for link in links if link],
                )
            )
        yield from self._snapshot_pages(candidates, now)


class CloseoutMissingRule(RiskRule):
    """Work orders stuck in VERIFYING or completed without a closeout.

    Entering ``VERIFYING`` is detected from the latest work-order event
    with ``to_status='verifying'`` (the live transition path emits generic
    ``TRANSITION`` events, so the status field — not the event type — is
    the authoritative discriminator).
    """

    code = 'CLOSEOUT_MISSING'
    category = 'closeout'
    cadence = CADENCE_DAILY
    source_kind = 'work_order'
    severity_base = 'medium'
    default_config = {'verifying_hours': 24}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for verification stalls and closeout anomalies."""
        from tasks.models import WorkOrderEvent

        now = timezone.now()
        verifying_hours = float(config.get('verifying_hours', 24))
        candidates: list[RiskCandidate] = []

        verifying = list(queryset.filter(lifecycle_status='verifying').order_by('pk'))
        entered = _latest_transitions(
            WorkOrderEvent,
            'work_order_id',
            [wo.pk for wo in verifying],
            'verifying',
            _WORK_ORDER_TRANSITION_EVENTS,
        )
        for wo in verifying:
            started = _aware(entered.get(wo.pk)) or _aware(wo.created_at) or now
            age = _age_hours(started, now)
            if age < verifying_hours:
                continue
            links = [make_action_link('Open work order', 'work_order', wo.pk)]
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(wo.pk), 'verifying'),
                    source_model='tasks.KanbanCard',
                    source_id=str(wo.pk),
                    title=f'Work order stuck in verification: {wo.reference or wo.pk}',
                    summary=(
                        f'In VERIFYING for {age:.0f}h without completion '
                        f'(threshold {verifying_hours:.0f}h)'
                    ),
                    severity_factors=self._factors(
                        age_hours=round(age, 2), due_breached=True
                    ),
                    evidence={
                        'work_order_id': wo.pk,
                        'work_order_reference': wo.reference or '',
                        'lifecycle_status': wo.lifecycle_status,
                        'entered_verifying_at': started.isoformat(),
                    },
                    source_as_of=now,
                    condition_started_at=started,
                    due_at=started + timedelta(hours=verifying_hours),
                    action_links=[link for link in links if link],
                )
            )

        missing = queryset.filter(
            lifecycle_status='completed', structured_closeout__isnull=True
        ).order_by('pk')
        for wo in missing.iterator():
            completed_at = _aware(wo.actual_completed_at) or _aware(wo.updated_at)
            started = completed_at or now
            links = [make_action_link('Open work order', 'work_order', wo.pk)]
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(wo.pk), 'missing_closeout'),
                    source_model='tasks.KanbanCard',
                    source_id=str(wo.pk),
                    title=(
                        f'Completed without structured closeout: '
                        f'{wo.reference or wo.pk}'
                    ),
                    summary='Work order is completed but has no closeout record',
                    severity_factors=self._factors(
                        age_hours=round(_age_hours(started, now), 2)
                    ),
                    evidence={
                        'work_order_id': wo.pk,
                        'work_order_reference': wo.reference or '',
                        'lifecycle_status': wo.lifecycle_status,
                        'completed_at': completed_at.isoformat()
                        if completed_at
                        else None,
                    },
                    source_as_of=now,
                    condition_started_at=started,
                    action_links=[link for link in links if link],
                )
            )
        yield from self._snapshot_pages(candidates, now)


class StockBelowCriticalRule(RiskRule):
    """Configured or scope-relevant parts below their minimum stock level.

    Uses the native ``Part.get_stock_count()`` / ``minimum_stock`` semantics
    (the same signal the per-part low-stock notification consumes) and adds
    the aggregated operational view that signal lacks.
    """

    code = 'STOCK_BELOW_CRITICAL'
    category = 'stock'
    cadence = CADENCE_DAILY
    source_kind = 'part_stock'
    severity_base = 'medium'
    default_config = {'part_ids': []}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for parts under their minimum stock."""
        from part import filters as part_filters

        now = timezone.now()
        part_ids = config.get('part_ids') or []
        variant_query = part_filters.variant_stock_query()
        rows = (
            queryset
            .filter(minimum_stock__gt=0)
            .annotate(
                in_stock=part_filters.annotate_total_stock(),
                variant_stock=part_filters.annotate_variant_quantity(
                    variant_query, reference='quantity'
                ),
            )
            .annotate(
                total_in_stock=ExpressionWrapper(
                    F('in_stock') + F('variant_stock'), output_field=DecimalField()
                )
            )
            .order_by('pk')
        )
        if part_ids:
            rows = rows.filter(pk__in=[int(pk) for pk in part_ids])
        candidates: list[RiskCandidate] = []
        for part in rows.iterator():
            total = part.total_in_stock
            if total >= part.minimum_stock:
                continue
            links = [make_action_link('Open part', 'part', part.pk)]
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(part.pk),),
                    source_model='part.Part',
                    source_id=str(part.pk),
                    title=f'Stock below minimum: {part.name}',
                    summary=(
                        f'In stock {total} is below the configured minimum '
                        f'{part.minimum_stock}'
                    ),
                    severity_factors=self._factors(
                        shortfall=str(part.minimum_stock - total)
                    ),
                    evidence={
                        'part_id': part.pk,
                        'part_name': part.name,
                        'total_stock': str(total),
                        'minimum_stock': str(part.minimum_stock),
                    },
                    source_as_of=now,
                    condition_started_at=now,
                    action_links=[link for link in links if link],
                )
            )
        yield from self._snapshot_pages(candidates, now)


class AssetRepeatMaintenanceRule(RiskRule):
    """Machines accumulating repeated maintenance activity in a window.

    Activity signal only: ``AssetMaintenanceRecord`` has no typed failure
    or outcome field, so this rule may not claim failure semantics.
    """

    code = 'ASSET_REPEAT_MAINTENANCE'
    category = 'assets'
    cadence = CADENCE_DAILY
    source_kind = 'asset_machine'
    severity_base = 'medium'
    default_config = {'window_days': 30, 'threshold': 3}

    def evaluate(self, *, queryset, scope, config, watermark, actor=None):
        """Yield candidates for machines with repeated maintenance."""
        now = timezone.now()
        window_days = int(config.get('window_days', 30))
        threshold = int(config.get('threshold', 3))
        cutoff = (now - timedelta(days=window_days)).date()
        rows = (
            queryset
            .annotate(
                recent_records=Count(
                    'maintenance_records',
                    filter=Q(maintenance_records__date__gte=cutoff),
                ),
                earliest_recent=Min(
                    'maintenance_records__date',
                    filter=Q(maintenance_records__date__gte=cutoff),
                ),
            )
            .filter(recent_records__gte=threshold)
            .order_by('pk')
        )
        candidates: list[RiskCandidate] = []
        for machine in rows.iterator():
            started = (
                _date_to_datetime(machine.earliest_recent)
                if machine.earliest_recent
                else now
            )
            links = [make_action_link('Open machine', 'asset_machine', machine.pk)]
            candidates.append(
                RiskCandidate(
                    fingerprint_parts=(str(machine.pk),),
                    source_model='assets.AssetMachine',
                    source_id=str(machine.pk),
                    title=f'Repeat maintenance activity: {machine.name}',
                    summary=(
                        f'{machine.recent_records} maintenance records in the '
                        f'last {window_days} days (threshold {threshold})'
                    ),
                    severity_factors=self._factors(record_count=machine.recent_records),
                    evidence={
                        'machine_id': machine.pk,
                        'machine_name': machine.name,
                        'record_count': machine.recent_records,
                        'window_days': window_days,
                    },
                    source_as_of=now,
                    condition_started_at=started,
                    action_links=[link for link in links if link],
                )
            )
        yield from self._snapshot_pages(candidates, now)


@dataclass(frozen=True)
class RuleSpec:
    """Registration record for one rule code.

    ``evaluator`` is ``None`` for reserved / dormant codes: their rule
    definitions may exist (so operators can see them in rule health) but
    they never evaluate, and rule health reports why.
    """

    code: str
    category: str
    cadence: str
    source_kind: str
    severity_base: str
    critical_rule: bool
    default_config: dict
    requires_flags: tuple[str, ...] = ()
    evaluator: RiskRule | None = None
    dormant_reason: str = ''


def _spec(rule: RiskRule, requires_flags: tuple[str, ...] = ()) -> RuleSpec:
    """Build a spec from a live rule instance."""
    return RuleSpec(
        code=rule.code,
        category=rule.category,
        cadence=rule.cadence,
        source_kind=rule.source_kind,
        severity_base=rule.severity_base,
        critical_rule=rule.critical_rule,
        default_config=dict(rule.default_config),
        requires_flags=requires_flags,
        evaluator=rule,
    )


RULE_SPECS: dict[str, RuleSpec] = {
    spec.code: spec
    for spec in (
        _spec(WoBlockedSafetyRule()),
        _spec(WoBlockedAssignmentRule(), requires_flags=('AIMMS_WORK_ORDERS_ENABLED',)),
        _spec(WoBlockedProcedureRule(), requires_flags=('AIMMS_WORK_ORDERS_ENABLED',)),
        _spec(
            JobKitShortageAgingRule(),
            requires_flags=('AIMMS_WORK_ORDERS_ENABLED', 'AIMMS_JOB_KITS_ENABLED'),
        ),
        _spec(
            PoLateRule(),
            requires_flags=('AIMMS_WORK_ORDERS_ENABLED', 'AIMMS_JOB_KITS_ENABLED'),
        ),
        _spec(ApprovalSlaBreachRule()),
        _spec(ApprovalRevalidationFailedRule()),
        _spec(PacketStalledRule()),
        _spec(CloseoutMissingRule(), requires_flags=('AIMMS_WORK_ORDERS_ENABLED',)),
        _spec(StockBelowCriticalRule()),
        _spec(AssetRepeatMaintenanceRule()),
        RuleSpec(
            code='WO_BLOCKED_PARTS',
            category='parts',
            cadence=CADENCE_MINUTES_15,
            source_kind='work_order',
            severity_base='high',
            critical_rule=False,
            default_config={},
            requires_flags=('AIMMS_WORK_ORDERS_ENABLED', 'AIMMS_JOB_KITS_ENABLED'),
            evaluator=None,
            dormant_reason=(
                'Dormant: the readiness registry does not emit JOB_KIT_SHORT / '
                'JOB_KIT_NOT_STAGED yet; declared constants are not facts'
            ),
        ),
        RuleSpec(
            code='RFQ_REPLY_OVERDUE',
            category='procurement',
            cadence=CADENCE_HOURLY,
            source_kind='purchase_order_line',
            severity_base='medium',
            critical_rule=False,
            default_config={},
            requires_flags=('AIMMS_RFQ_AUTOMATION_ENABLED',),
            evaluator=None,
            dormant_reason='Reserved: RFQ automation (#3) has not shipped',
        ),
        RuleSpec(
            code='EXTERNAL_SYNC_FAILED',
            category='sync',
            cadence=CADENCE_MINUTES_15,
            source_kind='work_order',
            severity_base='high',
            critical_rule=True,
            default_config={},
            requires_flags=('AIMMS_EXTERNAL_SYNC_ENABLED',),
            evaluator=None,
            dormant_reason='Reserved: external writeback sync (#18) has not shipped',
        ),
    )
}
