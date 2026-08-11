"""Fixture-driven unit tests for the Risk Radar rule library."""

import uuid
from datetime import timedelta

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .risk_models import RiskFindingState
from .risk_rules import (
    AnomalyUnaddressedRule,
    ApprovalRevalidationFailedRule,
    ApprovalSlaBreachRule,
    AssetRepeatMaintenanceRule,
    CloseoutMissingRule,
    JobKitShortageAgingRule,
    PacketStalledRule,
    PoLateRule,
    ScheduleInfeasibleRule,
    ShiftBriefingRule,
    StockBelowCriticalRule,
    WoBlockedAssignmentRule,
    WoBlockedProcedureRule,
    WoBlockedSafetyRule,
    make_action_link,
)
from .risk_scope import get_source_adapter
from .risk_testing import RISK_FLAGS, RiskEnvMixin


def evaluate(rule, adapter_kind, actor, scope, config=None):
    """Run one rule over its scoped adapter queryset and flatten pages."""
    queryset = get_source_adapter(adapter_kind).queryset_for_scope(
        actor=actor, scope=scope
    )
    candidates = []
    complete = False
    for page in rule.evaluate(
        queryset=queryset,
        scope=scope,
        config=config or dict(rule.default_config),
        watermark={},
        actor=actor,
    ):
        candidates.extend(page.candidates)
        complete = complete or page.complete
    return candidates, complete


@override_settings(**RISK_FLAGS)
class RuleTestBase(RiskEnvMixin, TestCase):
    """Shared environment for rule fixtures."""

    def setUp(self):
        """Build the two-tenant (customer + client) environment.

        Work orders carry explicit customers and stay on ``self.scope``;
        machine-anchored sources (packets, approvals via packets, parts,
        machines) belong to ``self.client_tenant`` and are evaluated under
        ``self.client_scope``.
        """
        self.build_env()
        self.addCleanup(self.teardown_scopes)


class ActionLinkTest(TestCase):
    """Corrective actions are links to existing governed routes only."""

    def test_known_targets_build_links(self):
        """Existing routes produce deep links."""
        link = make_action_link('Open packet', 'repair_packet', 7)
        assert link is not None
        self.assertEqual(link['route'], '/repair/packets/7/')

    def test_unknown_targets_are_suppressed(self):
        """Missing governed surfaces produce no link (RR-ADR-008)."""
        self.assertIsNone(make_action_link('Open kit', 'job_kit', 7))
        self.assertIsNone(make_action_link('Open approval', 'approval', 'x'))


class ApprovalSlaBreachRuleTest(RuleTestBase):
    """APPROVAL_SLA_BREACH measures the current review episode."""

    def _approval(self, opened_hours_ago=None):
        """Create an in-review approval linked into scope."""
        from approvals.models import Approval, ApprovalEvent, ApprovalStatus
        from repair.models import RepairPacket, RepairPacketApprovalLink

        approval = Approval.objects.create(
            action_type='purchase_order',
            summary='Buy bearings',
            payload={},
            status=ApprovalStatus.IN_REVIEW,
            idempotency_key=uuid.uuid4().hex,
        )
        RepairPacketApprovalLink.objects.create(
            packet=RepairPacket.objects.create(fault_summary='x', machine=self.machine),
            approval=approval,
        )
        if opened_hours_ago is not None:
            event = ApprovalEvent.objects.create(
                approval=approval, event_type='opened', event_payload={}
            )
            ApprovalEvent.objects.filter(pk=event.pk).update(
                timestamp=timezone.now() - timedelta(hours=opened_hours_ago)
            )
        return approval

    def test_breach_past_sla(self):
        """An episode older than the SLA yields one candidate."""
        approval = self._approval(opened_hours_ago=30)
        candidates, complete = evaluate(
            ApprovalSlaBreachRule(),
            'approval',
            self.actor,
            self.client_scope,
            config={'sla_hours': 24},
        )
        self.assertTrue(complete)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_id, str(approval.pk))
        self.assertTrue(candidates[0].severity_factors['due_breached'])

    def test_within_sla_is_silent(self):
        """A recent episode yields nothing."""
        self._approval(opened_hours_ago=2)
        candidates, _ = evaluate(
            ApprovalSlaBreachRule(),
            'approval',
            self.actor,
            self.client_scope,
            config={'sla_hours': 24},
        )
        self.assertEqual(candidates, [])

    def test_latest_opened_event_wins(self):
        """A re-opened review restarts the SLA clock."""
        from approvals.models import ApprovalEvent

        approval = self._approval(opened_hours_ago=48)
        recent = ApprovalEvent.objects.create(
            approval=approval, event_type='opened', event_payload={}
        )
        ApprovalEvent.objects.filter(pk=recent.pk).update(
            timestamp=timezone.now() - timedelta(hours=1)
        )
        candidates, _ = evaluate(
            ApprovalSlaBreachRule(),
            'approval',
            self.actor,
            self.client_scope,
            config={'sla_hours': 24},
        )
        self.assertEqual(candidates, [])

    def test_eventless_row_uses_created_at(self):
        """Legacy rows without events measure from creation."""
        from approvals.models import Approval

        approval = self._approval(opened_hours_ago=None)
        Approval.objects.filter(pk=approval.pk).update(
            created_at=timezone.now() - timedelta(hours=30)
        )
        candidates, _ = evaluate(
            ApprovalSlaBreachRule(),
            'approval',
            self.actor,
            self.client_scope,
            config={'sla_hours': 24},
        )
        self.assertEqual(len(candidates), 1)


