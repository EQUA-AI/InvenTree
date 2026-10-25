"""Focused atomic automatic intake contracts using native work commands."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings

from rest_framework.test import APIClient
from tasks.models import WorkOrder, WorkOrderCommand

from assets.health_models import MachineAnomaly, MachineSignalState
from machine_health.tests.fixtures import HealthEnvMixin
from machine_health.services.test_alarm_work import trigger_with_work, work_plan
from repair.intake_scenarios import prepared_target_scenario
from repair.models import RepairPacket
from repair.work_packages import WorkPackageError


@override_settings(AIMMS_TEST_ALARMS_ENABLED=True, AIMMS_WORK_ORDERS_ENABLED=True)
class AutomaticTestAlarmWorkTest(HealthEnvMixin, TestCase):
    """One request creates one owned planned aggregate or no alarm at all."""

    def setUp(self):
        """Build disposable real signals, people and native API authorization."""
        self.build_health_env()
        self.set_signal(3.2)
        self.actor = get_user_model().objects.create_superuser(username='automatic-planner')
        self.owner = get_user_model().objects.create_superuser(username='automatic-owner')
        self.api = APIClient()
        self.api.force_authenticate(self.actor)
        self.url = f'/api/machine-health/machines/{self.machine.pk}/health/test-alarm/'

    def payload(self):
        """Read the server-prepared preview instead of inventing intake text."""
        response = self.api.get(self.url, {'binding_id': self.binding.pk})
        self.assertEqual(response.status_code, 200, response.data)
        return {
            'binding_id': self.binding.pk, 'component_id': None,
            'confirmed': True, 'test_value': 10.0,
            'idempotency_key': str(uuid.uuid4()), 'create_work_order': True,
            'plan_token': response.data['plan_token'], 'assigned_to_id': self.owner.pk,
        }

    def counts(self):
        """Track every durable aggregate for rollback and retry assertions."""
        return tuple(model.objects.count() for model in (
            MachineAnomaly, WorkOrder, RepairPacket, WorkOrderCommand,
        ))

    def test_atomic_creation_owns_source_and_preserves_measured_data(self):
        """Create real linked native draft work, never start or approve it."""
        measurements = list(MachineSignalState.objects.values())
        with patch('machine_health.services.test_alarms.notify_alarm') as notify, self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(self.url, self.payload(), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        receipt = response.data['work_order']
        source = MachineAnomaly.objects.get(pk=response.data['anomaly_id'])
        work = WorkOrder.objects.get(pk=receipt['work_order_id'])
        self.assertEqual(source.work_order_id, work.pk)
        self.assertEqual(source.repair_packet_id, receipt['repair_packet_id'])
        self.assertEqual(work.assigned_to_id, self.owner.pk)
        self.assertEqual(work.lifecycle_status, 'draft')
        self.assertEqual(work.repair_packet.work_order_id, work.pk)
        self.assertEqual(work.cards.count(), 1)
        self.assertEqual(list(MachineSignalState.objects.values()), measurements)
        self.assertIsNone(work.handovers.get().accepted_at)
        notify.assert_called_once()

    def test_replay_returns_same_receipt_even_after_source_ends_or_plan_expires(self):
        """Lost responses cannot turn into another alarm or work package."""
        payload = self.payload()
        first = self.api.post(self.url, payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        counts = self.counts()
        MachineAnomaly.objects.filter(pk=first.data['anomaly_id']).update(status='resolved')
        with patch('machine_health.services.test_alarm_work.signing.loads', side_effect=AssertionError('Replay must not revalidate an expired preview')):
            replay = self.api.post(self.url, payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data['work_order'], first.data['work_order'])
        self.assertEqual(counts, self.counts())

    def test_reused_key_cannot_change_owner_or_alarm_only_mode(self):
        """The complete command is bound to the alarm idempotency hash."""
        payload = self.payload()
        first = self.api.post(self.url, payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        counts = self.counts()
        changed = self.api.post(self.url, {**payload, 'assigned_to_id': self.actor.pk}, format='json')
        self.assertEqual(changed.status_code, 400, changed.data)
        alarm_only = {key: payload[key] for key in ('binding_id', 'component_id', 'confirmed', 'test_value', 'idempotency_key')}
        self.assertEqual(self.api.post(self.url, alarm_only, format='json').status_code, 400)
        self.assertEqual(counts, self.counts())

    def test_existing_repair_requires_explicit_separate_reason_and_is_unchanged(self):
        """Preview and refusal expose a real existing job, not a hardcoded ID."""
        existing = WorkOrder.objects.create(machine=self.machine, title='Existing bearing investigation', reference='WO-003826')
        original = dict(WorkOrder.objects.filter(pk=existing.pk).values().get())
        payload = self.payload()
        preview = self.api.get(self.url, {'binding_id': self.binding.pk})
        self.assertEqual(preview.data['duplicates'][0]['title'], existing.title)
        counts = self.counts()
        with patch('machine_health.services.test_alarms.notify_alarm') as notify, self.captureOnCommitCallbacks(execute=True):
            refused = self.api.post(self.url, payload, format='json')
        self.assertEqual(refused.status_code, 409, refused.data)
        self.assertEqual(counts, self.counts())
        notify.assert_not_called()
        payload.update(duplicate_override=True, duplicate_override_reason='Separate motor winding exercise; existing bearing job remains unchanged.')
        created = self.api.post(self.url, payload, format='json')
        self.assertEqual(created.status_code, 200, created.data)
        self.assertNotEqual(created.data['work_order']['work_order_id'], existing.pk)
        self.assertEqual(original, dict(WorkOrder.objects.filter(pk=existing.pk).values().get()))

    def test_native_creation_failure_rolls_back_alarm_and_notification(self):
        """No orphan alarm survives a refused or failed native work command."""
        payload = self.payload()
        counts = self.counts()
        with patch('machine_health.services.test_alarm_work.create_repair_work_package', side_effect=WorkPackageError('Native work refused')), patch('machine_health.services.test_alarms.notify_alarm') as notify, self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(counts, self.counts())
        notify.assert_not_called()

    def test_preview_tampering_actor_change_and_missing_owner_are_rejected(self):
        """Signed scope is not caller-authored text or transferable authority."""
        payload = self.payload()
        counts = self.counts()
        bad = self.api.post(self.url, {**payload, 'plan_token': payload['plan_token'] + 'x'}, format='json')
        self.assertEqual(bad.status_code, 400, bad.data)
        other = get_user_model().objects.create_superuser(username='different-planner')
        self.api.force_authenticate(other)
        self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 400)
        self.api.force_authenticate(self.actor)
        self.assertEqual(self.api.post(self.url, {**payload, 'assigned_to_id': None}, format='json').status_code, 400)
        self.assertEqual(counts, self.counts())

    def test_inactive_owner_cannot_create_alarm(self):
        """Assignment eligibility is checked again at the mutation boundary."""
        payload = self.payload()
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        counts = self.counts()
        self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 400)
        self.assertEqual(counts, self.counts())

    def test_failure_after_native_packet_creation_rolls_back_all_records(self):
        """Even a late evidence failure rolls back the already-created work and card."""
        payload = self.payload()
        counts = self.counts()
        with patch('repair.work_packages._link_anomaly', side_effect=WorkPackageError('Evidence failed after packet write')):
            response = self.api.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(counts, self.counts())

    @override_settings(AIMMS_WORK_ORDERS_ENABLED=False)
    def test_disabled_work_surface_refuses_preview(self):
        """Automatic creation cannot bypass the deployment master gate."""
        response = self.api.get(self.url, {'binding_id': self.binding.pk})
        self.assertEqual(response.status_code, 403, response.data)

    def test_pump08_template_matches_only_the_document_target(self):
        """The canonical winding-six scope never leaks into other pumps/channels."""
        machine = SimpleNamespace(asset_type='pump', source_key='P8', client=SimpleNamespace(code='internal'), parent=SimpleNamespace(name='Cedar Creek Final Effluent Pump Station'))
        target = {'component_code': 'PS-MOTOR:1', 'name': 'Motor Winding Temperature 6'}
        scenario = prepared_target_scenario(machine, target)
        self.assertEqual(scenario['fields']['title'], 'Pump 08 | High Motor Winding Temperature')
        self.assertEqual(scenario['suggested_owner_username'], 'cc.avery.patel')
        self.assertEqual(scenario['parts'], [])
        self.assertIsNone(prepared_target_scenario(machine, {**target, 'name': 'Motor Winding Temperature 5'}))


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locking')
@override_settings(AIMMS_TEST_ALARMS_ENABLED=True, AIMMS_WORK_ORDERS_ENABLED=True)
class AutomaticWorkConcurrencyTest(HealthEnvMixin, TransactionTestCase):
    """Independent connections cannot create duplicate automatic aggregates."""

    def setUp(self):
        """Prepare actual committed signal and identity fixtures."""
        self.build_health_env()
        self.set_signal(3.2)
        self.actor = get_user_model().objects.create_superuser(username='automatic-race-planner')
        self.owner = get_user_model().objects.create_superuser(username='automatic-race-owner')
        self.plan = work_plan(actor=self.actor, machine=self.machine, binding_id=self.binding.pk)

    def race(self, keys):
        """Send independent commands at the same machine lock simultaneously."""
        barrier = Barrier(2)

        def send(key):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=20)
                try:
                    anomaly, receipt = trigger_with_work(
                        actor=actor, machine=self.machine, binding_id=self.binding.pk,
                        component_id=None, test_value=10.0, idempotency_key=key,
                        assigned_to_id=self.owner.pk, plan_token=self.plan['plan_token'],
                    )
                    return anomaly.pk, receipt['work_order_id']
                except ValidationError:
                    return 'refused'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(send, keys))

    def test_same_key_race_returns_one_complete_aggregate(self):
        """A simultaneous retry returns the committed source and work IDs."""
        key = str(uuid.uuid4())
        results = self.race([key, key])
        self.assertNotEqual(results[0], 'refused')
        self.assertEqual(results[0], results[1])
        self.assertEqual(MachineAnomaly.objects.count(), 1)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(RepairPacket.objects.count(), 1)

    def test_different_key_race_cannot_create_two_active_tests(self):
        """Exactly one active event wins; the other command writes nothing."""
        results = self.race([str(uuid.uuid4()), str(uuid.uuid4())])
        self.assertEqual(results.count('refused'), 1)
        self.assertEqual(MachineAnomaly.objects.count(), 1)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(RepairPacket.objects.count(), 1)
