"""R6 P07: each reference-model position shows only its machine's own records."""

from datetime import timedelta
from io import StringIO
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.models import WorkOrder
from tasks.scope import ScopeError

from assets.equipment_positions import POSITION_FAMILIES, REFERENCE_POSITIONS_RULE
from assets.health_models import (
    HealthSource,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
    SourceType,
)
from assets.models import AssetMachine, Client, ClientScopeGrant
from assets.registry import ensure_pump, register_station
from assets.registry_models import AssetComponent, DictionaryPoint
from part.models import Part
from users.models import RuleSet


def url(pk):
    """The positions read for one machine."""
    return f'/api/assets/machines/{pk}/positions/'


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class EquipmentPositionsTests(TestCase):
    """Real catalogue, registered station and pump, bound signals and work."""

    @classmethod
    def setUpTestData(cls):
        """The pump catalogue as the loader writes it."""
        super().setUpTestData()
        call_command('load_pump_catalogue', stdout=StringIO())

    def setUp(self):
        """A scoped reader, a station, two pumps and their component slots."""
        self.reader = get_user_model().objects.create_user(username='positions')
        group = Group.objects.create(name='Position readers')
        self.reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(name='Positions', code='positions')
        ClientScopeGrant.objects.create(user=self.reader, client=self.tenant)
        self.station = register_station(
            client=self.tenant,
            name='Positions station',
            source_namespace='positions-source',
            source_entity_uuid=uuid4(),
            source_key='PH_9',
        )
        self.pump = ensure_pump(self.station, 'P1')
        self.other_pump = ensure_pump(self.station, 'P2')

        def slot(machine, ipn, code):
            return AssetComponent.objects.create(
                machine=machine,
                part=Part.objects.get(IPN=ipn),
                code=code,
                name=Part.objects.get(IPN=ipn).name,
                provenance='Inferred from source tags; physical assignment is unverified.',
            )

        self.bearing = slot(self.pump, 'PS-MOTOR-DE-BRG', 'PS-MOTOR-DE-BRG:1')
        self.motor = slot(self.pump, 'PS-MOTOR', 'PS-MOTOR:1')
        self.motor.status = 'verified'
        self.motor.save()
        # A family no position stands for, and another pump's bearing.
        slot(self.pump, 'PS-THRUST-BRG', 'PS-THRUST-BRG:1')
        self.other_bearing = slot(
            self.other_pump, 'PS-MOTOR-DE-BRG', 'PS-MOTOR-DE-BRG:1'
        )

        self.source = HealthSource.objects.create(
            name='Positions SCADA',
            source_type=SourceType.SCADA,
            connector_type='webhook',
            secret_ref='health/positions',
            freshness_threshold_seconds=900,
        )

        def signal(machine, component, tag, value):
            point = DictionaryPoint.objects.create(
                station=self.station,
                machine=machine,
                component=component,
                path=f'/dex/{tag}',
                raw_tag=tag,
                display_name=tag,
                status='approved',
            )
            binding = MachineSignalBinding.objects.create(
                machine=machine,
                source=self.source,
                external_key=f'/dex/{tag}',
                display_name=tag.replace('_', ' ').title(),
                signal_kind='temperature',
                unit='degC',
                warn_max=90,
                critical_max=110,
                dictionary_point=point,
            )
            observed = timezone.now() - timedelta(seconds=30)
            MachineSignalState.objects.create(
                binding=binding,
                value={'value': value, 'unit': 'degC'},
                observed_at=observed,
                received_at=observed,
                quality=SignalQuality.GOOD,
            )
            return binding

        self.bearing_signal = signal(self.pump, self.bearing, 'PUMP1_DE_BRG_TEMP', 95)
        signal(self.pump, self.motor, 'PUMP1_WINDING_TEMP', 70)
        signal(self.other_pump, self.other_bearing, 'PUMP2_DE_BRG_TEMP', 50)
        # A binding with no dictionary point belongs to no position.
        MachineSignalBinding.objects.create(
            machine=self.pump,
            source=self.source,
            external_key='/dex/PUMP1_LOOSE',
            display_name='Loose',
            unit='bar',
        )

        def work(machine, title, ref='', **extra):
            return WorkOrder.objects.create(
                title=title,
                machine=machine,
                priority=extra.pop('priority', 'medium'),
                lifecycle_status=extra.pop('lifecycle_status', 'planned'),
                affected_component_ref=ref,
                **extra,
            )

        self.bearing_job = work(
            self.pump, 'Replace DE bearing', 'PS-MOTOR-DE-BRG:1', priority='high'
        )
        work(self.pump, 'General inspection')
        work(
            self.pump,
            'Closed bearing job',
            'PS-MOTOR-DE-BRG:1',
            lifecycle_status='completed',
        )
        work(self.pump, 'Archived bearing job', 'PS-MOTOR-DE-BRG:1', is_active=False)
        work(self.other_pump, 'Other pump bearing', 'PS-MOTOR-DE-BRG:1')
        self.client.force_login(self.reader)

    def read(self, pk=None):
        """The positions as the Equipment view reads them."""
        response = self.client.get(url(pk or self.pump.pk))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        body = response.json()
        return body, {p['id']: p for p in body['positions']}

    def test_every_reference_position_is_listed_by_its_rule(self):
        """Nine positions in model order; four have no family at all."""
        body, positions = self.read()
        self.assertEqual(body['rule'], REFERENCE_POSITIONS_RULE)
        self.assertEqual(list(positions), list(POSITION_FAMILIES))
        for unbound in ('coupling', 'seal', 'impeller', 'base', 'machine'):
            with self.subTest(position=unbound):
                self.assertIsNone(positions[unbound]['family'])
                self.assertEqual(positions[unbound]['components'], [])
                self.assertEqual(positions[unbound]['signals'], [])
                self.assertIsNone(positions[unbound]['work'])

    def test_a_position_finds_its_slot_through_the_catalogue_family(self):
        """The bearing is this pump's MOTOR-DE-BRG slot, with its status."""
        _, positions = self.read()
        bearing = positions['bearing']
        self.assertEqual(bearing['family'], 'MOTOR-DE-BRG')
        self.assertEqual(bearing['family_name'], 'Motor Drive-End Bearing')
        self.assertEqual(
            [(c['code'], c['status']) for c in bearing['components']],
            [('PS-MOTOR-DE-BRG:1', 'draft')],
        )
        self.assertEqual(bearing['components'][0]['part']['IPN'], 'PS-MOTOR-DE-BRG')
        self.assertEqual(
            [(c['code'], c['status']) for c in positions['motor']['components']],
            [('PS-MOTOR:1', 'verified')],
        )
        # A family this pump has no slot for reports none, not a neighbour's.
        self.assertEqual(positions['pump']['family'], 'CASING')
        self.assertEqual(positions['pump']['components'], [])
        self.assertIsNone(positions['pump']['work'])

    def test_signals_come_only_from_the_slot_and_match_the_health_tab(self):
        """The bearing reading is its own; the motor's and the loose tag stay out."""
        from machine_health.services.summary import signal_rows

        _, positions = self.read()
        bearing_signals = positions['bearing']['signals']
        self.assertEqual(
            [s['binding_id'] for s in bearing_signals], [self.bearing_signal.pk]
        )
        health = {row['binding_id']: row for row in signal_rows(self.pump)}[
            self.bearing_signal.pk
        ]
        self.assertEqual(bearing_signals[0]['state'], health['state'])
        self.assertEqual(bearing_signals[0]['state'], 'warning')
        self.assertEqual(bearing_signals[0]['value'], 95)
        self.assertEqual(
            [s['display_name'] for s in positions['motor']['signals']],
            ['Pump1 Winding Temp'],
        )

    def test_work_is_attributed_by_slot_code_and_the_rest_is_parent_context(self):
        """Only open, active, in-scope work on this pump's slot is the bearing's."""
        body, positions = self.read()
        bearing_work = positions['bearing']['work']
        self.assertEqual(bearing_work['count'], 1)
        self.assertEqual(
            [w['title'] for w in bearing_work['results']], ['Replace DE bearing']
        )
        self.assertEqual(bearing_work['results'][0]['priority'], 'high')
        self.assertEqual(positions['motor']['work'], {'count': 0, 'results': []})
        # Both open jobs on the pump are its machine context.
        self.assertEqual(body['machine_work'], {'count': 2})

    def test_unresolved_work_scope_is_unknown_not_zero(self):
        """Without a Work scope the work is unknown; slots and signals remain."""
        with mock.patch(
            'assets.equipment_positions.work_order_scope_filter',
            side_effect=ScopeError('unavailable'),
        ):
            body, positions = self.read()
        self.assertIsNone(body['machine_work'])
        self.assertIsNone(positions['bearing']['work'])
        self.assertEqual(len(positions['bearing']['components']), 1)
        self.assertEqual(len(positions['bearing']['signals']), 1)

    def test_out_of_scope_machines_read_as_missing_and_writes_are_refused(self):
        """Another client's pump is a 404; nothing can be written here."""
        other_client = Client.objects.create(name='Elsewhere', code='elsewhere')
        elsewhere = AssetMachine.objects.create(name='Elsewhere', client=other_client)
        self.assertEqual(self.client.get(url(elsewhere.pk)).status_code, 404)
        self.assertEqual(self.client.get(url(999999)).status_code, 404)
        # Even a full Work editor cannot write through this read.
        editor = get_user_model().objects.create_user(username='positions-editor')
        editors = Group.objects.create(name='Position editors')
        RuleSet.objects.update_or_create(
            group=editors,
            name='work_order',
            defaults={
                'can_view': True,
                'can_add': True,
                'can_change': True,
                'can_delete': True,
            },
        )
        editor.groups.add(editors)
        ClientScopeGrant.objects.create(user=editor, client=self.tenant)
        self.client.force_login(editor)
        for method in ('post', 'put', 'patch', 'delete'):
            with self.subTest(method=method):
                self.assertEqual(
                    getattr(self.client, method)(url(self.pump.pk)).status_code, 405
                )
        outsider = get_user_model().objects.create_user(username='no-role')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(url(self.pump.pk)).status_code, 403)

    def test_a_duplicate_ipn_neither_hides_a_slot_nor_renames_its_family(self):
        """IPNs are not unique: a user Part sharing PS-MOTOR changes nothing."""
        Part.objects.create(name='Someone else motor', IPN='PS-MOTOR')
        _, positions = self.read()
        motor = positions['motor']
        self.assertEqual([c['code'] for c in motor['components']], ['PS-MOTOR:1'])
        self.assertEqual(motor['family_name'], 'Electric Motor')
        self.assertEqual(len(motor['signals']), 1)

    def test_work_outside_the_readers_boundary_is_not_attributed(self):
        """A customer-owned job on the bearing slot is not the reader's to see."""
        from company.models import Company

        WorkOrder.objects.create(
            title='Customer bearing job',
            machine=self.pump,
            priority='high',
            lifecycle_status='planned',
            affected_component_ref='PS-MOTOR-DE-BRG:1',
            customer=Company.objects.create(name='Elsewhere co', is_customer=True),
        )
        body, positions = self.read()
        self.assertEqual(
            [w['title'] for w in positions['bearing']['work']['results']],
            ['Replace DE bearing'],
        )
        self.assertEqual(body['machine_work'], {'count': 2})