class ApprovalRevalidationFailedRuleTest(RuleTestBase):
    """APPROVAL_REVALIDATION_FAILED matches only unrecovered failures."""

    def _approval(self, events):
        """Create an in-scope changes-requested approval with events."""
        import uuid as uuid_mod

        from approvals.models import Approval, ApprovalEvent, ApprovalStatus
        from repair.models import RepairPacket, RepairPacketApprovalLink

        approval = Approval.objects.create(
            action_type='purchase_order',
            summary='spend',
            payload={},
            status=ApprovalStatus.CHANGES_REQUESTED,
            idempotency_key=uuid_mod.uuid4().hex,
        )
        RepairPacketApprovalLink.objects.create(
            packet=RepairPacket.objects.create(fault_summary='x', machine=self.machine),
            approval=approval,
        )
        for hours_ago, event_type in events:
            event = ApprovalEvent.objects.create(
                approval=approval, event_type=event_type, event_payload={}
            )
            ApprovalEvent.objects.filter(pk=event.pk).update(
                timestamp=timezone.now() - timedelta(hours=hours_ago)
            )
        return approval

    def test_unrecovered_failure_matches(self):
        """A latest revalidation failure yields one candidate."""
        approval = self._approval([(5, 'revalidation_failed')])
        candidates, _ = evaluate(
            ApprovalRevalidationFailedRule(), 'approval', self.actor, self.client_scope
        )
        self.assertEqual(
            [candidate.source_id for candidate in candidates], [str(approval.pk)]
        )

    def test_later_revision_clears(self):
        """A later revised/opened event clears the condition."""
        self._approval([(5, 'revalidation_failed'), (1, 'revised')])
        candidates, _ = evaluate(
            ApprovalRevalidationFailedRule(), 'approval', self.actor, self.client_scope
        )
        self.assertEqual(candidates, [])


