"""Retained report approval, native completion and test-only recovery boundaries."""

import uuid
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from assets.health_models import AnomalySeverity, AnomalyStatus, MachineAnomaly
from machine_health.services.test_alarms import DETECTOR
from tasks.closeout_models import CloseoutEffect
from tasks.services.closeout import CloseoutError, complete_work_order
from tasks.services.closeout_effects import EffectOutcomeUnknown
from tasks.services.closeout_reconcile import record_reading
from tasks.services.work_orders import WorkOrderScopeError
from tasks.services.service_reports import (
    equipment_return_url,
    execute_sap_writeback,
    latest_report,
    report_state,
    require_approved_report,
    service_report_command,
)
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, VALID_CLOSEOUT, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS, AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor')
class ServiceReportTest(CloseoutEnvMixin, TestCase):
    """Review is separate from technician submission and native service authority."""

    def setUp(self):
        self.build_env(username='service-report-tech')
        self.reviewer = self.make_scoped_user(
            'service-report-reviewer', permissions=['review_closeout', 'verify_closeout']
        )

    def command(self, action, actor=None, **kwargs):
        """Drive the real command service at the current persisted version."""
        self.work_order.refresh_from_db()
        return service_report_command(
            work_order_id=self.work_order.pk, actor=actor or self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=str(uuid.uuid4()), action=action, **kwargs,
        )

    def submit_and_approve(self):
        self.command('submit', account=VALID_CLOSEOUT)
        report_id = latest_report(self.work_order).pk
        self.command('approve', self.reviewer, report_id=report_id)
        self.work_order.refresh_from_db()
        return report_id

    def complete(self):
        self.work_order.refresh_from_db()
        return complete_work_order(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=str(uuid.uuid4()), closeout=VALID_CLOSEOUT,
        )

    def test_submit_preview_approve_complete(self):
        report_id = self.submit_and_approve()
        state = report_state(self.work_order)
        self.assertEqual(state['report_id'], report_id)
        self.assertEqual(state['status'], 'approved')
        self.assertEqual(state['report']['account']['action'], VALID_CLOSEOUT['action'])
        self.assertEqual(self.complete().lifecycle_status, 'completed')
        self.assertTrue(self.work_order.structured_closeout.reports.exists())

    def test_scoped_api_submits_and_reads_retained_report(self):
        api = APIClient()
        api.force_authenticate(self.actor)
        url = f'/api/tasks/work-orders/{self.work_order.pk}/closeout/service-report/'
        response = api.post(url, {
            'action': 'submit', 'account': VALID_CLOSEOUT,
            'expected_version': self.work_order.lifecycle_version,
            'idempotency_key': str(uuid.uuid4()),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        read = api.get(url)
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read['Cache-Control'], 'private, no-store')
        self.assertEqual(read.data['status'], 'submitted')
        self.assertIn('evidence', read.data['report'])

    def test_self_approval_refused(self):
        self.command('submit', account=VALID_CLOSEOUT)
        with self.assertRaisesMessage(CloseoutError, 'different reviewer'):
            self.command('approve', report_id=latest_report(self.work_order).pk)

    def test_unapproved_report_blocks_completion(self):
        self.command('submit', account=VALID_CLOSEOUT)
        with self.assertRaisesMessage(CloseoutError, 'independently approve'):
            self.complete()

    def test_changed_account_cannot_reuse_approval(self):
        self.submit_and_approve()
        with self.assertRaises(CloseoutError):
            require_approved_report(self.work_order, {**VALID_CLOSEOUT, 'result': 'Changed'}, None)

    def test_unchanged_approved_report_cannot_be_resubmitted(self):
        report_id = self.submit_and_approve()
        with self.assertRaisesMessage(CloseoutError, 'already approved'):
            self.command('submit', account=VALID_CLOSEOUT)
        self.work_order.refresh_from_db()
        self.assertEqual(report_state(self.work_order)['status'], 'approved')
        self.assertEqual(latest_report(self.work_order).pk, report_id)

    def test_changed_approved_report_requires_new_independent_approval(self):
        report_id = self.submit_and_approve()
        self.command('submit', account={**VALID_CLOSEOUT, 'result': 'Rechecked result'})
        self.work_order.refresh_from_db()
        self.assertEqual(report_state(self.work_order)['status'], 'submitted')
        self.assertNotEqual(latest_report(self.work_order).pk, report_id)
        with self.assertRaises(CloseoutError):
            self.complete()

    def test_changed_version_invalidates_approval(self):
        self.submit_and_approve()
        self.work_order.lifecycle_version += 1
        self.work_order.save(update_fields=['lifecycle_version'])
        self.assertEqual(report_state(self.work_order)['status'], 'stale')
        with self.assertRaises(CloseoutError):
            self.complete()

    def test_changed_evidence_invalidates_approval(self):
        self.submit_and_approve()
        record_reading(work_order_id=self.work_order.pk, actor=self.actor,
                       label='Changed post-test reading', raw_text='2.5')
        self.assertEqual(report_state(self.work_order)['status'], 'stale')

    def test_return_requires_native_verification_policy(self):
        report_id = self.submit_and_approve()
        self.complete()
        with override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='off'):
            with self.assertRaisesMessage(Exception, 'disabled by policy'):
                self.command('return', self.reviewer, report_id=report_id)

    def test_technician_cannot_verify_their_own_completion(self):
        report_id = self.submit_and_approve()
        self.complete()
        with self.assertRaisesMessage(Exception, 'cannot verify their own'):
            self.command('return', self.actor, report_id=report_id)

    def test_replay_and_revoked_permission(self):
        arguments = dict(
            work_order_id=self.work_order.pk, actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=str(uuid.uuid4()), action='submit', account=VALID_CLOSEOUT,
        )
        first = service_report_command(**arguments)
        self.assertEqual(service_report_command(**arguments).event_id, first.event_id)
        self.actor.is_superuser = False
        with self.assertRaises((PermissionDenied, WorkOrderScopeError)):
            service_report_command(**arguments)

    def test_test_recovery_does_not_clear_real_alarms(self):
        test = MachineAnomaly.objects.create(
            machine=self.machine, work_order=self.work_order, detector=DETECTOR,
            fingerprint='synthetic-report-test', title='TEST', severity=AnomalySeverity.CRITICAL,
            status=AnomalyStatus.OPEN, metrics={'synthetic': True},
            first_observed_at=timezone.now(), last_observed_at=timezone.now(),
        )
        real = MachineAnomaly.objects.create(
            machine=self.machine, work_order=self.work_order, detector='threshold',
            fingerprint='real-report-test', title='Actual alarm', severity=AnomalySeverity.CRITICAL,
            status=AnomalyStatus.OPEN,
            first_observed_at=timezone.now(), last_observed_at=timezone.now(),
        )
        report_id = self.submit_and_approve()
        self.complete()
        result = self.command('return', self.reviewer, report_id=report_id)
        test.refresh_from_db()
        real.refresh_from_db()
        self.assertEqual(test.status, AnomalyStatus.RESOLVED)
        self.assertEqual(real.status, AnomalyStatus.OPEN)
        self.assertEqual(result.metadata['resolved_test_alarms'], [test.pk])
        self.assertEqual(result.metadata['equipment_url'], f'/machines/machine/{self.machine.pk}/equipment/')
        self.assertTrue(CloseoutEffect.objects.filter(effect_type='test_recovery').exists())

    def test_test_work_requires_report_even_without_global_policy(self):
        MachineAnomaly.objects.create(
            machine=self.machine, work_order=self.work_order, detector=DETECTOR,
            fingerprint='report-required', title='TEST', severity=AnomalySeverity.CRITICAL,
            first_observed_at=timezone.now(), last_observed_at=timezone.now(),
        )
        with self.assertRaises(CloseoutError):
            self.complete()

    def test_sap_not_configured_is_not_success(self):
        report_id = self.submit_and_approve()
        self.complete()
        with self.assertRaisesMessage(CloseoutError, 'not configured'):
            self.command('sap', self.reviewer, report_id=report_id)
        self.assertFalse(CloseoutEffect.objects.filter(effect_type='sap_writeback').exists())

    def test_motor_return_uses_registered_component_family(self):
        from assets.models import AssetComponent, AssetMachine
        from assets.registry import ensure_pump, register_station
        from part.models import Part

        station = register_station(client=self.tenant, name='Service station',
                                   source_namespace='test-service', source_entity_uuid=uuid.uuid4(),
                                   source_key='SERVICE_STATION')
        self.machine = ensure_pump(station, 'P8')
        self.work_order.machine = self.machine
        self.work_order.save(update_fields=['machine'])
        component = AssetComponent.objects.create(machine=self.machine, name='Motor', code='MOTOR:1',
                                                  part=Part.objects.create(name='Motor', IPN='PS-MOTOR'))
        MachineAnomaly.objects.create(
            machine=self.machine, work_order=self.work_order, detector=DETECTOR,
            fingerprint='test-motor-return', title='TEST motor', severity='critical',
            first_observed_at=timezone.now(), last_observed_at=timezone.now(),
            metrics={'synthetic': True, 'component_id': component.pk},
        )
        self.assertEqual(equipment_return_url(self.work_order),
                         f'/machines/machine/{self.machine.pk}/equipment/?view=model&position=motor')

    @override_settings(AIMMS_SAP_WRITEBACK_ADAPTER=mock.Mock())
    def test_sap_dispatch_ambiguous_outcome_is_not_retried(self):
        self.submit_and_approve()
        self.complete()
        effect = CloseoutEffect.objects.create(
            closeout=self.work_order.structured_closeout,
            effect_type='sap_writeback', effect_key='sap-test', payload_hash='x',
        )
        adapter = mock.Mock(side_effect=TimeoutError)
        with override_settings(AIMMS_SAP_WRITEBACK_ADAPTER=adapter):
            with self.assertRaises(EffectOutcomeUnknown):
                execute_sap_writeback(effect)
        adapter.assert_called_once()

    def test_sap_requires_actual_acknowledgement(self):
        self.submit_and_approve()
        self.complete()
        effect = CloseoutEffect.objects.create(
            closeout=self.work_order.structured_closeout,
            effect_type='sap_writeback', effect_key='sap-ack-test', payload_hash='x',
        )
        adapter = mock.Mock(return_value={'status': 'acknowledged', 'reference': 'SAP-123'})
        with override_settings(AIMMS_SAP_WRITEBACK_ADAPTER=adapter):
            self.assertEqual(execute_sap_writeback(effect), 'SAP-123')
        self.assertEqual(adapter.call_args.kwargs['idempotency_key'], effect.effect_key)

    def test_end_test_preserves_work_state_and_real_alarm(self):
        self.work_order.lifecycle_status = 'draft'
        self.work_order.save(update_fields=['lifecycle_status'])
        alarms = []
        for detector in (DETECTOR, 'threshold'):
            alarms.append(MachineAnomaly.objects.create(
                machine=self.machine, work_order=self.work_order, detector=detector,
                fingerprint=f'end-{detector}', title=detector, severity='critical',
                status='open', metrics={'synthetic': detector == DETECTOR},
                first_observed_at=timezone.now(), last_observed_at=timezone.now(),
            ))
        with mock.patch('tasks.services.service_reports.notify_test_ended') as notify, self.captureOnCommitCallbacks(execute=True):
            result = self.command('end_test', reason='Operator ended simulation', confirmed=True)
        for alarm in alarms:
            alarm.refresh_from_db()
        self.work_order.refresh_from_db()
        self.assertEqual(alarms[0].status, 'resolved')
        self.assertEqual(alarms[0].metrics['recovery_kind'], 'ended_test')
        self.assertEqual(alarms[1].status, 'open')
        self.assertEqual(self.work_order.lifecycle_status, 'draft')
        self.assertFalse(hasattr(self.work_order, 'structured_closeout'))
        self.assertFalse(result.metadata['work_completed'])
        self.assertEqual(report_state(self.work_order)['test_recovery'], {
            'total': 1, 'active': 0, 'pending': 0, 'other_active_alarms': 1,
        })
        notify.assert_called_once()
        version = self.work_order.lifecycle_version
        with self.assertRaisesMessage(CloseoutError, 'no active synthetic'):
            self.command('end_test', reason='Operator ended simulation', confirmed=True)
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_version, version)

    def test_end_test_notification_failure_does_not_hide_committed_result(self):
        alarm = MachineAnomaly.objects.create(
            machine=self.machine, work_order=self.work_order, detector=DETECTOR,
            fingerprint='end-notification-error', title='Synthetic test', severity='critical',
            status='open', metrics={'synthetic': True},
            first_observed_at=timezone.now(), last_observed_at=timezone.now(),
        )
        with mock.patch('tasks.services.service_reports.notify_test_ended', side_effect=RuntimeError('Synthetic transport failure')), self.captureOnCommitCallbacks(execute=True):
            result = self.command('end_test', reason='Operator ended simulation', confirmed=True)
        alarm.refresh_from_db()
        self.assertEqual(alarm.status, 'resolved')
        self.assertEqual(result.metadata['resolved_test_alarms'], [alarm.pk])

    def test_end_test_requires_admin_confirmation_and_reason(self):
        for actor, confirmed, reason in [(self.reviewer, True, 'End simulation'),
                                         (self.actor, False, 'End simulation'),
                                         (self.actor, True, '')]:
            with self.assertRaises(CloseoutError):
                self.command('end_test', actor=actor, confirmed=confirmed, reason=reason)

    def test_suppressed_exercise_is_pending_and_can_be_explicitly_ended(self):
        alarm = MachineAnomaly.objects.create(
            machine=self.machine, work_order=self.work_order, detector=DETECTOR,
            fingerprint='suppressed-test', title='Synthetic test', severity='critical',
            status='suppressed', metrics={'synthetic': True},
            first_observed_at=timezone.now(), last_observed_at=timezone.now(),
        )
        state = report_state(self.work_order)['test_recovery']
        self.assertEqual(state['active'], 0)
        self.assertEqual(state['pending'], 1)
        self.command('end_test', reason='Operator ended suppressed test', confirmed=True)
        alarm.refresh_from_db()
        self.assertEqual(alarm.status, 'resolved')

    def test_non_test_work_cannot_be_ended_as_an_exercise(self):
        with self.assertRaisesMessage(CloseoutError, 'no synthetic'):
            self.command('end_test', confirmed=True, reason='End simulation')
