"""Mimics enforce station scope, reviewed values and complete aggregate inputs."""

from datetime import timedelta
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone

from assets.activation import point_hash
from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.ingestion_models import IngestionCheckpoint
from assets.models import Client, DictionaryPoint
from assets.registry import ensure_pump, register_station
from assets.test_registry import test_scope
from InvenTree.unit_test import InvenTreeAPITestCase
from machine_health.mimic_layout import layout_coverage
from machine_health.services.mimic import station_mimic


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=test_scope, AIMMS_COSMOS_PUMPHOUSE_ENABLED=True
)
class MimicTests(InvenTreeAPITestCase):
    """Exercise the public endpoint and real database projection."""

    is_staff = False
    roles = ['work_order.view']

    @classmethod
    def setUpTestData(cls):
        """Use sparse registered bay keys and a same-Client source."""
        super().setUpTestData()
        cls.tenant = Client.objects.create(code='registry-test', name='Mimic client')
        cls.station = register_station(
            client=cls.tenant,
            name='Station',
            source_namespace='mimic',
            source_entity_uuid=uuid4(),
            source_key='PH_TEST',
        )
        cls.pump = ensure_pump(cls.station, 'P1')
        cls.other_pump = ensure_pump(cls.station, 'P17')
        cls.source = HealthSource.objects.create(
            name='Mimic source',
            client=cls.tenant,
            source_type='scada',
            connector_type='cosmos_pumphouse',
            config={'stations': [str(cls.station.source_entity_uuid)]},
        )
        IngestionCheckpoint.objects.create(
            source=cls.source,
            station=cls.station,
            station_uuid=str(cls.station.source_entity_uuid),
            hour_bucket='0',
            sub_time_period=0,
        )

    def add_point(self, path, value, *, machine=None, unit='MW', age=0, quality='good'):
        """Build reviewed state directly to isolate projection from catalogue setup."""
        point = DictionaryPoint.objects.create(
            station=self.station,
            machine=machine or self.pump,
            path=path,
            raw_tag=path,
            display_name=path,
            data_type='number' if not isinstance(value, str) else 'status',
            unit=unit,
            unit_status='verified' if unit else 'unitless',
            status='approved',
            review_note='Fixture',
        )
        binding = MachineSignalBinding.objects.create(
            machine=point.machine,
            source=self.source,
            dictionary_point=point,
            dictionary_hash=point_hash(point),
            external_key=path,
            display_name=path,
            unit=unit,
        )
        MachineSignalState.objects.create(
            binding=binding,
            value={'value': value},
            observed_at=timezone.now() - timedelta(seconds=age),
            quality=quality,
        )
        return point, binding

    def get_mimic(self, **params):
        """Read the scoped API as a view-only operator."""
        return self.client.get(
            f'/api/machine-health/station/{self.station.pk}/mimic/', params
        )

    def test_empty_station_is_unknown_and_sparse_keys_are_preserved(self):
        """Absent live mappings still return a renderable diagram, never fake zero."""
        response = self.get_mimic(unit='P17')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([bay['key'] for bay in response.data['bays']], ['P1', 'P17'])
        self.assertIsNone(response.data['points']['/pd/P17/st']['value'])
        self.assertEqual(response.data['points']['/pd/P17/st']['reason'], 'not_bound')
        self.assertIsNone(response.data['totals']['power']['value'])
        self.assertEqual(self.get_mimic(unit='P2').status_code, 400)

    def test_selected_values_alarms_and_unconfigured_thresholds(self):
        """Only configured limits produce alarms; no limits means unknown health."""
        _, binding = self.add_point('/dex/CORE1', 45, unit='degC')
        self.add_point('/pd/P1/st', 'R', unit='')
        self.add_point('/dex/CORE17', 30, machine=self.other_pump, unit='degC')
        response = self.get_mimic(unit='P1').data
        self.assertEqual(response['bays'][0]['state'], 'running')
        self.assertEqual(response['points']['/dex/CORE1']['value'], 45)
        self.assertNotIn('/dex/CORE17', response['points'])
        self.assertEqual(response['alarms'], [])
        self.assertEqual(response['points']['/dex/CORE1']['condition'], 'unknown')
        binding.critical_max = 40
        binding.save()
        self.assertEqual(
            self.get_mimic(unit='P1').data['alarms'][0]['condition'], 'critical'
        )

    def test_stale_bad_and_future_readings_are_null(self):
        """Server time and source quality control what may be presented as current."""
        self.add_point('/old', 9, age=301)
        self.add_point('/bad', 9, quality='bad')
        self.add_point('/future', 9, age=-10)
        points = self.get_mimic(unit='P1').data['points']
        for pointer, reason in [
            ('/old', 'stale'),
            ('/bad', 'bad_quality'),
            ('/future', 'clock_skew'),
        ]:
            self.assertIsNone(points[pointer]['value'])
            self.assertEqual(points[pointer]['reason'], reason)
        self.assertGreater(points['/old']['age_seconds'], 300)

    @override_settings(AIMMS_COSMOS_PUMPHOUSE_ENABLED=False)
    def test_kill_switch_removes_values_and_alarms(self):
        """Turning off polling is represented in every output value."""
        self.add_point('/pd/P1/pmw', 10)
        result = self.get_mimic(unit='P1').data
        self.assertFalse(result['enabled'])
        self.assertIsNone(result['points']['/pd/P1/pmw']['value'])
        self.assertEqual(result['points']['/pd/P1/pmw']['reason'], 'disabled')
        self.assertEqual(result['alarms'], [])

    def test_totals_require_every_bay_and_convert_reviewed_units(self):
        """Incomplete bay data cannot silently become a low plant total."""
        self.add_point('/pd/P1/pmw', 1)
        self.assertIsNone(self.get_mimic().data['totals']['power']['value'])
        _, binding = self.add_point(
            '/pd/P17/pmw', 2000, machine=self.other_pump, unit='kW'
        )
        total = self.get_mimic().data['totals']['power']
        self.assertEqual(total['value'], 3)
        self.assertTrue(total['derived'])
        MachineSignalState.objects.filter(binding=binding).update(quality='bad')
        self.assertIsNone(self.get_mimic().data['totals']['power']['value'])
        self.add_point('/pmw', 7, machine=self.station)
        total = self.get_mimic().data['totals']['power']
        self.assertFalse(total['derived'])
        self.assertEqual(total['value'], 7)

    def test_revocation_and_client_reassignment_hide_values(self):
        """Cached readings cannot outlive review authority or source ownership."""
        point, _ = self.add_point('/pd/P1/pmw', 10)
        report = layout_coverage(self.station)
        self.assertFalse(report['ready'])
        self.assertTrue(report['missing'])
        point.status = 'rejected'
        point.save()
        self.assertIsNone(self.get_mimic(unit='P1').data['points'][point.path]['value'])
        other = Client.objects.create(code='mimic-other', name='Other')
        self.source.client = other
        self.source.save()
        result = self.get_mimic(unit='P1').data
        self.assertIsNone(result['source'])
        self.assertFalse(result['enabled'])
        self.station = register_station(
            client=other,
            name='Foreign station',
            source_namespace='mimic',
            source_entity_uuid=uuid4(),
            source_key='PRIVATE',
        )
        self.assertEqual(self.get_mimic().status_code, 404)

    def test_projection_has_bounded_database_work(self):
        """Station requests fetch readings in batches, not one query per point."""
        for number in range(20):
            self.add_point(f'/value{number}', number)
        with self.assertNumQueries(8):
            result = station_mimic(self.station, unit='P1')
        self.assertEqual(len(result['points']), 23)