class TransitionDetectionTest(RuleTestBase):
    """Status-entry clocks ignore non-transition audit events."""

    def test_audit_events_do_not_suppress_verifying_stall(self):
        """An ASSIGNED audit event must not reset the VERIFYING clock."""
        from tasks.models import WorkOrderEvent

        wo = self.make_work_order(lifecycle='verifying', machine=self.machine)
        entered = WorkOrderEvent.objects.create(
            work_order=wo,
            event_type='TRANSITION',
            from_status='in_progress',
            to_status='verifying',
            correlation_id=uuid.uuid4(),
        )
        WorkOrderEvent.objects.filter(pk=entered.pk).update(
            created_at=timezone.now() - timedelta(hours=48)
        )
        # Recent audit event stamping the unchanged status must not count.
        WorkOrderEvent.objects.create(
            work_order=wo,
            event_type='ASSIGNED',
            from_status='verifying',
            to_status='verifying',
            correlation_id=uuid.uuid4(),
        )
        candidates, _ = evaluate(
            CloseoutMissingRule(),
            'work_order',
            self.actor,
            self.scope,
            config={'verifying_hours': 24},
        )
        self.assertEqual(
            [candidate.fingerprint_parts for candidate in candidates],
            [(str(wo.pk), 'verifying')],
        )

    def test_generated_event_does_not_reset_packet_stall(self):
        """A GENERATED audit event must not reset the packet stall clock."""
        from repair.models import RepairPacket, RepairPacketEvent

        packet = RepairPacket.objects.create(fault_summary='f', machine=self.machine)
        RepairPacket.objects.filter(pk=packet.pk).update(
            created_at=timezone.now() - timedelta(hours=72)
        )
        RepairPacketEvent.objects.create(
            packet=packet,
            event_type='generated',
            from_status='draft',
            to_status='draft',
        )
        candidates, _ = evaluate(
            PacketStalledRule(),
            'repair_packet',
            self.actor,
            self.client_scope,
            config={'stall_hours': 48},
        )
        self.assertEqual(
            [candidate.fingerprint_parts for candidate in candidates],
            [(str(packet.pk), 'draft')],
        )


class JobKitShortageAgingRuleTest(RuleTestBase):
    """JOBKIT_SHORTAGE_AGING binds the stable line key."""

    def _shortage(self, *, age_hours, status='open'):
        """Create a shortage for an in-scope work order."""
        from tasks.jobkit_models import JobKit, JobKitLine, JobKitShortage

        from part.models import Part

        part = Part.objects.create(name=f'Part-{uuid.uuid4().hex[:8]}', description='d')
        kit = JobKit.objects.create(
            work_order=self.make_work_order(), created_by=self.actor
        )
        line = JobKitLine.objects.create(
            kit=kit,
            sequence=1,
            kind='part',
            requested_part=part,
            selected_part=part,
            required_quantity=2,
            fulfillment_mode='reserve_consume',
            source='manual',
        )
        shortage = JobKitShortage.objects.create(line=line, quantity=2, status=status)
        JobKitShortage.objects.filter(pk=shortage.pk).update(
            created_at=timezone.now() - timedelta(hours=age_hours)
        )
        return line

    def test_open_shortage_emits_line_keyed_candidate(self):
        """Every open shortage emits a line-keyed candidate.

        The engine's ``open_min_age_hours`` gate owns the opening
        threshold, so continuously-open episodes survive the reconciler's
        delete/recreate churn (which resets ``created_at``) instead of
        being falsely resolved by an emit-time age filter.
        """
        line = self._shortage(age_hours=30)
        young = self._shortage(age_hours=1)
        candidates, _ = evaluate(
            JobKitShortageAgingRule(),
            'job_kit_shortage',
            self.actor,
            self.scope,
            config={'open_min_age_hours': 24},
        )
        by_key = {candidate.source_id: candidate for candidate in candidates}
        self.assertEqual(set(by_key), {str(line.key), str(young.key)})
        self.assertEqual(
            by_key[str(line.key)].fingerprint_parts, (str(line.key), 'open')
        )
        self.assertTrue(by_key[str(line.key)].severity_factors['due_breached'])
        self.assertFalse(by_key[str(young.key)].severity_factors['due_breached'])

    def test_nonopen_is_silent(self):
        """Non-open shortages yield nothing."""
        self._shortage(age_hours=48, status='ordered')
        candidates, _ = evaluate(
            JobKitShortageAgingRule(),
            'job_kit_shortage',
            self.actor,
            self.scope,
            config={'open_min_age_hours': 24},
        )
        self.assertEqual(candidates, [])


