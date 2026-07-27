"""The repair work-package approval executor and its single-authority bridge.

Two properties matter here. First, approving a repair from the queue must run the
same audited command a planner runs by hand - the queue may not become a second,
weaker write path. Second, when a proposal is bridged to the queue, exactly one
rail executes it: chat holds the preview, the approval holds the authority.
"""

from __future__ import annotations

import unittest
import uuid

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from tasks.models import KanbanCard
from tasks.scope import MaintenanceScope

from aichat.models import ChatActionProposal
from aichat.services import proposals as chat_svc
from approvals.executors import (
    EXECUTOR_REQUIRED_ACTIONS,
    RepairWorkPackageExecutor,
    is_executor_required,
    registry,
)
from approvals.models import ActionType
from assets.health_models import AnomalyStatus, SourceType
from assets.models import AssetMachine
from company.models import Company
from repair.models import RepairPacket


def _payload_of(result) -> dict:
    """Return an EffectResult's payload, failing loudly when it has none.

    ``result_payload`` is optional on the interface, so unwrapping it here keeps
    a missing payload an explicit assertion rather than an AttributeError three
    lines later.
    """
    assert result.result_payload is not None, 'executor returned no result payload'
    return result.result_payload


class RepairWorkPackageExecutorTest(TestCase):
    """The executor delegates to the canonical command and detects drift."""

    def setUp(self):
        """Create a scoped actor and a machine."""
        suffix = uuid.uuid4().hex[:8]
        self.customer = Company.objects.create(name=f'Exec {suffix}', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username=f'exec-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.machine = AssetMachine.objects.create(
            name=f'Clarifier {suffix}', customer=self.customer
        )
        self.executor = RepairWorkPackageExecutor()

    def _payload(self, **overrides):
        payload = {
            'machine_id': self.machine.pk,
            'title': 'Scraper drive overload',
            'origin': 'chat',
            'actor_id': self.actor.pk,
            'fault': {'summary': 'Torque alarm at 82%', 'criticality': 'high'},
        }
        payload.update(overrides)
        return payload

    def test_executor_is_required_so_a_missing_one_fails_closed(self):
        """Approving a repair must never silently succeed with no effect."""
        self.assertIn(ActionType.REPAIR_WORK_PACKAGE, EXECUTOR_REQUIRED_ACTIONS)
        self.assertTrue(is_executor_required(ActionType.REPAIR_WORK_PACKAGE))
        self.assertTrue(registry.has(ActionType.REPAIR_WORK_PACKAGE))

    def test_validate_rejects_a_malformed_draft(self):
        """Validation reuses the canonical schema rather than a second one."""
        self.assertEqual(self.executor.validate(self._payload()), [])
        self.assertTrue(self.executor.validate(self._payload(title='  ')))

    def test_execute_creates_one_linked_aggregate(self):
        """The effect is the same command the manual path runs."""
        result = self.executor.execute(self._payload(), uuid.uuid4().hex)

        self.assertTrue(result.success)
        payload = _payload_of(result)
        work_order = KanbanCard.objects.get(pk=payload['work_order_id'])
        packet = RepairPacket.objects.get(pk=payload['repair_packet_id'])
        self.assertEqual(work_order.machine_id, self.machine.pk)
        self.assertEqual(packet.work_order_id, work_order.pk)

    def test_execute_is_idempotent_for_one_key(self):
        """Replaying an effect key returns the same repair, not a second one."""
        key = uuid.uuid4().hex
        first = self.executor.execute(self._payload(), key)
        second = self.executor.execute(self._payload(), key)

        self.assertEqual(
            _payload_of(first)['work_order_id'], _payload_of(second)['work_order_id']
        )
        self.assertEqual(KanbanCard.objects.filter(machine=self.machine).count(), 1)

    def test_duplicate_open_repair_fails_the_effect_with_its_links(self):
        """The queue cannot bypass duplicate control either."""
        self.executor.execute(self._payload(), uuid.uuid4().hex)

        blocked = self.executor.execute(
            self._payload(title='Second repair'), uuid.uuid4().hex
        )

        self.assertFalse(blocked.success)
        self.assertTrue(_payload_of(blocked)['duplicates'])

    def test_risk_tier_rises_with_criticality(self):
        """A critical fault warrants the higher review tier."""
        self.assertEqual(self.executor.compute_risk_tier(self._payload()), 2)
        self.assertEqual(
            self.executor.compute_risk_tier(
                self._payload(fault={'criticality': 'critical'})
            ),
            3,
        )

    def test_missing_machine_is_drift_not_a_crash(self):
        """A machine deleted while the request waited fails the precondition."""
        payload = self._payload(machine_id=987654)
        report = self.executor.check_preconditions(payload, {})

        self.assertTrue(report.has_drift)
        self.assertEqual(report.failed[0]['check'], 'machine_exists')

    def test_a_new_repair_since_the_request_is_warned_about(self):
        """A second repair opened meanwhile is surfaced, not silently ignored."""
        payload = self._payload()
        baseline = self.executor.compute_baseline(payload)

        self.executor.execute(self._payload(), uuid.uuid4().hex)

        report = self.executor.check_preconditions(payload, baseline)
        self.assertFalse(report.has_drift)
        self.assertTrue(report.warnings)


