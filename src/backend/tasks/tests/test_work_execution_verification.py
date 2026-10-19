"""P23: retain native failed outcomes without inventing authority or values."""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from assets.models import AssetMachine, get_default_client
from repair.models import LockoutPoint, RepairPacket, RepairPacketGate
from tasks.models import (
    Procedure, ProcedureRevision, WorkOrder, WorkOrderEvent,
    WorkOrderProcedureApplication, WorkOrderStepExecution,
)
from tasks.work_execution.verification import project


@override_settings(AIMMS_PROCEDURES_ENABLED=True)
class VerificationProjectionTest(TestCase):
    """Read saved test state, outcome events, and independent lockout records."""

    def setUp(self):
        """Create a current result and an older failed-outcome event."""
        self.actor = get_user_model().objects.create_user(username='verification-tech')
        self.work = WorkOrder.objects.create(title='Verification fixture')
        procedure = Procedure.objects.create(code='VERIFY', name='Verify', created_by=self.actor)
        revision = ProcedureRevision.objects.create(procedure=procedure, revision=1, work_order_type='corrective', created_by=self.actor)
        self.application = WorkOrderProcedureApplication.objects.create(work_order=self.work, revision=revision, snapshot={'revision': {'revision': 1}}, snapshot_hash='b' * 64, applied_by=self.actor, idempotency_key='verification-fixture')
        self.execution = WorkOrderStepExecution.objects.create(application=self.application, step_key=uuid.uuid4(), sequence=1, step_snapshot={'step_type': 'verification', 'min_value': '0', 'max_value': '4', 'unit': 'mm/s'}, status='completed', value={'number': '0'}, passed=True, version=4, completed_by=self.actor, completed_at=timezone.now())
        WorkOrderEvent.objects.create(work_order=self.work, event_type='STEP_FAILED', actor=self.actor, correlation_id=uuid.uuid4(), metadata={'step_execution_id': self.execution.pk, 'version': 2, 'evidence_ids': [91]})

    def test_previous_failure_is_not_filled_with_current_value(self):
        """Outcome-only history stays explicit after a later pass."""
        result = project(self.work)
        test = result['tests'][0]
        self.assertEqual(test['value'], {'number': '0'})
        self.assertTrue(test['passed'])
        self.assertEqual(test['events'][0]['event_type'], 'STEP_FAILED')
        self.assertFalse(test['events'][0]['historical_value_available'])
        self.assertNotIn('value', test['events'][0])
        self.assertEqual(test['events'][0]['evidence_ids'], [91])
        self.assertEqual(test['events'][0]['recorded_by'], 'verification-tech')

    def test_scope_does_not_merge_sibling_test_results(self):
        """A result belongs only to its owning Work and application."""
        sibling = WorkOrder.objects.create(title='Independent work')
        self.assertEqual(project(sibling)['tests'], [])

    def test_result_never_supplies_authority(self):
        """A native pass or completed lifecycle cannot authorize operation."""
        self.work.lifecycle_status = 'completed'
        self.work.save(update_fields=['lifecycle_status'])
        result = project(self.work)
        for key in ('technical_acceptance', 'controlled_test_authority', 'operating_permission', 'observed_return', 'service_state'):
            self.assertFalse(result[key]['available'])

    @override_settings(AIMMS_PROCEDURES_ENABLED=False)
    def test_disabled_procedures_withhold_tests(self):
        """A disabled source remains unavailable, not an evaluated empty set."""
        result = project(self.work)
        self.assertEqual(result['procedures_state'], 'not_enabled')
        self.assertEqual(result['tests'], [])

    def test_restored_lockout_is_a_separate_source_record(self):
        """A restored point does not establish operating or service return."""
        machine = AssetMachine.objects.create(name='Verification machine', client=get_default_client())
        packet = RepairPacket.objects.create(machine=machine, work_order=self.work, fault_summary='Fixture')
        gate = RepairPacketGate.objects.create(packet=packet, name='LOTO', gate_type='loto')
        LockoutPoint.objects.create(gate=gate, isolation_device='Synthetic feeder', status='restored', restored_at=timezone.now(), applied_by=self.actor)
        result = project(self.work)
        self.assertEqual(result['lockouts'][0]['status'], 'restored')
        self.assertFalse(result['observed_return']['available'])

    def test_overview_projection_does_not_change_test_or_event(self):
        """Reading twice records neither a test nor an acceptance."""
        before = self.work.events.count()
        project(self.work)
        project(self.work)
        self.execution.refresh_from_db()
        self.assertEqual(self.execution.version, 4)
        self.assertEqual(self.work.events.count(), before)