class PoLateRuleTest(RuleTestBase):
    """PO_LATE is one finding per unreceived late line."""

    def _line(self, *, line_target=None, order_target=None, quantity=10, received=0):
        """Create a PO line linked into scope via a shortage."""
        from tasks.jobkit_models import JobKit, JobKitLine, JobKitShortage

        from company.models import Company
        from order.models import PurchaseOrder, PurchaseOrderLineItem
        from part.models import Part

        supplier = Company.objects.create(
            name=f'Supp-{uuid.uuid4().hex[:8]}', is_supplier=True
        )
        order = PurchaseOrder.objects.create(
            supplier=supplier,
            reference=f'PO-{uuid.uuid4().hex[:8]}',
            target_date=order_target,
        )
        line = PurchaseOrderLineItem.objects.create(
            order=order, quantity=quantity, received=received, target_date=line_target
        )
        part = Part.objects.create(name=f'Part-{uuid.uuid4().hex[:8]}', description='d')
        kit = JobKit.objects.create(
            work_order=self.make_work_order(), created_by=self.actor
        )
        kit_line = JobKitLine.objects.create(
            kit=kit,
            sequence=1,
            kind='part',
            requested_part=part,
            selected_part=part,
            required_quantity=1,
            fulfillment_mode='reserve_consume',
            source='manual',
        )
        JobKitShortage.objects.create(
            line=kit_line, quantity=1, status='ordered', purchase_order_line=line
        )
        return line

    def test_late_line_matches_with_line_date(self):
        """A passed line target date with unreceived quantity matches."""
        yesterday = (timezone.now() - timedelta(days=1)).date()
        line = self._line(line_target=yesterday)
        candidates, _ = evaluate(
            PoLateRule(), 'purchase_order_line', self.actor, self.scope
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].source_id, str(line.pk))

    def test_falls_back_to_order_target(self):
        """A line without its own date uses the order target date."""
        last_week = (timezone.now() - timedelta(days=7)).date()
        self._line(order_target=last_week)
        candidates, _ = evaluate(
            PoLateRule(), 'purchase_order_line', self.actor, self.scope
        )
        self.assertEqual(len(candidates), 1)

    def test_no_dates_or_received_is_silent(self):
        """Dateless and fully received lines are ineligible."""
        self._line()
        yesterday = (timezone.now() - timedelta(days=1)).date()
        self._line(line_target=yesterday, quantity=5, received=5)
        future = (timezone.now() + timedelta(days=3)).date()
        self._line(line_target=future)
        candidates, _ = evaluate(
            PoLateRule(), 'purchase_order_line', self.actor, self.scope
        )
        self.assertEqual(candidates, [])


class WoBlockedSafetyRuleTest(RuleTestBase):
    """WO_BLOCKED_SAFETY consumes the packet lifecycle-owner services."""

    def _packet(self, status):
        """Create a packet on the in-scope machine."""
        from repair.models import RepairPacket

        return RepairPacket.objects.create(
            fault_summary='f', machine=self.machine, status=status, criticality='high'
        )

    def test_unsatisfied_gate_blocks_advance(self):
        """A pending blocking gate yields an 'advance' candidate."""
        from repair.models import RepairPacketGate

        packet = self._packet('approved')
        RepairPacketGate.objects.create(
            packet=packet, name='LOTO', gate_type='loto', sequence=1, is_blocking=True
        )
        candidates, _ = evaluate(
            WoBlockedSafetyRule(), 'repair_packet', self.actor, self.client_scope
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].fingerprint_parts, (str(packet.pk), 'advance'))
        self.assertEqual(candidates[0].severity_factors['criticality'], 'high')

    def test_unrestored_loto_blocks_return_to_service(self):
        """Satisfied gates but active LOTO yields an 'rts' candidate."""
        from repair.models import GateStatus, LockoutPoint, RepairPacketGate

        packet = self._packet('executing')
        gate = RepairPacketGate.objects.create(
            packet=packet,
            name='LOTO',
            gate_type='loto',
            sequence=1,
            is_blocking=True,
            status=GateStatus.CONFIRMED,
        )
        # VERIFIED satisfies the gate (advance is legal) but the point is
        # not RESTORED, so return to service stays blocked — exactly the
        # divergence the live packet services encode.
        LockoutPoint.objects.create(
            gate=gate,
            energy_source='electrical',
            status=LockoutPoint.PointStatus.VERIFIED,
        )
        candidates, _ = evaluate(
            WoBlockedSafetyRule(), 'repair_packet', self.actor, self.client_scope
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].fingerprint_parts, (str(packet.pk), 'rts'))

    def test_clean_packet_is_silent(self):
        """A packet without safety blockers yields nothing."""
        self._packet('approved')
        candidates, _ = evaluate(
            WoBlockedSafetyRule(), 'repair_packet', self.actor, self.client_scope
        )
        self.assertEqual(candidates, [])