class RepairAnomalyDriftTest(TestCase):
    """Drift on the anomaly the repair answers."""

    def setUp(self):
        """Create a machine with one open anomaly and a matching payload."""
        from assets.health_models import HealthSource
        from machine_health.services.anomalies import fingerprint_for, record_anomaly

        suffix = uuid.uuid4().hex[:8]
        self.customer = Company.objects.create(name=f'Drift {suffix}', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username=f'drift-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.machine = AssetMachine.objects.create(
            name=f'UV Channel {suffix}', customer=self.customer
        )
        HealthSource.objects.create(
            name=f'SCADA {suffix}', source_type=SourceType.SCADA
        )
        self.anomaly, _ = record_anomaly(
            machine=self.machine,
            fingerprint=fingerprint_for('drift', suffix),
            title='Lamp bank output low',
            severity='critical',
        )
        self.executor = RepairWorkPackageExecutor()
        self.payload = {
            'machine_id': self.machine.pk,
            'title': 'Replace lamp bank',
            'origin': 'chat',
            'actor_id': self.actor.pk,
            'source': {'anomaly_id': self.anomaly.pk},
        }
        self.baseline = self.executor.compute_baseline(self.payload)

    def test_baseline_captures_the_anomaly_condition(self):
        """The approver's decision is anchored to what was true when asked."""
        self.assertEqual(self.baseline['anomaly']['id'], self.anomaly.pk)
        self.assertEqual(self.baseline['anomaly']['status'], AnomalyStatus.OPEN)

    def test_resolved_anomaly_blocks_execution_as_drift(self):
        """A fault that resolved while waiting must not raise a repair."""
        self.anomaly.status = AnomalyStatus.RESOLVED
        self.anomaly.save(update_fields=['status'])

        report = self.executor.check_preconditions(self.payload, self.baseline)

        self.assertTrue(report.has_drift)
        self.assertEqual(report.failed[0]['check'], 'anomaly_active')

    def test_anomaly_claimed_by_another_repair_blocks_execution(self):
        """Somebody else already raised the work; approving would duplicate it."""
        other = KanbanCard.objects.create(
            title='Already raised',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_HIGH,
            machine=self.machine,
        )
        self.anomaly.work_order = other
        self.anomaly.save(update_fields=['work_order'])

        report = self.executor.check_preconditions(self.payload, self.baseline)

        self.assertTrue(report.has_drift)
        self.assertEqual(report.failed[0]['check'], 'anomaly_unclaimed')

    def test_unchanged_anomaly_passes(self):
        """No drift when nothing moved."""
        report = self.executor.check_preconditions(self.payload, self.baseline)

        self.assertFalse(report.has_drift)
        self.assertIn({'check': 'anomaly_active'}, report.passed)


class ApprovalBridgeTest(TestCase):
    """Exactly one execution authority per proposal."""

    def setUp(self):
        """Create a scoped actor and machine for chat proposals."""
        suffix = uuid.uuid4().hex[:8]
        self.customer = Company.objects.create(
            name=f'Bridge {suffix}', is_customer=True
        )
        self.actor = get_user_model().objects.create_superuser(
            username=f'bridge-{suffix}', email=f'{suffix}@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.machine = AssetMachine.objects.create(
            name=f'Skid {suffix}', customer=self.customer
        )

    def _propose(self):
        return chat_svc.create_proposal(
            owner=self.actor,
            scope_key=f'customer:{self.customer.pk}',
            scope_hash='c' * 64,
            action_type='repair_work_package.create',
            work_order_id=None,
            reason='Raised from chat',
            idempotency_key=uuid.uuid4().hex,
            policy_version='test-v1',
            intent={
                'machine_id': self.machine.pk,
                'title': 'Membrane integrity check',
                'origin': 'chat',
            },
            thread_id='thread-9',
        )

    def test_bridge_is_off_by_default(self):
        """Deployments keep the chat rail until they staff an approval inbox."""
        proposal = self._propose()

        self.assertIsNone(proposal.approval_id)

        confirmed = chat_svc.confirm_proposal(
            owner=self.actor, scope_hash='c' * 64, proposal_id=proposal.id
        )
        self.assertEqual(confirmed.receipt['command'], 'create_repair_work_package')

    @override_settings(AIMMS_APPROVAL_QUEUE_OWNS_REPAIRS=True)
    def test_bridged_proposal_links_one_approval(self):
        """The chat row becomes a preview pointing at the approval."""
        proposal = self._propose()

        self.assertIsNotNone(proposal.approval_id)
        approval = proposal.approval
        self.assertEqual(approval.action_type, ActionType.REPAIR_WORK_PACKAGE)
        self.assertIn(self.machine.name, approval.summary)
        self.assertEqual(approval.payload['actor_id'], self.actor.pk)
        self.assertTrue(approval.baseline_context['machine_exists'])

    @override_settings(AIMMS_APPROVAL_QUEUE_OWNS_REPAIRS=True)
    def test_bridged_proposal_refuses_to_dispatch(self):
        """Chat cannot execute what the approval queue owns - no dual effect."""
        proposal = self._propose()

        with self.assertRaises(chat_svc.ApprovalOwnsExecution):
            chat_svc.confirm_proposal(
                owner=self.actor, scope_hash='c' * 64, proposal_id=proposal.id
            )

        self.assertFalse(KanbanCard.objects.filter(machine=self.machine).exists())
        self.assertFalse(RepairPacket.objects.filter(machine=self.machine).exists())
        proposal.refresh_from_db()
        self.assertIsNone(proposal.receipt)

    @override_settings(AIMMS_APPROVAL_QUEUE_OWNS_REPAIRS=True)
    def test_bridging_creates_no_repair_by_itself(self):
        """Raising an approval is a request, not an effect."""
        self._propose()

        self.assertFalse(KanbanCard.objects.filter(machine=self.machine).exists())
        self.assertEqual(ChatActionProposal.objects.count(), 1)
