"""P23: retain native failed outcomes without inventing authority or values."""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from assets.models import AssetMachine, get_default_client
from repair.models import LockoutPoint, RepairPacket, RepairPacketGate
from tasks.models import (
    Procedure,
    ProcedureRevision,
    WorkOrder,
    WorkOrderEvent,
    WorkOrderProcedureApplication,
    WorkOrderStepExecution,
)
from tasks.scope import MaintenanceScope
from tasks.services.procedure_execution import complete_step, mark_step_not_applicable
from tasks.work_execution.verification import project


@override_settings(AIMMS_PROCEDURES_ENABLED=True)
class VerificationProjectionTest(TestCase):
    """Read saved test state, outcome events, and independent lockout records."""

    def setUp(self):
        """Create a current result and an older failed-outcome event."""
        self.actor = get_user_model().objects.create_user(username='verification-tech')
        self.work = WorkOrder.objects.create(title='Verification fixture')
        procedure = Procedure.objects.create(
            code='VERIFY', name='Verify', created_by=self.actor
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure,
            revision=1,
            work_order_type='corrective',
            created_by=self.actor,
        )
        self.application = WorkOrderProcedureApplication.objects.create(
            work_order=self.work,
            revision=revision,
            snapshot={'revision': {'revision': 1}},
            snapshot_hash='b' * 64,
            applied_by=self.actor,
            idempotency_key='verification-fixture',
        )
        self.execution = WorkOrderStepExecution.objects.create(
            application=self.application,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={
                'step_type': 'verification',
                'min_value': '0',
                'max_value': '4',
                'unit': 'mm/s',
            },
            status='completed',
            value={'number': '0'},
            passed=True,
            version=4,
            completed_by=self.actor,
            completed_at=timezone.now(),
        )
        WorkOrderEvent.objects.create(
            work_order=self.work,
            event_type='STEP_FAILED',
            actor=self.actor,
            correlation_id=uuid.uuid4(),
            metadata={
                'step_execution_id': self.execution.pk,
                'version': 2,
                'evidence_ids': [91],
            },
        )

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

    def test_events_are_isolated_between_steps_of_the_same_work(self):
        """Each execution retains only its own events within one parent Work."""
        other = WorkOrderStepExecution.objects.create(
            application=self.application,
            step_key=uuid.uuid4(),
            sequence=2,
            step_snapshot={'step_type': 'inspection'},
        )
        event = WorkOrderEvent.objects.create(
            work_order=self.work,
            event_type='STEP_COMPLETED',
            actor=self.actor,
            correlation_id=uuid.uuid4(),
            metadata={
                'step_execution_id': other.pk,
                'version': 1,
                'note': 'Other step note',
            },
        )
        rows = {row['id']: row for row in project(self.work)['tests']}
        self.assertEqual(len(rows[self.execution.pk]['events']), 1)
        self.assertNotEqual(rows[self.execution.pk]['events'][0]['id'], event.pk)
        self.assertEqual([row['id'] for row in rows[other.pk]['events']], [event.pk])

    def _complete_then_dispose(self, reading):
        """Exercise the supported writers with two distinct recorded actors."""
        machine = AssetMachine.objects.create(
            name='Disposition fixture', client=get_default_client()
        )
        self.work.machine = machine
        self.work.save(update_fields=['machine'])
        self.actor.is_superuser = True
        self.actor.save(update_fields=['is_superuser'])
        supervisor = get_user_model().objects.create_user(
            username='disposition-supervisor', is_superuser=True
        )
        for actor in (self.actor, supervisor):
            actor.maintenance_scopes = {
                MaintenanceScope(
                    customer_id=None, site_key=None, client_id=machine.client_id
                )
            }
        self.execution.step_snapshot.update(value_type='number', required=True)
        self.execution.save(update_fields=['step_snapshot'])
        self.work.events.all().delete()
        completed_at = timezone.now()
        with patch(
            'tasks.services.procedure_execution.timezone.now', return_value=completed_at
        ):
            completed = complete_step(
                work_order_id=self.work.pk,
                application_id=self.application.pk,
                step_key=self.execution.step_key,
                actor=self.actor,
                expected_version=self.execution.version,
                idempotency_key='record-measurement',
                value={'number': reading},
                note='Original measurement observation',
            )
        disposed_at = completed_at + timezone.timedelta(hours=1)
        with patch(
            'tasks.services.procedure_execution.timezone.now', return_value=disposed_at
        ):
            mark_step_not_applicable(
                work_order_id=self.work.pk,
                application_id=self.application.pk,
                step_key=self.execution.step_key,
                actor=supervisor,
                expected_version=completed.version,
                idempotency_key='record-disposition',
                reason='Recorded applicability decision',
            )
        row = project(self.work)['tests'][0]
        self.assertEqual(row['status'], 'not_applicable')
        self.assertIsNone(row['value'])
        self.assertIsNone(row['passed'])
        self.assertEqual(row['note'], '')
        self.assertEqual(row['recorded_by'], supervisor.username)
        self.assertEqual(row['recorded_at'], disposed_at.isoformat())
        self.assertEqual(row['events'][0]['recorded_by'], self.actor.username)
        self.assertEqual(row['events'][0]['recorded_at'], completed_at.isoformat())
        self.assertEqual(
            row['events'][0]['recorded_note'], 'Original measurement observation'
        )
        self.execution.refresh_from_db()
        # The read suppresses stale current-result facts; it does not edit the writer's record.
        self.assertEqual(self.execution.value, {'number': str(reading)})
        return row

    def test_failed_then_not_applicable_does_not_reattribute_the_reading(self):
        """A failed reading stays an earlier event, not the supervisor's reading."""
        row = self._complete_then_dispose('9.2')
        self.assertEqual(row['events'][0]['event_type'], 'STEP_FAILED')
        self.assertEqual(row['events'][1]['event_type'], 'STEP_NOT_APPLICABLE')

    def test_completed_then_not_applicable_does_not_reattribute_the_reading(self):
        """A prior pass is not a current result of the later disposition."""
        row = self._complete_then_dispose('0.8')
        self.assertEqual(row['events'][0]['event_type'], 'STEP_COMPLETED')

    def test_attempt_notes_keep_their_own_event_and_recorder(self):
        """A retest's current note cannot replace the earlier failure's note."""
        event = self.work.events.get()
        event.reason = ''
        event.metadata['note'] = 'Original failed-attempt observation'
        event.save(update_fields=['reason', 'metadata'])
        self.execution.note = 'Later passing-attempt observation'
        self.execution.save(update_fields=['note'])
        row = project(self.work)['tests'][0]
        self.assertEqual(row['note'], 'Later passing-attempt observation')
        self.assertEqual(row['events'][0]['reason'], '')
        self.assertEqual(
            row['events'][0]['recorded_note'], 'Original failed-attempt observation'
        )

    def test_attempt_notes_are_bounded_and_malformed_notes_are_unknown(self):
        """Long notes disclose truncation; non-text metadata is never rendered as a note."""
        event = self.work.events.get()
        event.metadata['note'] = 'x' * 4001
        event.save(update_fields=['metadata'])
        row = project(self.work)['tests'][0]['events'][0]
        self.assertEqual(row['recorded_note'], 'x' * 4000)
        self.assertTrue(row['recorded_note_truncated'])
        event.metadata['note'] = {'unexpected': 'object'}
        event.save(update_fields=['metadata'])
        self.assertIsNone(project(self.work)['tests'][0]['events'][0]['recorded_note'])

    def test_result_never_supplies_authority(self):
        """A native pass or completed lifecycle cannot authorize operation."""
        self.work.lifecycle_status = 'completed'
        self.work.save(update_fields=['lifecycle_status'])
        result = project(self.work)
        for key in (
            'technical_acceptance',
            'controlled_test_authority',
            'operating_permission',
            'observed_return',
            'service_state',
        ):
            self.assertFalse(result[key]['available'])

    @override_settings(AIMMS_PROCEDURES_ENABLED=False)
    def test_disabled_procedures_withhold_tests(self):
        """A disabled source remains unavailable, not an evaluated empty set."""
        result = project(self.work)
        self.assertEqual(result['procedures_state'], 'not_enabled')
        self.assertEqual(result['tests'], [])

    def test_restored_lockout_is_a_separate_source_record(self):
        """A restored point does not establish operating or service return."""
        machine = AssetMachine.objects.create(
            name='Verification machine', client=get_default_client()
        )
        packet = RepairPacket.objects.create(
            machine=machine, work_order=self.work, fault_summary='Fixture'
        )
        gate = RepairPacketGate.objects.create(
            packet=packet, name='LOTO', gate_type='loto'
        )
        LockoutPoint.objects.create(
            gate=gate,
            isolation_device='Synthetic feeder',
            status='restored',
            restored_at=timezone.now(),
            applied_by=self.actor,
        )
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