class ReadinessRulesTest(RuleTestBase):
    """Assignment/procedure rules consume only live emitted blockers."""

    def test_missing_assignee_and_asset(self):
        """READY work orders emit live ASSIGNEE/ASSET blockers."""
        with_machine = self.make_work_order(machine=self.machine)
        bare = self.make_work_order()
        candidates, _ = evaluate(
            WoBlockedAssignmentRule(), 'work_order', self.actor, self.scope
        )
        codes = {
            (candidate.source_id, candidate.fingerprint_parts[1])
            for candidate in candidates
        }
        self.assertIn((str(with_machine.pk), 'ASSIGNEE_REQUIRED'), codes)
        self.assertIn((str(bare.pk), 'ASSIGNEE_REQUIRED'), codes)
        self.assertIn((str(bare.pk), 'ASSET_REQUIRED'), codes)
        self.assertNotIn((str(with_machine.pk), 'ASSET_REQUIRED'), codes)

    def test_assigned_work_order_is_silent(self):
        """A staffed work order with an asset emits nothing."""
        self.make_work_order(machine=self.machine, assigned_to=self.actor)
        candidates, _ = evaluate(
            WoBlockedAssignmentRule(), 'work_order', self.actor, self.scope
        )
        self.assertEqual(candidates, [])

    def test_procedure_rule_only_consumes_step_codes(self):
        """The procedure rule stays silent without live step blockers."""
        self.make_work_order(lifecycle='in_progress', machine=self.machine)
        candidates, _ = evaluate(
            WoBlockedProcedureRule(), 'work_order', self.actor, self.scope
        )
        self.assertEqual(candidates, [])


class PacketStalledRuleTest(RuleTestBase):
    """PACKET_STALLED measures from entry into the current status."""

    def test_stalled_packet_matches(self):
        """A packet stuck past the threshold yields a candidate."""
        from repair.models import RepairPacket

        packet = RepairPacket.objects.create(fault_summary='f', machine=self.machine)
        RepairPacket.objects.filter(pk=packet.pk).update(
            created_at=timezone.now() - timedelta(hours=72)
        )
        candidates, _ = evaluate(
            PacketStalledRule(),
            'repair_packet',
            self.actor,
            self.client_scope,
            config={'stall_hours': 48},
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].fingerprint_parts, (str(packet.pk), 'draft'))

    def test_recent_transition_resets_clock(self):
        """A recent lifecycle event keeps the packet silent."""
        from repair.models import RepairPacket, RepairPacketEvent

        packet = RepairPacket.objects.create(
            fault_summary='f', machine=self.machine, status='diagnosed'
        )
        RepairPacket.objects.filter(pk=packet.pk).update(
            created_at=timezone.now() - timedelta(hours=100)
        )
        RepairPacketEvent.objects.create(
            packet=packet,
            event_type='advanced',
            from_status='draft',
            to_status='diagnosed',
        )
        candidates, _ = evaluate(
            PacketStalledRule(),
            'repair_packet',
            self.actor,
            self.client_scope,
            config={'stall_hours': 48},
        )
        self.assertEqual(candidates, [])


