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
from tasks.services.staff_assignment import auto_assignment

from assets.health_models import (
    MachineAnomaly,
    MachineSignalBinding,
    MachineSignalState,
)
from assets.models import AssetMachine
from machine_health.services.test_alarm_work import trigger_with_work, work_plan
from machine_health.services.test_alarms import trigger_test_alarm
from machine_health.tests.fixtures import HealthEnvMixin
from repair.intake_scenarios import prepared_target_scenario
from repair.models import RepairPacket
from repair.work_packages import WorkPackageError
from users.models import Trade


@override_settings(AIMMS_TEST_ALARMS_ENABLED=True, AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_TEST_ALARM_EVENT_WORK_ENABLED=True)
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

    def event_payload(self):
        """The authorized test event carries no caller-authored work plan."""
        return {
            'binding_id': self.binding.pk, 'component_id': None,
            'confirmed': True, 'test_value': 10.0,
            'idempotency_key': str(uuid.uuid4()), 'event_owned_work': True,
        }

    def test_event_flag_is_declared_off_by_default(self):
        """Deployment must explicitly opt in to event-owned work creation."""
        from aimms_flags import REGISTRY

        entry = next(
            flag for flag in REGISTRY
            if flag.env_name == 'AIMMS_TEST_ALARM_EVENT_WORK_ENABLED'
        )
        self.assertFalse(entry.default)
        self.assertTrue(self.api.get(self.url).data['event_work_enabled'])

    def test_event_owned_work_is_atomic_linked_and_escalated(self):
        """One event owns one native packet and a truthful staffing decision."""
        trade, _ = Trade.objects.get_or_create(
            code='electrical', defaults={'name': 'Electrical'}
        )
        self.owner.profile.trades.add(trade)
        trade_match = auto_assignment(
            self.actor, WorkOrder(machine=self.machine),
            required_trades=[trade.code], requirements_confirmed=True,
        )
        self.assertEqual(trade_match['assigned_to'], self.owner.pk)
        self.assertEqual(trade_match['code'], 'ASSIGNMENT_QUALIFIED')
        unknown_requirements = auto_assignment(
            self.actor, WorkOrder(machine=self.machine),
            required_trades=[], requirements_confirmed=False,
        )
        self.assertEqual(unknown_requirements, {
            'assigned_to': None,
            'code': 'ASSIGNMENT_REQUIREMENTS_UNKNOWN',
            'candidates': [],
        })
        payload = self.event_payload()
        response = self.api.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        receipt = response.data['work_order']
        source = MachineAnomaly.objects.get(pk=response.data['anomaly_id'])
        work = WorkOrder.objects.get(pk=receipt['work_order_id'])
        self.assertEqual(source.work_order_id, work.pk)
        self.assertEqual(source.repair_packet_id, work.repair_packet.pk)
        self.assertEqual(receipt['anomaly_id'], source.pk)
        self.assertEqual(MachineAnomaly.objects.count(), 1)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(RepairPacket.objects.count(), 1)
        self.assertEqual(work.cards.count(), 1)
        self.assertIsNone(work.assigned_to_id)
        self.assertEqual(work.required_trades, [])
        self.assertFalse(work.trade_requirements_confirmed)
        self.assertEqual(work.priority, receipt['triage']['priority'])
        self.assertEqual(work.repair_packet.criticality, receipt['triage']['criticality'])
        self.assertEqual(receipt['assignment']['code'], 'ASSIGNMENT_REQUIREMENTS_UNKNOWN')
        self.assertIn('Supervisor review', receipt['assignment']['supervisor_escalation'])
        audit = work.events.get(event_type='WORK_PACKAGE_CREATED')
        self.assertNotIn('supervisor_escalation', audit.metadata['result_metadata']['assignment'])
        escalation = work.events.get(event_type='TEST_ALARM_ASSIGNMENT_ESCALATED')
        self.assertEqual(escalation.correlation_id, audit.correlation_id)
        self.assertEqual(escalation.metadata['assignment'], receipt['assignment'])
        self.assertEqual(escalation.metadata['source_anomaly_id'], source.pk)
        self.assertEqual(source.metrics['automatic_work_order']['assignment'], receipt['assignment'])
        counts = self.counts()
        replay = self.api.post(self.url, payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data['work_order'], receipt)
        self.assertEqual(counts, self.counts())
        changed_review = self.api.post(self.url, {
            **payload, 'duplicate_override': True,
            'duplicate_override_reason': 'A new review cannot change this command',
        }, format='json')
        self.assertEqual(changed_review.status_code, 400, changed_review.data)
        self.assertEqual(counts, self.counts())
        self.assertEqual(work.events.filter(event_type='TEST_ALARM_ASSIGNMENT_ESCALATED').count(), 1)
        self.binding.active = False
        self.binding.save(update_fields=['active'])
        replay_after_change = self.api.post(self.url, payload, format='json')
        self.assertEqual(replay_after_change.status_code, 200, replay_after_change.data)
        self.assertEqual(replay_after_change.data['work_order'], receipt)
        changed = self.api.post(self.url, {**payload, 'test_value': 11}, format='json')
        self.assertEqual(changed.status_code, 400, changed.data)
        self.assertEqual(counts, self.counts())

    def test_event_owned_gate_scope_and_target_fail_closed(self):
        """The event path retains both feature gates, scope and exact mapping."""
        payload = self.event_payload()
        counts = self.counts()
        with override_settings(AIMMS_TEST_ALARM_EVENT_WORK_ENABLED=False):
            self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 403)
        with override_settings(AIMMS_WORK_ORDERS_ENABLED=False):
            self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 403)
        with override_settings(AIMMS_TEST_ALARMS_ENABLED=False):
            self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 404)
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: set()):
            self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 404)
        self.actor.is_superuser = False
        self.actor.save(update_fields=['is_superuser'])
        self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 403)
        self.actor.is_superuser = True
        self.actor.save(update_fields=['is_superuser'])
        foreign_machine = AssetMachine.objects.create(name='Foreign target', client=self.client_tenant)
        foreign_binding = MachineSignalBinding.objects.create(
            machine=foreign_machine, source=self.source, external_key='foreign-target',
            display_name='Foreign target', signal_kind='vibration', unit='mm/s', critical_max=9,
        )
        self.assertEqual(self.api.post(self.url, {**payload, 'binding_id': foreign_binding.pk}, format='json').status_code, 400)
        self.assertEqual(self.api.post(self.url, {**payload, 'component_id': 99999}, format='json').status_code, 400)
        for planning in (
            {'create_work_order': True},
            {'plan_token': 'manual-plan'},
            {'assigned_to_id': self.owner.pk},
        ):
            self.assertEqual(self.api.post(self.url, {**payload, **planning}, format='json').status_code, 400)
        self.assertEqual(counts, self.counts())

    def test_event_owned_duplicate_review_is_explicit_audited_and_replay_safe(self):
        """A reviewed duplicate creates one unassigned aggregate and binds the reason."""
        existing = WorkOrder.objects.create(machine=self.machine, title='Existing bearing investigation')
        original = dict(WorkOrder.objects.filter(pk=existing.pk).values().get())
        measurements = list(MachineSignalState.objects.values())
        payload = self.event_payload()
        preview = self.api.get(self.url, {'binding_id': self.binding.pk})
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['duplicates'][0]['work_order_id'], existing.pk)
        counts = self.counts()
        with patch('machine_health.services.test_alarms.notify_alarm') as notify, self.captureOnCommitCallbacks(execute=True):
            refused = self.api.post(self.url, payload, format='json')
        self.assertEqual(refused.status_code, 409, refused.data)
        self.assertEqual(refused.data['code'], 'DUPLICATE_OPEN_REPAIR')
        self.assertEqual(counts, self.counts())

        notify.assert_not_called()

        reason = 'Separate winding exercise; existing bearing investigation remains unchanged.'
        reviewed = {**payload, 'duplicate_override': True, 'duplicate_override_reason': reason}
        created = self.api.post(self.url, reviewed, format='json')
        self.assertEqual(created.status_code, 200, created.data)
        source = MachineAnomaly.objects.get(pk=created.data['anomaly_id'])
        work = WorkOrder.objects.get(pk=created.data['work_order']['work_order_id'])
        self.assertNotEqual(work.pk, existing.pk)
        self.assertEqual(source.work_order_id, work.pk)
        self.assertEqual(source.repair_packet_id, work.repair_packet.pk)
        self.assertIsNone(work.assigned_to_id)
        self.assertFalse(work.trade_requirements_confirmed)
        self.assertEqual(created.data['work_order']['assignment']['code'], 'ASSIGNMENT_REQUIREMENTS_UNKNOWN')
        self.assertEqual(work.events.get(event_type='WORK_PACKAGE_CREATED').reason, reason)
        self.assertEqual(work.events.filter(event_type='TEST_ALARM_ASSIGNMENT_ESCALATED').count(), 1)
        self.assertEqual(original, dict(WorkOrder.objects.filter(pk=existing.pk).values().get()))
        self.assertEqual(measurements, list(MachineSignalState.objects.values()))

        counts = self.counts()
        replay = self.api.post(self.url, reviewed, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data['work_order'], created.data['work_order'])
        for changed in (
            {**reviewed, 'duplicate_override_reason': 'A different reviewed reason'},
            {**reviewed, 'duplicate_override': False, 'duplicate_override_reason': ''},
        ):
            response = self.api.post(self.url, changed, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(counts, self.counts())

    def test_event_owned_legacy_no_override_receipt_still_replays(self):
        """A pending pre-review command retains its exact no-override identity."""
        payload = self.event_payload()

        def legacy_trigger(**kwargs):
            kwargs['command_context'] = {'create_work_order': True, 'event_owned': True}
            return trigger_test_alarm(**kwargs)

        with patch('machine_health.services.test_alarm_work.trigger_test_alarm', side_effect=legacy_trigger):
            first = self.api.post(self.url, payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        counts = self.counts()
        replay = self.api.post(self.url, payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data, first.data)
        changed = self.api.post(self.url, {
            **payload, 'duplicate_override': True,
            'duplicate_override_reason': 'A new review cannot change an old command',
        }, format='json')
        self.assertEqual(changed.status_code, 400, changed.data)
        self.assertEqual(counts, self.counts())

    def test_event_owned_review_fields_require_a_matching_nonblank_reason(self):
        """Acknowledgement and reason must agree at both API and service boundaries."""
        payload = self.event_payload()
        counts = self.counts()
        for review in (
            {'duplicate_override': True},
            {'duplicate_override': True, 'duplicate_override_reason': '   '},
            {'duplicate_override': False, 'duplicate_override_reason': 'No acknowledgement'},
        ):
            response = self.api.post(self.url, {**payload, **review}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        service_alarm = {key: value for key, value in payload.items() if key not in ('confirmed', 'event_owned_work')}
        for planning in (
            {'create_work_order': True},
            {'plan_token': 'manual-plan'},
            {'assigned_to_id': self.owner.pk},
            {'duplicate_override': True, 'duplicate_override_reason': '   '},
            {'duplicate_override': False, 'duplicate_override_reason': 'No acknowledgement'},
        ):
            with self.assertRaises(WorkPackageError):
                trigger_with_work(actor=self.actor, machine=self.machine, event_owned=True, **planning, **service_alarm)
        self.assertEqual(counts, self.counts())

    def test_event_owned_duplicate_review_retains_authorization_and_scope(self):
        """Review never substitutes for alarm, creation, assignment or scope authority."""
        WorkOrder.objects.create(machine=self.machine, title='Private existing repair')
        payload = {
            **self.event_payload(), 'duplicate_override': True,
            'duplicate_override_reason': 'Independent reviewed exercise',
        }
        counts = self.counts()
        self.api.force_authenticate(user=None)
        self.assertIn(self.api.post(self.url, payload, format='json').status_code, (401, 403))
        self.api.force_authenticate(self.actor)
        self.actor.is_superuser = False
        self.actor.save(update_fields=['is_superuser'])
        self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 403)
        self.actor.is_superuser = True
        self.actor.save(update_fields=['is_superuser'])
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: set()):
            self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 404)
        with patch('users.permissions.check_user_role', return_value=False):
            self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 403)
        with patch.object(self.actor, 'has_perm', side_effect=lambda permission: permission != 'tasks.assign_workorder') as has_perm:
            response = self.api.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        has_perm.assert_any_call('tasks.assign_workorder')
        self.assertEqual(counts, self.counts())

    @override_settings(AIMMS_TEST_ALARM_EVENT_WORK_ENABLED=False)
    def test_event_flag_off_retains_the_reviewed_work_path(self):
        """Disabling event-owned creation preserves explicit preview and assignment."""
        targets = self.api.get(self.url)
        self.assertEqual(targets.status_code, 200, targets.data)
        self.assertFalse(targets.data['event_work_enabled'])
        payload = self.payload()
        response = self.api.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        work = WorkOrder.objects.get(pk=response.data['work_order']['work_order_id'])
        self.assertEqual(work.assigned_to_id, self.owner.pk)

    def test_event_owned_existing_invisible_work_refuses_without_disclosure(self):
        """Automatic work cannot override or disclose invisible existing work."""
        WorkOrder.objects.create(machine=self.machine, title='Private existing repair')
        counts = self.counts()
        with patch('machine_health.services.test_alarm_work.scoped_work_orders', return_value=WorkOrder.objects.none()):
            for review in ({}, {'duplicate_override': True, 'duplicate_override_reason': 'Reviewed elsewhere'}):
                response = self.api.post(self.url, {**self.event_payload(), **review}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertNotIn('Private existing repair', str(response.data))
        self.assertEqual(counts, self.counts())

    def test_event_owned_failure_rolls_back_and_same_key_retries(self):
        """A late failure leaves no orphan and the same event can be retried."""
        payload = self.event_payload()
        counts = self.counts()
        with patch('repair.work_packages._link_anomaly', side_effect=WorkPackageError('link failed')):
            response = self.api.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(counts, self.counts())
        self.assertEqual(self.api.post(self.url, payload, format='json').status_code, 200)

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
@override_settings(AIMMS_TEST_ALARMS_ENABLED=True, AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_TEST_ALARM_EVENT_WORK_ENABLED=True)
class AutomaticWorkConcurrencyTest(HealthEnvMixin, TransactionTestCase):
    """Independent connections cannot create duplicate automatic aggregates."""

    def setUp(self):
        """Prepare actual committed signal and identity fixtures."""
        self.build_health_env()
        self.set_signal(3.2)
        self.actor = get_user_model().objects.create_superuser(username='automatic-race-planner')
        self.owner = get_user_model().objects.create_superuser(username='automatic-race-owner')
        self.plan = work_plan(actor=self.actor, machine=self.machine, binding_id=self.binding.pk)

    def race(self, keys, event_owned=False, duplicate_review=None):
        """Send independent commands at the same machine lock simultaneously."""
        barrier = Barrier(2)

        def send(key):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=20)
                try:
                    planning = {'event_owned': True} if event_owned else {
                        'assigned_to_id': self.owner.pk, 'plan_token': self.plan['plan_token'],
                    }
                    planning.update(duplicate_review or {})
                    anomaly, receipt = trigger_with_work(
                        actor=actor, machine=self.machine, binding_id=self.binding.pk,
                        component_id=None, test_value=10.0, idempotency_key=key,
                        **planning,
                    )
                    return anomaly.pk, receipt['work_order_id'], receipt['repair_packet_id']
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

    def test_event_owned_same_key_race_returns_one_aggregate(self):
        """Concurrent deliveries share one event and owning work package."""
        key = str(uuid.uuid4())
        results = self.race([key, key], event_owned=True)
        self.assertNotEqual(results[0], 'refused')
        self.assertEqual(results[0], results[1])
        self.assertEqual(MachineAnomaly.objects.count(), 1)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.assertEqual(RepairPacket.objects.count(), 1)

    def test_event_owned_reviewed_duplicate_same_key_race_is_atomic(self):
        """Concurrent reviewed deliveries leave existing work and create one aggregate."""
        existing = WorkOrder.objects.create(machine=self.machine, title='Existing independent repair')
        original = dict(WorkOrder.objects.filter(pk=existing.pk).values().get())
        key = str(uuid.uuid4())
        results = self.race([key, key], event_owned=True, duplicate_review={
            'duplicate_override': True,
            'duplicate_override_reason': 'Reviewed independent exercise',
        })
        self.assertNotEqual(results[0], 'refused')
        self.assertEqual(results[0], results[1])
        self.assertEqual(MachineAnomaly.objects.count(), 1)
        self.assertEqual(WorkOrder.objects.count(), 2)
        self.assertEqual(RepairPacket.objects.count(), 1)
        work = WorkOrder.objects.get(pk=results[0][1])
        self.assertEqual(work.repair_packet.pk, results[0][2])
        self.assertIsNone(work.assigned_to_id)
        self.assertEqual(work.events.filter(event_type='TEST_ALARM_ASSIGNMENT_ESCALATED').count(), 1)
        self.assertEqual(original, dict(WorkOrder.objects.filter(pk=existing.pk).values().get()))

    def test_event_owned_different_keys_cannot_create_two_active_tests(self):
        """Separate concurrent deliveries cannot each own an active signal."""
        results = self.race([str(uuid.uuid4()), str(uuid.uuid4())], event_owned=True)
        self.assertEqual(results.count('refused'), 1)
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
