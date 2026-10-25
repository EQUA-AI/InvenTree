"""Synthetic test conditions must never corrupt the plant's actual evidence."""

import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from machine_health.services.test_alarms import trigger_test_alarm
from machine_health.tests.fixtures import HealthEnvMixin
from tasks.scope import ScopeError


@override_settings(AIMMS_TEST_ALARMS_ENABLED=True)
class TestAlarmAPITest(HealthEnvMixin, TestCase):
    """Authorized, confirmed, exact-target and replay-safe test commands."""

    def setUp(self):
        self.build_health_env()
        self.state = self.set_signal(3.2)
        self.actor = get_user_model().objects.create_superuser(
            username='test-alarm-admin', email='test@example.com', password='pw'
        )
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = f'/api/machine-health/machines/{self.machine.pk}/health/test-alarm/'

    def payload(self):
        """A confirmed pump-level signal, not a guessed motor relationship."""
        return {'binding_id': self.binding.pk, 'component_id': None,
                'confirmed': True, 'idempotency_key': str(uuid.uuid4())}

    def test_raise_replay_and_measurements_unchanged(self):
        from assets.health_models import MachineAnomaly, MachineSignalState

        before = list(MachineSignalState.objects.values())
        payload = self.payload()
        with mock.patch(
            'machine_health.services.test_alarms.notify_alarm'
        ) as notify, self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        anomaly = MachineAnomaly.objects.get(pk=response.data['anomaly_id'])
        self.assertTrue(anomaly.metrics['synthetic'])
        self.assertIn('TEST', anomaly.title)
        self.assertAlmostEqual(anomaly.metrics['test_value'], 9.9)
        notify.assert_called_once()
        self.assertEqual(self.api.post(self.url, payload, format='json').data, response.data)
        self.assertEqual(list(MachineSignalState.objects.values()), before)

    def test_confirmation_required(self):
        payload = {**self.payload(), 'confirmed': False}
        self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 400)

    def test_signal_annotations_are_separate_and_follow_test_lifecycle(self):
        from assets.health_models import MachineAnomaly, MachineSignalState

        before = list(MachineSignalState.objects.values())
        with mock.patch('machine_health.services.test_alarms.notify_alarm'):
            raised = self.api.post(self.url, self.payload(), format='json')
        self.assertEqual(raised.status_code, 200, raised.data)
        anomaly = MachineAnomaly.objects.get(pk=raised.data['anomaly_id'])
        signals_url = self.url.replace('test-alarm/', 'signals/')
        for status in ['open', 'acknowledged', 'suppressed', 'resolved']:
            anomaly.status = status
            anomaly.save(update_fields=['status'])
            response = self.api.get(signals_url)
            self.assertEqual(response.status_code, 200, response.data)
            [row] = response.data['results']
            self.assertEqual(row['value'], 3.2)
            self.assertEqual(row['state'], 'normal')
            summary = self.api.get(self.url.replace('test-alarm/', ''))
            self.assertEqual(summary.status_code, 200, summary.data)
            self.assertEqual(summary.data['synthetic_active_count'], int(status in ['open', 'acknowledged']))
            if status in ['open', 'acknowledged']:
                [annotation] = row['test_alarms']
                self.assertEqual(annotation['anomaly_id'], anomaly.pk)
                self.assertAlmostEqual(annotation['test_value'], 9.9)
                self.assertEqual(annotation['status'], status)
            else:
                self.assertEqual(row['test_alarms'], [])
        self.assertEqual(list(MachineSignalState.objects.values()), before)

    def test_annotation_requires_actual_binding_and_machine_scope(self):
        from assets.health_models import MachineAnomaly

        with mock.patch('machine_health.services.test_alarms.notify_alarm'):
            raised = self.api.post(self.url, self.payload(), format='json')
        anomaly = MachineAnomaly.objects.get(pk=raised.data['anomaly_id'])
        anomaly.bindings.clear()
        url = self.url.replace('test-alarm/', 'signals/')
        self.assertEqual(self.api.get(url).data['results'][0]['test_alarms'], [])
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: set()):
            self.assertEqual(self.api.get(url).status_code, 404)

    @override_settings(AIMMS_TEST_ALARMS_ENABLED=False)
    def test_disabled_is_not_exposed(self):
        self.assertEqual(self.api.get(self.url).status_code, 404)

    def test_staff_is_not_alarm_authority(self):
        self.actor.is_superuser = False
        self.actor.save()
        self.assertEqual(self.api.post(self.url, self.payload(), format='json').status_code, 403)

    def test_missing_scope_is_nondisclosing(self):
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: set()):
            self.assertEqual(self.api.get(self.url).status_code, 404)

    def test_component_cannot_be_invented(self):
        self.assertEqual(self.api.post(self.url, {
            **self.payload(), 'component_id': 99999,
        }, format='json').status_code, 400)

    def test_corrupt_foreign_component_mapping_is_not_a_test_target(self):
        from assets.models import AssetComponent, AssetMachine, DictionaryPoint
        from assets.registry import ensure_pump, register_station
        from part.models import Part

        station = register_station(client=self.client_tenant, name='Test station',
                                   source_namespace='test-alarm', source_entity_uuid=uuid.uuid4(),
                                   source_key='TEST_STATION')
        pump = ensure_pump(station, 'P8')
        other = AssetMachine.objects.create(name='Foreign component owner', client=self.client_tenant)
        component = AssetComponent.objects.create(machine=other, code='MOTOR', name='Foreign motor',
                                                 part=Part.objects.create(name='Foreign motor', IPN='TEST-MOTOR'))
        point = DictionaryPoint.objects.create(station=station, machine=pump,
                                               path='/foreign', raw_tag='foreign', display_name='Foreign mapping',
                                               component=None)
        self.binding.machine = pump
        self.binding.dictionary_point = point
        self.binding.save(update_fields=['machine', 'dictionary_point'])
        DictionaryPoint.objects.filter(pk=point.pk).update(component=component)
        url = f'/api/machine-health/machines/{pump.pk}/health/test-alarm/'
        self.assertEqual(self.api.get(url).data['results'], [])
        self.assertEqual(self.api.post(url, {**self.payload(), 'component_id': component.pk},
                                       format='json').status_code, 400)

    def test_missing_configured_limit_requires_explicit_value(self):
        self.binding.critical_max = None
        self.binding.save()
        [target] = self.api.get(self.url).data['results']
        self.assertTrue(target['eligible'])
        self.assertEqual(target['value'], 3.2)
        self.assertEqual(target['limit_source'], 'unconfigured')
        self.assertEqual(self.api.post(self.url, self.payload(), format='json').status_code, 400)
        response = self.api.post(self.url, {**self.payload(), 'test_value': 12}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_explicit_value_and_replay_identity_preserve_every_measurement_and_limit(self):
        from assets.health_models import MachineSignalBinding, MachineSignalState

        before = list(MachineSignalState.objects.values())
        limits = list(MachineSignalBinding.objects.values())
        payload = {**self.payload(), 'test_value': 15.25}
        response = self.api.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['test_value'], 15.25)
        self.assertEqual(self.api.post(self.url, payload, format='json').data, response.data)
        self.assertEqual(self.api.post(self.url, {**payload, 'test_value': 16}, format='json').status_code, 400)
        self.assertEqual(list(MachineSignalState.objects.values()), before)
        self.assertEqual(list(MachineSignalBinding.objects.values()), limits)

    def test_test_value_must_exceed_all_upper_bounds(self):
        for value in [3.2, 9, -1]:
            response = self.api.post(self.url, {**self.payload(), 'test_value': value}, format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_nonfinite_service_values_refused(self):
        for value in [float('inf'), float('-inf'), float('nan')]:
            with self.assertRaises(ValidationError):
                trigger_test_alarm(machine=self.machine, actor=self.actor,
                                   binding_id=self.binding.pk, component_id=None,
                                   idempotency_key=uuid.uuid4(), test_value=value)

    def test_status_channel_is_listed_but_cannot_be_selected(self):
        self.binding.signal_kind = 'status'
        self.binding.save()
        [target] = self.api.get(self.url).data['results']
        self.assertFalse(target['eligible'])
        self.assertEqual(target['ineligible_reason'], 'non_numeric')
        self.assertEqual(self.api.post(self.url, self.payload(), format='json').status_code, 400)

    def test_bad_numeric_channel_keeps_measured_quality(self):
        self.state.quality = 'bad'
        self.state.save()
        [target] = self.api.get(self.url).data['results']
        self.assertTrue(target['eligible'])
        self.assertEqual(target['quality'], 'bad')
        self.assertEqual(self.api.post(self.url, self.payload(), format='json').status_code, 200)

    def test_no_sample_with_configured_limit_remains_visible(self):
        self.state.delete()
        [target] = self.api.get(self.url).data['results']
        self.assertTrue(target['eligible'])
        self.assertTrue(target['stale'])
        self.assertIsNone(target['value'])
        self.assertEqual(self.api.post(self.url, self.payload(), format='json').status_code, 200)

    def test_summary_and_board_conditions_are_test_labelled_until_recovery(self):
        from assets.health_models import MachineAnomaly
        from machine_health.services.summary import health_conditions
        from tasks.models import WorkOrder
        from tasks.serializers import WorkOrderBoardSerializer

        response = self.api.post(self.url, self.payload(), format='json')
        anomaly = MachineAnomaly.objects.get(pk=response.data['anomaly_id'])
        work = WorkOrder.objects.create(machine=self.machine, title='Synthetic exercise')
        self.assertIsNone(WorkOrderBoardSerializer(work).data['machine_condition'])
        condition = WorkOrderBoardSerializer(work, context={'request': SimpleNamespace(user=self.actor)}).data['machine_condition']
        self.assertEqual(condition['state'], 'critical')
        self.assertEqual(condition['synthetic_active_count'], 1)
        anomaly.status = 'resolved'
        anomaly.save(update_fields=['status'])
        self.assertEqual(health_conditions([self.machine])[self.machine.pk]['synthetic_active_count'], 0)

    def test_replay_cannot_change_target(self):
        payload = self.payload()
        self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 200)
        self.assertEqual(self.api.post(self.url, {
            **payload, 'binding_id': self.binding.pk + 1,
        }, format='json').status_code, 400)

    def test_another_key_cannot_duplicate_an_active_test(self):
        self.assertEqual(self.api.post(self.url, self.payload(), format='json').status_code, 200)
        self.assertEqual(self.api.post(self.url, self.payload(), format='json').status_code, 400)

    def test_service_rechecks_permissions(self):
        self.actor.is_superuser = False
        with self.assertRaises(PermissionDenied):
            trigger_test_alarm(machine=self.machine, actor=self.actor,
                               binding_id=self.binding.pk, component_id=None,
                               idempotency_key=uuid.uuid4())