class CloseoutMissingRuleTest(RuleTestBase):
    """CLOSEOUT_MISSING covers verification stalls and anomalies."""

    def test_stuck_in_verifying(self):
        """A long-verifying work order yields a candidate."""
        from tasks.models import WorkOrderEvent

        wo = self.make_work_order(lifecycle='verifying', machine=self.machine)
        event = WorkOrderEvent.objects.create(
            work_order=wo,
            event_type='TRANSITION',
            from_status='in_progress',
            to_status='verifying',
            correlation_id=uuid.uuid4(),
        )
        WorkOrderEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - timedelta(hours=48)
        )
        candidates, _ = evaluate(
            CloseoutMissingRule(),
            'work_order',
            self.actor,
            self.scope,
            config={'verifying_hours': 24},
        )
        self.assertEqual(
            [candidate.fingerprint_parts for candidate in candidates],
            [(str(wo.pk), 'verifying')],
        )

    def test_completed_without_closeout(self):
        """A completed work order without a closeout is an anomaly."""
        wo = self.make_work_order(lifecycle='completed', machine=self.machine)
        candidates, _ = evaluate(
            CloseoutMissingRule(), 'work_order', self.actor, self.scope
        )
        self.assertEqual(
            [candidate.fingerprint_parts for candidate in candidates],
            [(str(wo.pk), 'missing_closeout')],
        )

    def test_completed_with_closeout_is_silent(self):
        """A proper closeout suppresses the anomaly."""
        from tasks.workorder_models import WorkOrderCloseout

        wo = self.make_work_order(lifecycle='completed', machine=self.machine)
        WorkOrderCloseout.objects.create(
            work_order=wo,
            action='fixed',
            result='works',
            verification_summary='verified',
            completed_by=self.actor,
            completed_at=timezone.now(),
        )
        candidates, _ = evaluate(
            CloseoutMissingRule(), 'work_order', self.actor, self.scope
        )
        self.assertEqual(candidates, [])


class StockBelowCriticalRuleTest(RuleTestBase):
    """STOCK_BELOW_CRITICAL reuses native minimum-stock semantics."""

    def _installed_part(self, *, minimum, stock):
        """Create a part installed on the in-scope machine with stock."""
        from assets.models import MachinePart
        from part.models import Part
        from stock.models import StockItem

        part = Part.objects.create(
            name=f'Part-{uuid.uuid4().hex[:8]}', description='d', minimum_stock=minimum
        )
        MachinePart.objects.create(machine=self.machine, part=part)
        if stock:
            StockItem.objects.create(part=part, quantity=stock)
        return part

    def test_below_minimum_matches(self):
        """Stock under the configured minimum yields a candidate."""
        part = self._installed_part(minimum=5, stock=2)
        candidates, _ = evaluate(
            StockBelowCriticalRule(), 'part_stock', self.actor, self.client_scope
        )
        self.assertEqual(
            [candidate.source_id for candidate in candidates], [str(part.pk)]
        )

    def test_healthy_or_unconfigured_is_silent(self):
        """Healthy stock and zero-minimum parts yield nothing."""
        self._installed_part(minimum=5, stock=10)
        self._installed_part(minimum=0, stock=0)
        candidates, _ = evaluate(
            StockBelowCriticalRule(), 'part_stock', self.actor, self.client_scope
        )
        self.assertEqual(candidates, [])

    def test_configured_part_list_restricts(self):
        """An explicit part list restricts the sweep."""
        low_listed = self._installed_part(minimum=5, stock=0)
        self._installed_part(minimum=5, stock=0)
        candidates, _ = evaluate(
            StockBelowCriticalRule(),
            'part_stock',
            self.actor,
            self.client_scope,
            config={'part_ids': [low_listed.pk]},
        )
        self.assertEqual(
            [candidate.source_id for candidate in candidates], [str(low_listed.pk)]
        )

    def test_query_count_does_not_scale_with_parts(self):
        """Stock totals are annotated in bulk instead of queried per part."""
        self._installed_part(minimum=5, stock=1)
        with CaptureQueriesContext(connection) as one_part:
            evaluate(
                StockBelowCriticalRule(), 'part_stock', self.actor, self.client_scope
            )

        for _ in range(5):
            self._installed_part(minimum=5, stock=1)
        with CaptureQueriesContext(connection) as many_parts:
            evaluate(
                StockBelowCriticalRule(), 'part_stock', self.actor, self.client_scope
            )

        self.assertLessEqual(
            len(many_parts.captured_queries), len(one_part.captured_queries) + 1
        )


