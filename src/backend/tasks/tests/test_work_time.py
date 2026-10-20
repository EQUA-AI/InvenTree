"""O15 attribution, idempotency, retained corrections and telemetry proposals."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from assets.models import (
    AssetMachine,
    Client,
    ClientScopeGrant,
    HealthSource,
    MachineSignalBinding,
)
from tasks.models import WorkOrder
from tasks.services.work_time import command, propose_downtime, time_projection
from tasks.time_models import WorkDowntime, WorkTimeInterval
from users.models import RuleSet


@override_settings(
    USE_TZ=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
)
class WorkTimeTests(TestCase):
    """Exercise real recorded time, corrections and telemetry with no external service."""

    def setUp(self):
        """Create isolated actors, scoped Work and catalogue records."""
        self.actor = get_user_model().objects.create_superuser(
            'time-engineer', 't@example.org', 'password'
        )
        self.tenant = Client.objects.create(name='Time', code='time')
        ClientScopeGrant.objects.create(user=self.actor, client=self.tenant)
        self.machine = AssetMachine.objects.create(name='Time pump', client=self.tenant)
        self.work = WorkOrder.objects.create(
            title='Time', machine=self.machine, lifecycle_status='in_progress'
        )
        self.start = timezone.now() + timedelta(seconds=1)
        self.client.force_login(self.actor)

    def run_command(
        self, operation, fields=None, key=None, version=None, reconcile_only=False
    ):
        """Execute a versioned Work recording command."""
        self.work.refresh_from_db()
        return command(
            work_order_id=self.work.pk,
            actor=self.actor,
            operation=operation,
            fields=fields or {},
            expected_version=self.work.lifecycle_version
            if version is None
            else version,
            idempotency_key=f'{self.actor.pk}:{key or uuid.uuid4()}',
            reconcile_only=reconcile_only,
        )

    def test_server_clock_start_pause_stop_and_unknown_gaps(self):
        """Server clock start pause stop and unknown gaps."""
        with patch('tasks.services.work_time.timezone.now', return_value=self.start):
            saved = self.run_command('start', key='start')
            self.assertEqual(self.run_command('start', key='start', version=1), saved)
        with patch(
            'tasks.services.work_time.timezone.now',
            return_value=self.start + timedelta(minutes=10),
        ):
            self.run_command('pause')
        with patch(
            'tasks.services.work_time.timezone.now',
            return_value=self.start + timedelta(minutes=20),
        ):
            self.run_command('start')
        with patch(
            'tasks.services.work_time.timezone.now',
            return_value=self.start + timedelta(minutes=25),
        ):
            result = self.run_command('stop')
        self.assertEqual(result['time']['recorded_seconds'], 900)
        self.assertEqual(len(result['time']['gaps']), 2)
        self.assertEqual(WorkTimeInterval.objects.count(), 2)

    def test_correction_rejects_same_person_overlap_on_other_work(self):
        """A supervisor cannot double count a person's other job, open or closed."""
        row = WorkTimeInterval.objects.create(
            work_order=self.work,
            person=self.actor,
            recorded_by=self.actor,
            started_at=self.start,
            ended_at=self.start + timedelta(minutes=5),
        )
        other = WorkOrder.objects.create(title='Other job', machine=self.machine)
        conflicting = WorkTimeInterval.objects.create(
            work_order=other,
            person=self.actor,
            recorded_by=self.actor,
            started_at=self.start + timedelta(minutes=10),
            ended_at=self.start + timedelta(minutes=20),
        )
        fields = {
            'interval_id': row.pk,
            'started_at': self.start.isoformat(),
            'ended_at': (self.start + timedelta(minutes=15)).isoformat(),
            'reason': 'Checked correction',
        }
        with patch(
            'tasks.services.work_time.timezone.now',
            return_value=self.start + timedelta(hours=1),
        ):
            for end in [self.start + timedelta(minutes=20), None]:
                conflicting.ended_at = end
                conflicting.save(update_fields=['ended_at'])
                with self.subTest(other_end=end), self.assertRaises(ValidationError):
                    self.run_command('correct', fields)
        self.assertFalse(WorkTimeInterval.objects.filter(supersedes=row).exists())

    def test_closed_work_does_not_accrue_time_or_trailing_gaps(self):
        """Closed Work caps an unclosed timer and its trailing gap at closure."""
        from tasks.models import WorkOrderEvent

        row = WorkTimeInterval.objects.create(
            work_order=self.work,
            person=self.actor,
            recorded_by=self.actor,
            started_at=self.start,
        )
        cutoff = self.start + timedelta(minutes=10)
        for status in ['completed', 'canceled']:
            self.work.lifecycle_status = status
            self.work.actual_completed_at = cutoff if status == 'completed' else None
            self.work.save()
            if status == 'canceled':
                event = WorkOrderEvent.objects.create(
                    work_order=self.work,
                    event_type='CANCEL',
                    from_status='in_progress',
                    to_status=status,
                    actor=self.actor,
                    correlation_id=uuid.uuid4(),
                )
                WorkOrderEvent.objects.filter(pk=event.pk).update(created_at=cutoff)
                later = WorkOrderEvent.objects.create(
                    work_order=self.work,
                    event_type='WORK_TIME_CORRECTED',
                    from_status='canceled',
                    to_status='canceled',
                    actor=self.actor,
                    correlation_id=uuid.uuid4(),
                )
                WorkOrderEvent.objects.filter(pk=later.pk).update(
                    created_at=cutoff + timedelta(hours=1)
                )

            with self.subTest(status=status):
                result = time_projection(self.work, self.start + timedelta(hours=2))
                self.assertEqual(result['recorded_seconds'], 600)
                self.assertTrue(result['open_at_closure'])
                row.ended_at = self.start + timedelta(minutes=5)
                row.save()
                result = time_projection(self.work, self.start + timedelta(hours=3))
                self.assertEqual(result['gaps'][-1]['ended_at'], cutoff.isoformat())
                row.ended_at = None
                row.save()

    def test_foreign_work_get_post_and_proposal_are_not_disclosed(self):
        """Exercise an actually foreign tenant, not just a read-only role."""
        other = Client.objects.create(name='Foreign time', code='foreign-time')
        machine = AssetMachine.objects.create(name='Foreign pump', client=other)
        foreign = WorkOrder.objects.create(title='Foreign', machine=machine)
        url = f'/api/tasks/work-orders/{foreign.pk}/time/'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url, {'propose': 'true'}).status_code, 404)
        self.assertEqual(
            self.client.post(
                url,
                {
                    'operation': 'start',
                    'fields': {},
                    'expected_version': foreign.lifecycle_version,
                    'idempotency_key': f'{self.actor.pk}:foreign',
                },
                content_type='application/json',
            ).status_code,
            404,
        )
        self.assertFalse(WorkTimeInterval.objects.exists())

    def test_no_records_is_unknown_not_zero(self):
        """No records is unknown not zero."""
        self.assertIsNone(time_projection(self.work)['recorded_seconds'])
        self.assertEqual(time_projection(self.work)['coverage'], 'unknown')

    def test_correction_keeps_original_and_rejects_overlap(self):
        """Correction keeps original and rejects overlap."""
        row = WorkTimeInterval.objects.create(
            work_order=self.work,
            person=self.actor,
            recorded_by=self.actor,
            started_at=self.start,
            ended_at=self.start + timedelta(minutes=10),
            end_action='stop',
        )
        with patch(
            'tasks.services.work_time.timezone.now',
            return_value=self.start + timedelta(hours=1),
        ):
            fields = {
                'interval_id': row.pk,
                'started_at': self.start.isoformat(),
                'ended_at': (self.start + timedelta(minutes=5)).isoformat(),
                'reason': 'Supervisor corrected departure',
            }
            with self.assertRaises(ValidationError):
                self.run_command(
                    'correct',
                    {
                        **fields,
                        'started_at': self.start.replace(tzinfo=None).isoformat(),
                    },
                )
            other = WorkTimeInterval.objects.create(
                work_order=self.work,
                person=self.actor,
                recorded_by=self.actor,
                started_at=self.start + timedelta(minutes=20),
                ended_at=self.start + timedelta(minutes=25),
            )
            with self.assertRaises(ValidationError):
                self.run_command(
                    'correct',
                    {
                        **fields,
                        'ended_at': (self.start + timedelta(minutes=21)).isoformat(),
                    },
                )
            other.delete()
            result = self.run_command('correct', fields)
        row.refresh_from_db()
        self.assertEqual(row.ended_at, self.start + timedelta(minutes=10))
        self.assertEqual(result['time']['recorded_seconds'], 300)
        self.assertTrue(result['time']['intervals'][0]['superseded'])

    def test_supervisor_closes_a_forgotten_open_interval_by_retained_correction(self):
        """A forgotten timer can be corrected without editing its original."""
        with patch('tasks.services.work_time.timezone.now', return_value=self.start):
            self.run_command('start')
        original = WorkTimeInterval.objects.get(work_order=self.work)
        supervisor = get_user_model().objects.create_superuser(
            'time-supervisor', 'supervisor@example.org', 'password'
        )
        ClientScopeGrant.objects.create(user=supervisor, client=self.tenant)
        self.work.refresh_from_db()
        with patch(
            'tasks.services.work_time.timezone.now',
            return_value=self.start + timedelta(hours=1),
        ):
            saved = command(
                work_order_id=self.work.pk,
                actor=supervisor,
                operation='correct',
                fields={
                    'interval_id': original.pk,
                    'started_at': self.start.isoformat(),
                    'ended_at': (self.start + timedelta(minutes=10)).isoformat(),
                    'reason': 'Supervisor checked the departure record',
                },
                expected_version=self.work.lifecycle_version,
                idempotency_key=f'{supervisor.pk}:{uuid.uuid4()}',
            )
            other = WorkOrder.objects.create(
                title='Next job', machine=self.machine, lifecycle_status='in_progress'
            )
            command(
                work_order_id=other.pk,
                actor=self.actor,
                operation='start',
                fields={},
                expected_version=other.lifecycle_version,
                idempotency_key=f'{self.actor.pk}:{uuid.uuid4()}',
            )
        original.refresh_from_db()
        self.assertIsNone(original.ended_at)
        self.assertTrue(saved['time']['intervals'][0]['superseded'])
        self.assertEqual(saved['time']['recorded_seconds'], 600)
        self.assertEqual(saved['time']['intervals'][1]['person_id'], self.actor.pk)
        self.assertEqual(
            saved['time']['intervals'][1]['recorded_by'], 'time-supervisor'
        )
        self.assertTrue(
            WorkTimeInterval.objects.filter(
                work_order=other, person=self.actor, ended_at__isnull=True
            ).exists()
        )

    def test_read_only_foreign_scope_stale_and_permission_boundaries(self):
        """Read only foreign scope stale and permission boundaries."""
        group = Group.objects.create(name='Time readers')
        reader = get_user_model().objects.create_user('time-reader')
        reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        ClientScopeGrant.objects.create(user=reader, client=self.tenant)
        self.client.force_login(reader)
        url = f'/api/tasks/work-orders/{self.work.pk}/time/'
        # Canonical tasks API uses /api/work-orders/; assert actual route too.
        self.assertEqual(
            self.client.post(url, {}, content_type='application/json').status_code, 403
        )
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.run_command('start')
        from tasks.services.work_orders import StaleVersion

        with self.assertRaises(StaleVersion):
            self.run_command('pause', version=1)

    def telemetry(self):
        """Build a bounded run/stop telemetry fixture."""
        source = HealthSource.objects.create(name='State', source_type='manual')
        binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            external_key='state',
            signal_kind='run_stop',
            display_name='Run/stop',
        )
        start = timezone.now() - timedelta(minutes=10)
        trend = {
            'available': True,
            'window_start': (start - timedelta(seconds=5)).isoformat(),
            'window_end': (start + timedelta(minutes=5)).isoformat(),
            'samples': [
                {
                    'observed_at': (start + timedelta(seconds=s)).isoformat(),
                    'plot_value': v,
                    'quality': q,
                }
                for s, v, q in [
                    (-5, 1, 'good'),
                    (0, 0, 'good'),
                    (5, 0, 'good'),
                    (10, 1, 'good'),
                ]
            ],
        }
        return binding, trend

    def test_downtime_proposal_confirm_replay_and_preserved_correction(self):
        """Downtime proposal confirm replay and preserved correction."""
        _binding, trend = self.telemetry()
        with patch('machine_health.services.trends.read_trend', return_value=trend):
            proposal = propose_downtime(self.work, self.actor)['proposals'][0]
        result = self.run_command(
            'downtime', {'token': proposal['token']}, key='confirm'
        )
        self.assertEqual(
            self.run_command(
                'downtime', {'token': proposal['token']}, key='confirm', version=1
            ),
            result,
        )
        self.assertEqual(WorkDowntime.objects.count(), 1)
        fields = {
            'token': proposal['token'],
            'supersedes': result['downtime'][0]['id'],
            'started_at': proposal['started_at'],
            'ended_at': proposal['ended_at'],
            'reason': 'Technician checked historian window',
        }
        corrected = self.run_command('downtime', fields)
        self.assertEqual(len(corrected['downtime']), 2)
        self.assertTrue(corrected['downtime'][1]['superseded'])

    def test_no_telemetry_does_not_propose(self):
        """A missing mapping cannot produce a stop window."""
        self.assertEqual(propose_downtime(self.work, self.actor)['proposals'], [])

    def test_bad_quality_alone_breaks_the_stop_window(self):
        """Good cadence does not repair a bad sample inside a stop."""
        _binding, trend = self.telemetry()
        with patch('machine_health.services.trends.read_trend', return_value=trend):
            self.assertEqual(
                len(propose_downtime(self.work, self.actor)['proposals']), 1
            )
            trend['samples'][2]['quality'] = 'bad'
            self.assertEqual(propose_downtime(self.work, self.actor)['proposals'], [])

    def test_gap_alone_breaks_the_stop_window(self):
        """All-good samples cannot bridge a missing interval."""
        _binding, trend = self.telemetry()
        with patch('machine_health.services.trends.read_trend', return_value=trend):
            self.assertEqual(
                len(propose_downtime(self.work, self.actor)['proposals']), 1
            )
            start = timezone.datetime.fromisoformat(trend['samples'][1]['observed_at'])
            trend['samples'][2]['observed_at'] = (
                start + timedelta(seconds=60)
            ).isoformat()
            trend['samples'][3]['observed_at'] = (
                start + timedelta(seconds=65)
            ).isoformat()
            self.assertEqual(propose_downtime(self.work, self.actor)['proposals'], [])

    def test_unsigned_downtime_is_rejected(self):
        """Unsigned downtime is rejected."""
        with self.assertRaises(ValidationError):
            self.run_command('downtime', {'token': 'invented'})

    def test_already_stopped_at_window_start_has_unknown_stop_boundary(self):
        """An initial stopped sample does not establish when downtime began."""
        _binding, trend = self.telemetry()
        trend['samples'] = trend['samples'][1:]
        with patch('machine_health.services.trends.read_trend', return_value=trend):
            self.assertEqual(propose_downtime(self.work, self.actor)['proposals'], [])

    def test_work_editor_cannot_correct_another_interval_without_supervisor_role(self):
        """Work edit permission alone cannot correct attributed intervals."""
        from rest_framework.exceptions import PermissionDenied

        self.actor.is_superuser = False
        self.actor.save(update_fields=['is_superuser'])
        group = Group.objects.create(name='Time editors')
        self.actor.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group,
            name='work_order',
            defaults={'can_view': True, 'can_change': True},
        )
        with self.assertRaises(PermissionDenied):
            self.run_command(
                'correct',
                {
                    'interval_id': 1,
                    'started_at': self.start.isoformat(),
                    'ended_at': self.start.isoformat(),
                    'reason': 'Unauthorized correction',
                },
            )

    def test_exact_replay_reauthorizes_an_inactive_actor(self):
        """A retained receipt never bypasses current actor authority."""
        from django.core.exceptions import PermissionDenied

        self.run_command('start', key='retained')
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            self.run_command('start', key='retained', version=1)

    def test_reconciliation_seals_absence_and_never_executes_missing_original(self):
        """A resolved absent intent cannot later create work or downtime facts."""
        result = self.run_command('start', key='absent', reconcile_only=True)
        self.assertEqual(result['record_command_outcome'], 'not_committed')
        self.assertEqual(self.run_command('start', key='absent', version=1), result)
        self.assertEqual(WorkTimeInterval.objects.count(), 0)
        saved = self.run_command('start', key='different')
        self.assertEqual(
            self.run_command('start', key='different', version=1, reconcile_only=True),
            saved,
        )
