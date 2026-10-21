"""R6 P12: a machine's Open work / Completed history and its fault occurrences."""

from datetime import date, timedelta
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tasks.models import WorkOrder
from tasks.scope import ScopeError

from assets.health_models import (
    AnomalySeverity,
    AnomalyStatus,
    HealthEvidenceSnapshot,
    HealthSource,
    MachineAnomaly,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
    SnapshotReason,
    SourceType,
)
from assets.machine_history import (
    FAULT_OCCURRENCES_RULE,
    MACHINE_WORK_VIEWS_RULE,
    fault_occurrences,
)
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from assets.registry import ensure_pump, register_station
from assets.registry_models import AssetComponent, DictionaryPoint
from company.models import Company
from part.models import Part
from repair.models import RepairPacket
from users.models import RuleSet


def reader(username):
    """A normal user with the stored work_order view role and nothing else."""
    user = get_user_model().objects.create_user(username=username)
    group = Group.objects.create(name=f'{username} readers')
    user.groups.add(group)
    RuleSet.objects.update_or_create(
        group=group, name='work_order', defaults={'can_view': True}
    )
    return user


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class MachineWorkViewsTests(TestCase):
    """Open work and Completed history counts agree with every other count."""

    def setUp(self):
        """One granted client; work in every lifecycle state; two records."""
        self.reader = reader('work-views')
        self.tenant = Client.objects.create(name='Views client', code='views')
        ClientScopeGrant.objects.create(user=self.reader, client=self.tenant)
        self.machine = AssetMachine.objects.create(
            name='Views pump', client=self.tenant
        )
        self.sibling = AssetMachine.objects.create(
            name='Sibling pump', client=self.tenant
        )
        other_client = Client.objects.create(name='Other client', code='other')
        self.foreign = AssetMachine.objects.create(
            name='Foreign pump', client=other_client
        )

        def work(status, machine=None, **extra):
            return WorkOrder.objects.create(
                title=f'{status} job',
                machine=machine or self.machine,
                priority=extra.pop('priority', 'medium'),
                lifecycle_status=status,
                **extra,
            )

        self.open_jobs = [
            work('planned', priority='low'),
            work(
                'in_progress',
                priority='high',
                affected_component='Drive-end bearing',
                affected_component_ref='PS-MOTOR-DE-BRG:1',
            ),
            work('on_hold'),
            work('verifying'),
        ]
        # Not open, not active, not this machine, or outside the boundary.
        work('draft')
        completed = work('completed', actual_completed_at=timezone.now())
        work('canceled')
        work('ready', is_active=False)
        work('ready', machine=self.sibling)
        self.open_jobs.append(
            work(
                'ready',
                customer=Company.objects.create(
                    name='Other customer', is_customer=True
                ),
            )
        )
        AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=date(2026, 9, 1),
            summary='Bearing replaced',
            work_order=completed,
        )
        AssetMaintenanceRecord.objects.create(
            machine=self.machine, date=date(2025, 3, 2), summary='Legacy service'
        )
        AssetMaintenanceRecord.objects.create(
            machine=self.sibling, date=date(2026, 1, 1), summary='Sibling service'
        )
        self.client.force_login(self.reader)

    def summary(self, machine=None, expected=200):
        """The work-summary read for one machine."""
        response = self.client.get(
            f'/api/assets/machines/{(machine or self.machine).pk}/work-summary/'
        )
        self.assertEqual(response.status_code, expected, response.content)
        return response.json() if expected == 200 else None

    def open_list(self, **params):
        """The native paginated Work list, as the Open work view reads it."""
        response = self.client.get(
            '/api/kanban/work-orders/list/',
            {'machine': self.machine.pk, 'lifecycle_group': 'open', **params},
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_open_count_agrees_with_the_list_and_the_machines_list(self):
        """One open set, one scope: three surfaces report the same number."""
        summary = self.summary()
        self.assertEqual(summary['rule'], MACHINE_WORK_VIEWS_RULE)
        self.assertEqual(summary['open'], {'count': 5, 'highest_priority': 'high'})
        self.assertEqual(
            summary['open_statuses'],
            ['planned', 'ready', 'in_progress', 'on_hold', 'verifying'],
        )
        listed = self.open_list()
        self.assertEqual(listed['count'], summary['open']['count'])
        self.assertEqual(
            {row['id'] for row in listed['results']}, {job.pk for job in self.open_jobs}
        )
        machines = self.client.get(
            '/api/assets/locations/machines/', {'search': 'Views pump'}
        ).json()['results']
        [row] = [row for row in machines if row['pk'] == self.machine.pk]
        self.assertEqual(row['open_work'], summary['open'])

    def test_completed_count_is_the_native_records_count(self):
        """Completed history counts exactly what its native table pages."""
        summary = self.summary()
        records = self.client.get(
            '/api/assets/maintenance/', {'machine': self.machine.pk, 'limit': 1}
        ).json()
        self.assertEqual(summary['completed']['count'], 2)
        self.assertEqual(records['count'], summary['completed']['count'])

    def test_open_rows_carry_the_recorded_component(self):
        """Component is the work order's own recorded component, read-only."""
        rows = {row['id']: row for row in self.open_list()['results']}
        bearing = rows[self.open_jobs[1].pk]
        self.assertEqual(bearing['affected_component'], 'Drive-end bearing')
        self.assertEqual(bearing['affected_component_ref'], 'PS-MOTOR-DE-BRG:1')
        self.assertEqual(rows[self.open_jobs[0].pk]['affected_component'], '')

    def test_completed_record_carries_its_work_component(self):
        """The native history row names the linked job's component."""
        job = WorkOrder.objects.get(lifecycle_status='completed')
        job.affected_component = 'Impeller'
        job.save()
        rows = self.client.get(
            '/api/assets/maintenance/', {'machine': self.machine.pk, 'limit': 25}
        ).json()['results']
        components = {row['summary']: row['work_order_component'] for row in rows}
        self.assertEqual(components['Bearing replaced'], 'Impeller')
        self.assertIsNone(components['Legacy service'])

    def test_lifecycle_group_is_strict(self):
        """Only ``open`` is a lifecycle group; anything else is refused."""
        for value in ('completed', 'OPEN', 'true'):
            response = self.client.get(
                '/api/kanban/work-orders/list/', {'lifecycle_group': value}
            )
            self.assertEqual(response.status_code, 400, value)

    def test_board_array_is_unchanged(self):
        """The Board's unpaginated array keeps its exact shape."""
        rows = self.client.get('/api/kanban/work-orders/').json()
        self.assertIsInstance(rows, list)
        self.assertNotIn('affected_component', rows[0])

    @override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: set())
    def test_unresolved_work_scope_is_not_found(self):
        """The real endpoint denies unresolved scope before reading counts."""
        self.summary(expected=404)

    def test_clientless_legacy_equipment_summary_is_not_found(self):
        """Registry visibility does not grant a missing maintenance boundary."""
        legacy = AssetMachine.objects.create(name='Legacy equipment')
        self.summary(legacy, expected=404)

    def test_scope_lost_after_machine_check_is_defensively_unknown(self):
        """A resolver failure after the machine gate must never become zero."""
        with mock.patch(
            'assets.location_api.work_order_scope_filter',
            side_effect=ScopeError('unavailable'),
        ):
            summary = self.summary()
        self.assertIsNone(summary['open'])
        self.assertEqual(summary['completed']['count'], 2)

    def test_machine_outside_the_readers_scope_is_not_found(self):
        """Another client's machine is indistinguishable from a missing one."""
        self.summary(self.foreign, expected=404)
        response = self.client.get('/api/assets/machines/999999/work-summary/')
        self.assertEqual(response.status_code, 404)

    def test_summary_is_read_only_and_not_cached(self):
        """Counts are live and the endpoint offers no writes."""
        response = self.client.get(
            f'/api/assets/machines/{self.machine.pk}/work-summary/'
        )
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        posted = self.client.post(
            f'/api/assets/machines/{self.machine.pk}/work-summary/', {}
        )
        self.assertIn(posted.status_code, (403, 405))


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class FaultOccurrenceTests(TestCase):
    """Existing anomalies with their retained evidence and owning work."""

    def setUp(self):
        """A machine with a resolved, an active and an unowned occurrence."""
        self.reader = reader('occurrences')
        self.tenant = Client.objects.create(name='Fault client', code='faults')
        ClientScopeGrant.objects.create(user=self.reader, client=self.tenant)
        self.station = register_station(
            client=self.tenant,
            name='Fault station',
            source_namespace='fault-source',
            source_entity_uuid=uuid4(),
            source_key='PH_7',
        )
        self.machine = ensure_pump(self.station, 'P1')
        self.source = HealthSource.objects.create(
            name='Fault SCADA',
            source_type=SourceType.SCADA,
            connector_type='webhook',
            secret_ref='health/faults',
        )
        component = AssetComponent.objects.create(
            machine=self.machine,
            part=Part.objects.create(name='DE bearing', IPN='PS-FAULT-BRG'),
            code='PS-FAULT-BRG:1',
            name='Drive-end bearing',
        )
        point = DictionaryPoint.objects.create(
            station=self.station,
            machine=self.machine,
            component=component,
            path='/dex/PUMP1_DE_BRG_TEMP',
            raw_tag='PUMP1_DE_BRG_TEMP',
            display_name='PUMP1_DE_BRG_TEMP',
            status='approved',
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            external_key='/dex/PUMP1_DE_BRG_TEMP',
            display_name='DE bearing temperature',
            unit='degC',
            warn_max=90,
            critical_max=110,
            dictionary_point=point,
        )
        self.now = timezone.now().replace(microsecond=0)
        self.owning = WorkOrder.objects.create(
            title='Replace DE bearing',
            machine=self.machine,
            priority='high',
            lifecycle_status='completed',
            affected_component='Drive-end bearing',
        )
        self.hidden = WorkOrder.objects.create(
            title='Customer job',
            machine=self.machine,
            priority='high',
            lifecycle_status='in_progress',
            customer=Company.objects.create(name='Hidden customer', is_customer=True),
        )

        def anomaly(title, hours_ago, status, **extra):
            item = MachineAnomaly.objects.create(
                machine=self.machine,
                source=self.source,
                fingerprint=f'fp-{title}',
                title=title,
                severity=AnomalySeverity.CRITICAL,
                status=status,
                first_observed_at=self.now - timedelta(hours=hours_ago),
                last_observed_at=self.now - timedelta(hours=hours_ago - 1),
                **extra,
            )
            item.bindings.add(self.binding)
            return item

        self.resolved = anomaly(
            'DE bearing high temperature',
            48,
            AnomalyStatus.RESOLVED,
            external_id='ALM-77812',
            alarm_code='TT-HI',
            work_order=self.owning,
            resolved_at=self.now - timedelta(hours=40),
            resolution_note='Reading back inside its limits.',
        )
        self.active = anomaly(
            'DE bearing high temperature again',
            3,
            AnomalyStatus.OPEN,
            work_order=self.hidden,
        )
        self.unowned = anomaly('Spare alarm', 10, AnomalyStatus.ACKNOWLEDGED)
        self.window_start = self.now - timedelta(hours=48)
        self.snapshot = HealthEvidenceSnapshot.objects.create(
            machine=self.machine,
            anomaly=self.resolved,
            source=self.source,
            binding=self.binding,
            signal_label='DE bearing temperature',
            unit='degC',
            window_start=self.window_start,
            window_end=self.window_start,
            samples=[{'observed_at': self.window_start.isoformat(), 'value': 118.4}],
            statistics={'available': True, 'latest': 118.4},
            quality=SignalQuality.GOOD,
            stale=False,
            reason=SnapshotReason.ANOMALY_REPAIR,
            content_hash='a' * 64,
        )
        self.client.force_login(self.reader)

    def url(self, suffix='', machine=None):
        """An occurrences URL for one machine."""
        pk = (machine or self.machine).pk
        return f'/api/assets/machines/{pk}/fault-history/occurrences/{suffix}'

    def read(self, suffix='', expected=200, **params):
        """GET and decode."""
        response = self.client.get(self.url(suffix), params)
        self.assertEqual(response.status_code, expected, response.content)
        return response.json()

    def test_occurrences_are_the_existing_anomalies_newest_first(self):
        """Ids are the anomalies' own; the source's alarm id and code are kept."""
        page = self.read()
        self.assertEqual(page['rule'], FAULT_OCCURRENCES_RULE)
        self.assertEqual(page['count'], 3)
        self.assertEqual(
            [row['id'] for row in page['results']],
            [self.active.pk, self.unowned.pk, self.resolved.pk],
        )
        resolved = page['results'][-1]
        self.assertEqual(resolved['external_id'], 'ALM-77812')
        self.assertEqual(resolved['alarm_code'], 'TT-HI')
        self.assertEqual(resolved['status'], 'resolved')
        self.assertFalse(resolved['active'])
        self.assertEqual(resolved['resolution_note'], 'Reading back inside its limits.')
        self.assertEqual(
            resolved['source'],
            {'id': self.source.pk, 'name': 'Fault SCADA', 'type': 'scada'},
        )
        # No invented identifier: an anomaly without one keeps it empty.
        self.assertEqual(page['results'][0]['external_id'], '')

    def test_components_come_from_the_signals_source_points(self):
        """The occurrence names the slot its signal belongs to, with its status."""
        row = self.read()['results'][-1]
        self.assertEqual(
            row['components'],
            [
                {
                    'code': 'PS-FAULT-BRG:1',
                    'name': 'Drive-end bearing',
                    'status': 'draft',
                }
            ],
        )
        self.assertEqual(row['signals'][0]['binding_id'], self.binding.pk)

    def test_owning_work_is_shown_only_inside_the_work_scope(self):
        """Shared customer Work is linked through the authorized machine endpoint."""
        rows = {row['id']: row for row in self.read()['results']}
        [owning] = rows[self.resolved.pk]['work']
        self.assertEqual(owning['id'], self.owning.pk)
        self.assertEqual(owning['relation'], 'owning')
        self.assertEqual(owning['lifecycle_status'], 'completed')
        self.assertEqual(
            [work['id'] for work in rows[self.active.pk]['work']], [self.hidden.pk]
        )
        self.assertNotIn('work_order', rows[self.active.pk])
        self.assertIsNone(rows[self.active.pk]['repair_packet'])
        self.assertEqual(rows[self.unowned.pk]['work'], [])

    def test_a_packet_with_its_own_work_order_is_related_work(self):
        """The model allows two jobs per occurrence; both are listed."""
        packet_work = WorkOrder.objects.create(
            title='Packet job',
            machine=self.machine,
            priority='medium',
            lifecycle_status='planned',
        )
        packet = RepairPacket.objects.create(
            machine=self.machine, work_order=packet_work, symptom='Hot bearing'
        )
        self.resolved.repair_packet = packet
        self.resolved.save()
        row = {row['id']: row for row in self.read()['results']}[self.resolved.pk]
        self.assertEqual(
            [(job['id'], job['relation']) for job in row['work']],
            [(self.owning.pk, 'owning'), (packet_work.pk, 'repair_packet')],
        )
        self.assertEqual(row['repair_packet']['id'], packet.pk)

    def test_customer_work_never_reveals_a_packet_with_a_different_machine(self):
        """O24 keeps P32 packet/Work machine consistency for occurrence links."""
        other = AssetMachine.objects.create(
            name='Different packet machine', client=self.tenant
        )
        self.hidden.machine = other
        self.hidden.save(update_fields=['machine'])
        packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.hidden,
            symptom='Invalid linked machine',
        )
        self.resolved.repair_packet = packet
        self.resolved.save()
        for suffix in ('', f'{self.resolved.pk}/'):
            with self.subTest(suffix=suffix):
                result = self.read(suffix)
                row = (
                    result
                    if suffix
                    else next(
                        item
                        for item in result['results']
                        if item['id'] == self.resolved.pk
                    )
                )
                self.assertIsNone(row['repair_packet'])
                self.assertEqual([work['id'] for work in row['work']], [self.owning.pk])

    def test_list_rows_cite_snapshots_without_their_samples(self):
        """Rows carry evidence references; samples come with the detail."""
        row = {row['id']: row for row in self.read()['results']}[self.resolved.pk]
        self.assertEqual(row['snapshot_count'], 1)
        [ref] = row['snapshots']
        self.assertEqual(ref['id'], str(self.snapshot.id))
        self.assertEqual(ref['sample_count'], 1)
        self.assertNotIn('samples', ref)
        self.assertEqual(ref['content_hash'], 'a' * 64)

    def test_current_readings_never_replace_the_retained_window(self):
        """A normal reading today leaves the fault-time evidence as captured."""
        MachineSignalState.objects.create(
            binding=self.binding,
            value={'value': 61.0, 'unit': 'degC'},
            observed_at=self.now,
            received_at=self.now,
            quality=SignalQuality.GOOD,
        )
        detail = self.read(f'{self.resolved.pk}/')
        [snapshot] = detail['snapshots']
        self.assertEqual(snapshot['samples'][0]['value'], 118.4)
        self.assertEqual(snapshot['window_start'], self.window_start.isoformat())
        self.assertEqual(snapshot['statistics'], {'available': True, 'latest': 118.4})
        self.assertNotIn('61.0', str(detail))
        self.assertFalse(detail['display_shifted'])

    def test_status_filter_and_paging(self):
        """Active and resolved views; pages are bounded and strict."""
        self.assertEqual(
            [row['id'] for row in self.read(status='active')['results']],
            [self.active.pk, self.unowned.pk],
        )
        self.assertEqual(
            [row['id'] for row in self.read(status='resolved')['results']],
            [self.resolved.pk],
        )
        page = self.read(limit=1, offset=1)
        self.assertEqual(page['count'], 3)
        self.assertEqual([row['id'] for row in page['results']], [self.unowned.pk])
        self.assertEqual(self.read(limit=500)['limit'], 100)
        for params in (
            {'status': 'open'},
            {'limit': 0},
            {'offset': -1},
            {'limit': 'x'},
        ):
            self.read(expected=400, **params)

    def test_recorded_evidence_times_do_not_follow_the_dashboard_clock(self):
        """A migrated source's moving display clock cannot alter evidence."""
        self.source.config = {'data_ranges': {
            str(self.station.source_entity_uuid): {
                'from': (self.now - timedelta(days=450)).isoformat(),
                'to': (self.now - timedelta(days=442)).isoformat(),
            }
        }}
        self.source.save()
        for now in (self.now, self.now + timedelta(days=1)):
            with mock.patch('django.utils.timezone.now', return_value=now):
                detail = self.read(f'{self.resolved.pk}/')
                listed = self.read(status='resolved')['results'][0]
            for row in (detail, listed):
                self.assertEqual(row['first_observed_at'], self.resolved.first_observed_at.isoformat())
                self.assertEqual(row['last_observed_at'], self.resolved.last_observed_at.isoformat())
                self.assertFalse(row['display_shifted'])
                snapshot = row['snapshots'][0]
                self.assertEqual(snapshot['window_start'], self.snapshot.window_start.isoformat())
                self.assertEqual(snapshot['window_end'], self.snapshot.window_end.isoformat())
                self.assertEqual(snapshot['captured_at'], self.snapshot.captured_at.isoformat())
                self.assertEqual(snapshot['content_hash'], self.snapshot.content_hash)
                self.assertFalse(snapshot['display_shifted'])
            self.assertEqual(detail['snapshots'][0]['samples'], self.snapshot.samples)

    def test_snapshot_retains_human_and_system_recorder(self):
        """Names follow Work's full-name / username convention; no guess."""
        self.reader.first_name = 'Recorded'
        self.reader.last_name = 'Technician'
        self.reader.save()
        retained = HealthEvidenceSnapshot.objects.create(
            machine=self.machine, anomaly=self.resolved, source=self.source,
            created_by=self.reader, system_actor='historian-import',
            signal_label='Retained attribution', window_start=self.now,
            window_end=self.now, content_hash='r' * 64,
        )
        def captured(snapshot_id):
            return next(row for row in self.read(f'{self.resolved.pk}/')['snapshots'] if row['id'] == str(snapshot_id))
        snapshot = captured(retained.pk)
        self.assertEqual(snapshot['captured_by_name'], 'Recorded Technician')
        self.assertEqual(snapshot['system_actor'], 'historian-import')
        self.reader.first_name = self.reader.last_name = ''
        self.reader.save()
        self.assertEqual(captured(retained.pk)['captured_by_name'], self.reader.username)
        self.assertIsNone(captured(self.snapshot.pk)['captured_by_name'])

    def test_occurrence_queries_stay_flat_with_more_work_and_snapshots(self):
        """Scope grants, work machines and snapshot recorders load in batches."""
        for index in range(7):
            owning = WorkOrder.objects.create(title=f'Owner {index}', machine=self.machine)
            related = WorkOrder.objects.create(title=f'Packet job {index}', machine=self.machine)
            packet = RepairPacket.objects.create(machine=self.machine, work_order=related, symptom='Retained alarm')
            anomaly = MachineAnomaly.objects.create(
                machine=self.machine, source=self.source, fingerprint=f'scale-{index}',
                title=f'Scale alarm {index}', first_observed_at=self.now,
                last_observed_at=self.now, work_order=owning, repair_packet=packet,
            )
            anomaly.bindings.add(self.binding)
            HealthEvidenceSnapshot.objects.create(
                machine=self.machine, anomaly=anomaly, source=self.source,
                created_by=self.reader, signal_label='Retained value',
                window_start=self.now, window_end=self.now,
                content_hash=str(index) * 64,
            )
        with CaptureQueriesContext(connection) as small:
            fault_occurrences(self.reader, self.machine, limit=1)
        with self.assertNumQueries(len(small)):
            page = fault_occurrences(self.reader, self.machine, limit=25)
        self.assertEqual(len(page['results']), 10)

    def test_detail_belongs_to_its_machine(self):
        """Another machine's anomaly id is not found through this machine."""
        other = AssetMachine.objects.create(name='Other pump', client=self.tenant)
        foreign = MachineAnomaly.objects.create(
            machine=other,
            fingerprint='fp-other',
            title='Other alarm',
            first_observed_at=self.now,
            last_observed_at=self.now,
        )
        self.read(f'{foreign.pk}/', expected=404)

    def test_machine_outside_scope_is_not_found(self):
        """Another client's machine reads as missing for list and detail."""
        foreign = AssetMachine.objects.create(
            name='Foreign pump',
            client=Client.objects.create(name='Foreign', code='foreign'),
        )
        response = self.client.get(self.url(machine=foreign))
        self.assertEqual(response.status_code, 404)