class AssetRepeatMaintenanceRuleTest(RuleTestBase):
    """ASSET_REPEAT_MAINTENANCE is an activity signal only."""

    def _records(self, machine, count, *, days_ago=5):
        """Create maintenance records within the window."""
        from assets.models import AssetMaintenanceRecord

        for index in range(count):
            AssetMaintenanceRecord.objects.create(
                machine=machine,
                date=(timezone.now() - timedelta(days=days_ago + index)).date(),
                summary=f'Repair {index}',
            )

    def test_threshold_reached_matches(self):
        """N records within the window yield one machine candidate."""
        self._records(self.machine, 3)
        candidates, _ = evaluate(
            AssetRepeatMaintenanceRule(),
            'asset_machine',
            self.actor,
            self.client_scope,
            config={'window_days': 30, 'threshold': 3},
        )
        self.assertEqual(
            [candidate.source_id for candidate in candidates], [str(self.machine.pk)]
        )
        self.assertEqual(candidates[0].severity_factors['record_count'], 3)

    def test_below_threshold_or_outside_window_is_silent(self):
        """Old or sparse activity yields nothing."""
        self._records(self.machine, 2)
        self._records(self.machine, 2, days_ago=90)
        candidates, _ = evaluate(
            AssetRepeatMaintenanceRule(),
            'asset_machine',
            self.actor,
            self.client_scope,
            config={'window_days': 30, 'threshold': 3},
        )
        self.assertEqual(candidates, [])


class AnomalyUnaddressedTests(RuleTestBase):
    """E1: active warning/critical anomalies aging without a linked response."""

    def _anomaly(self, *, machine=None, severity='critical', age_minutes=90, **kw):
        from assets.health_models import MachineAnomaly

        now = timezone.now()
        observed = now - timedelta(minutes=age_minutes)
        defaults = {
            'machine': machine or self.machine,
            'fingerprint': uuid.uuid4().hex,
            'severity': severity,
            'status': 'open',
            'title': 'Bearing temperature high',
            'first_observed_at': observed,
            'last_observed_at': now,
        }
        defaults.update(kw)
        return MachineAnomaly.objects.create(**defaults)

    def test_aged_unlinked_anomaly_matches_with_criticality(self):
        """A critical anomaly past grace with no WO/packet is a candidate."""
        anomaly = self._anomaly(severity='critical', age_minutes=90)
        candidates, complete = evaluate(
            AnomalyUnaddressedRule(), 'machine_anomaly', self.actor, self.client_scope
        )
        self.assertTrue(complete)
        self.assertEqual([c.source_id for c in candidates], [str(anomaly.pk)])
        self.assertEqual(candidates[0].severity_factors['criticality'], 'critical')
        self.assertEqual(candidates[0].evidence['anomaly_id'], anomaly.pk)
        kinds = {link['target_kind'] for link in candidates[0].action_links}
        self.assertIn('machine_anomaly', kinds)

    def test_warning_anomaly_has_no_criticality_promotion(self):
        """Warning anomalies keep the high base without a criticality factor."""
        self._anomaly(severity='warning', age_minutes=90)
        candidates, _ = evaluate(
            AnomalyUnaddressedRule(), 'machine_anomaly', self.actor, self.client_scope
        )
        self.assertEqual(len(candidates), 1)
        self.assertNotIn('criticality', candidates[0].severity_factors)

    def test_linked_young_info_and_resolved_are_silent(self):
        """Linked, in-grace, info-severity and resolved anomalies never match."""
        wo = self.make_work_order(machine=self.machine)
        self._anomaly(age_minutes=90, work_order=wo)
        self._anomaly(age_minutes=5)
        self._anomaly(severity='info', age_minutes=90)
        self._anomaly(age_minutes=90, status='resolved')
        candidates, _ = evaluate(
            AnomalyUnaddressedRule(), 'machine_anomaly', self.actor, self.client_scope
        )
        self.assertEqual(candidates, [])

    def test_other_tenant_anomaly_is_out_of_scope(self):
        """The adapter proves scope by the machine's client identity."""
        self._anomaly(machine=self.other_machine, age_minutes=90)
        candidates, _ = evaluate(
            AnomalyUnaddressedRule(), 'machine_anomaly', self.actor, self.client_scope
        )
        self.assertEqual(candidates, [])


class ScheduleInfeasibleTests(RuleTestBase):
    """E3: pure planning what-if over the scope's open work orders."""

    def _wo(self, *, minutes=60, due=None, lifecycle='ready', title='WO'):
        wo = self.make_work_order(lifecycle=lifecycle, title=title)
        wo.estimated_minutes = minutes
        wo.due_date = due
        wo.save(update_fields=['estimated_minutes', 'due_date'])
        return wo

    def test_missing_estimate_and_past_due_are_reported(self):
        """No-estimate and planned-past-due conditions become candidates."""
        no_estimate = self._wo(minutes=None, title='No estimate')
        late = self._wo(
            minutes=240, due=(timezone.now() - timedelta(days=2)).date(), title='Late'
        )
        candidates, complete = evaluate(
            ScheduleInfeasibleRule(), 'work_order', self.actor, self.scope
        )
        self.assertTrue(complete)
        reasons = {c.evidence.get('reason') for c in candidates}
        self.assertEqual(reasons, {'no_estimate', 'past_due'})
        by_reason = {c.evidence['reason']: c for c in candidates}
        self.assertEqual(by_reason['no_estimate'].source_id, str(no_estimate.pk))
        self.assertEqual(by_reason['past_due'].source_id, str(late.pk))
        self.assertGreaterEqual(by_reason['past_due'].severity_factors['days_late'], 1)

    def test_dependency_cycle_is_reported_once(self):
        """A dependency loop yields one stable candidate naming all members."""
        from tasks.models import WorkOrderDependency

        first = self._wo(title='First')
        second = self._wo(title='Second')
        WorkOrderDependency.objects.create(predecessor=first, successor=second)
        WorkOrderDependency.objects.create(predecessor=second, successor=first)
        candidates, _ = evaluate(
            ScheduleInfeasibleRule(), 'work_order', self.actor, self.scope
        )
        cycles = [
            c for c in candidates if c.evidence.get('reason') == 'dependency_cycle'
        ]
        self.assertEqual(len(cycles), 1)
        self.assertEqual(
            set(cycles[0].evidence['work_order_ids']), {first.pk, second.pk}
        )

    def test_terminal_and_feasible_orders_are_silent(self):
        """Completed/canceled and comfortably feasible orders never match."""
        self._wo(minutes=None, lifecycle='completed', title='Done')
        self._wo(
            minutes=60, due=(timezone.now() + timedelta(days=30)).date(), title='Fine'
        )
        candidates, _ = evaluate(
            ScheduleInfeasibleRule(), 'work_order', self.actor, self.scope
        )
        self.assertEqual(candidates, [])


class ShiftBriefingTests(RuleTestBase):
    """E2: one per-scope digest that supersedes daily by fingerprint date."""

    def test_digest_counts_active_findings_and_excludes_itself(self):
        """The digest counts other rules' findings, never its own."""
        self.make_finding(discriminator='a1', severity='high')
        self.make_finding(discriminator='a2', severity='medium')
        self.make_finding(discriminator='old', state=RiskFindingState.RESOLVED)
        self.make_finding(code='SHIFT_BRIEFING', discriminator='self')
        candidates, complete = evaluate(
            ShiftBriefingRule(), 'risk_finding', self.actor, self.scope
        )
        self.assertTrue(complete)
        self.assertEqual(len(candidates), 1)
        digest = candidates[0]
        self.assertEqual(digest.evidence['active_count'], 2)
        self.assertEqual(digest.evidence['by_severity'], {'high': 1, 'medium': 1})
        self.assertEqual(digest.evidence['resolved_last_window'], 1)
        self.assertEqual(digest.fingerprint_parts, (digest.evidence['briefing_date'],))
        self.assertEqual(digest.source_id, digest.evidence['briefing_date'])

    def test_empty_scope_still_emits_one_digest(self):
        """A quiet scope gets an explicit zero-finding briefing."""
        candidates, _ = evaluate(
            ShiftBriefingRule(), 'risk_finding', self.actor, self.scope
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].evidence['active_count'], 0)
