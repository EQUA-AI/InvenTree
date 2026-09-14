"""Station activation preserves reviewed ownership and isolates source lifecycles."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.ingestion_models import IngestionCheckpoint
from assets.models import Client
from assets.registry import register_station
from assets.tasks import poll_cosmos_pumphouse_sources
from InvenTree.unit_test import InvenTreeAPITestCase
from machine_health.services.ingestion import ingest_readings

from . import test_registry as registry_tests


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER=registry_tests.test_scope,
    AIMMS_COSMOS_PUMPHOUSE_ENABLED=False,
)
class StationActivationTests(InvenTreeAPITestCase):
    """Use real registry URLs, catalogue validation, roles and Client scopes."""

    is_staff = False
    roles = ['work_order.view', 'work_order.add', 'work_order.change']
    raw = registry_tests.RegistryTests.raw
    imported = registry_tests.RegistryTests.imported

    @classmethod
    def setUpTestData(cls):
        """Prepare a reviewed catalogue-backed station, with no telemetry bindings."""
        ContentType.objects.clear_cache()
        super().setUpTestData()
        call_command('load_pump_catalogue', stdout=StringIO())
        cls.tenant = Client.objects.create(code='registry-test', name='Activation test')
        cls.station = register_station(
            client=cls.tenant,
            name='Activation station',
            source_namespace='test',
            source_entity_uuid=uuid4(),
            source_key='PH_3',
        )

    def setUp(self):
        """Approve one test point; remaining draft points must stay unbound."""
        ContentType.objects.clear_cache()
        super().setUp()
        self.addCleanup(ContentType.objects.clear_cache)
        self.station.refresh_from_db()
        self.point = self.imported()
        self.point.status = 'approved'
        self.point.unit = self.point.template.units
        self.point.unit_status = 'verified'
        self.point.review_note = 'Confirmed by test fixture'
        self.point.save()
        self.source = HealthSource.objects.create(
            client=self.tenant,
            name='Activation Cosmos',
            source_type='scada',
            connector_type='cosmos_pumphouse',
            config={
                'stations': [str(self.station.source_entity_uuid)],
                'endpoint': 'https://private.invalid',
            },
            secret_ref='private-credential-reference',
        )
        self.url = f'/api/assets/registry/{self.station.pk}/activate/'

    def preview(self):
        """Obtain the exact preview used by the UI."""
        response = self.client.get(self.url, {'source': self.source.pk})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['preview']

    def activate(self, **extra):
        """Submit an activation with the current mapping hash."""
        preview = self.preview()
        return self.client.post(
            self.url,
            {'source': self.source.pk, 'source_hash': preview['source_hash'], **extra},
            format='json',
        )

    def test_activation_is_idempotent_and_leaves_thresholds_unknown(self):
        """Repeating one reviewed request preserves binding identity and its cursor."""
        preview = self.preview()
        body = {'source': self.source.pk, 'source_hash': preview['source_hash']}
        with patch('azure.cosmos.CosmosClient') as cosmos:
            response = self.client.post(self.url, body, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            binding = MachineSignalBinding.objects.get(dictionary_point=self.point)
            checkpoint = IngestionCheckpoint.objects.get(station=self.station)
            before = checkpoint.position
            response = self.client.post(self.url, body, format='json')
        cosmos.assert_not_called()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['activated'])
        self.assertFalse(response.data['enabled'])
        self.assertEqual(response.data['bound'], 1)
        self.assertGreater(response.data['unbound'], 0)
        self.assertEqual(MachineSignalBinding.objects.count(), 1)
        self.assertEqual(binding.machine_id, self.point.machine_id)
        self.assertEqual(binding.unit, self.point.unit)
        self.assertIsNone(binding.normal_max)
        self.assertIsNone(binding.warn_max)
        self.assertIsNone(binding.critical_max)
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.position, before)
        self.assertEqual(checkpoint.station_uuid, str(self.station.source_entity_uuid))
        self.assertEqual(MachineSignalBinding.objects.get().pk, binding.pk)

    def test_changed_review_rejects_old_preview_without_writes(self):
        """An approval revoked after preview cannot become a live binding."""
        preview = self.preview()
        self.point.status = 'rejected'
        self.point.save()
        response = self.client.post(
            self.url,
            {'source': self.source.pk, 'source_hash': preview['source_hash']},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MachineSignalBinding.objects.exists())
        self.assertFalse(IngestionCheckpoint.objects.exists())

    def test_review_revocation_disables_binding_and_removes_current_state(self):
        """A revoked point cannot keep an old value looking current."""
        self.assertEqual(self.activate().status_code, 200)
        ingest_readings(
            self.source,
            [
                {
                    'external_key': self.point.path,
                    'value': 42,
                    'observed_at': timezone.now(),
                }
            ],
            station=self.station,
        )
        self.assertTrue(MachineSignalState.objects.exists())
        self.point.status = 'draft'
        self.point.save()
        self.assertFalse(
            MachineSignalBinding.objects.get(dictionary_point=self.point).active
        )
        self.assertFalse(MachineSignalState.objects.exists())

    def test_manual_pointer_collision_rolls_back_activation(self):
        """Activation does not silently take over an operator's independent binding."""
        manual = MachineSignalBinding.objects.create(
            machine=self.point.machine,
            source=self.source,
            external_key=self.point.path,
            display_name='Manual',
            warn_max=50,
        )
        response = self.activate()
        self.assertEqual(response.status_code, 400)
        manual.refresh_from_db()
        self.assertIsNone(manual.dictionary_point_id)
        self.assertEqual(manual.warn_max, 50)
        self.assertFalse(IngestionCheckpoint.objects.exists())

    def test_other_client_source_and_station_are_not_disclosed(self):
        """Neither source IDs nor station IDs bypass exact Client scope."""
        other = Client.objects.create(code='activation-other', name='Other')
        self.source.client = other
        self.source.save()
        self.assertEqual(self.client.get(self.url).data['sources'], [])
        self.assertEqual(
            self.client.get(self.url, {'source': self.source.pk}).status_code, 404
        )
        station = register_station(
            client=other,
            name='Private station',
            source_namespace='private',
            source_entity_uuid=uuid4(),
            source_key='PH_2',
        )
        self.assertEqual(
            self.client.get(f'/api/assets/registry/{station.pk}/activate/').status_code,
            404,
        )

    def test_source_config_and_credential_reference_are_never_returned(self):
        """The operator picks a source name, not a connection string."""
        response = self.client.get(self.url, {'source': self.source.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('private.invalid', str(response.data))
        self.assertNotIn('private-credential-reference', str(response.data))
        self.assertNotIn('config', response.data)

    def test_view_only_can_preview_but_cannot_activate_or_deactivate(self):
        """Preview authority does not imply live mutation authority."""
        self.clearRoles()
        self.assignRole('work_order.view')
        preview = self.preview()
        body = {'source': self.source.pk, 'source_hash': preview['source_hash']}
        self.assertEqual(
            self.client.post(self.url, body, format='json').status_code, 403
        )
        self.assertEqual(
            self.client.delete(self.url, body, format='json').status_code, 403
        )

    def test_deactivation_preserves_other_station_and_manual_bindings(self):
        """Stopping a station retains its cursor and affects only managed bindings."""
        self.assertEqual(self.activate().status_code, 200)
        manual = MachineSignalBinding.objects.create(
            machine=self.station,
            source=self.source,
            external_key='/manual',
            display_name='Manual',
        )
        other = register_station(
            client=self.tenant,
            name='Other station',
            source_namespace='test',
            source_entity_uuid=uuid4(),
            source_key='PH_2',
        )
        other_binding = MachineSignalBinding.objects.create(
            machine=other,
            source=self.source,
            external_key=self.point.path,
            display_name='Other',
        )
        checkpoint = IngestionCheckpoint.objects.get(station=self.station)
        before = checkpoint.position
        body = {'source': self.source.pk, 'source_hash': self.preview()['source_hash']}
        response = self.client.delete(self.url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['activated'])
        checkpoint.refresh_from_db()
        self.assertFalse(checkpoint.active)
        self.assertEqual(checkpoint.position, before)
        self.assertEqual(
            set(MachineSignalBinding.objects.values_list('pk', flat=True)),
            {manual.pk, other_binding.pk},
        )
        with (
            self.settings(AIMMS_COSMOS_PUMPHOUSE_ENABLED=True),
            patch('assets.tasks.CosmosPumphouseConnector') as connector,
        ):
            self.assertEqual(poll_cosmos_pumphouse_sources(), 0)
        connector.assert_not_called()
        self.assertEqual(self.activate().status_code, 200)
        checkpoint.refresh_from_db()
        self.assertTrue(checkpoint.active)
        self.assertEqual(checkpoint.position, before)

    def test_poll_lease_prevents_activation_and_deactivation_races(self):
        """An in-flight poll finishes before bindings or checkpoint activity change."""
        self.assertEqual(self.activate().status_code, 200)
        checkpoint = IngestionCheckpoint.objects.get(station=self.station)
        checkpoint.lease_until = timezone.now() + timedelta(seconds=60)
        checkpoint.save()
        body = {'source': self.source.pk, 'source_hash': self.preview()['source_hash']}
        self.assertEqual(
            self.client.post(self.url, body, format='json').status_code, 400
        )
        self.assertEqual(
            self.client.delete(self.url, body, format='json').status_code, 400
        )
        self.assertTrue(
            MachineSignalBinding.objects.get(dictionary_point=self.point).active
        )

    def test_existing_checkpoint_is_linked_without_rewinding(self):
        """T9's previously unlinked cursor can be activated without guessing identity."""
        checkpoint = IngestionCheckpoint.objects.create(
            source=self.source,
            station_uuid=str(self.station.source_entity_uuid),
            hour_bucket='1752850800000',
            sub_time_period=1752850800123,
        )
        self.assertEqual(self.activate().status_code, 200)
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.station_id, self.station.pk)
        self.assertEqual(checkpoint.sub_time_period, 1752850800123)

    def test_changed_mapping_resets_thresholds_but_same_mapping_preserves_them(self):
        """Confirmed thresholds survive retries, but cannot follow a changed measurement."""
        self.assertEqual(self.activate().status_code, 200)
        binding = MachineSignalBinding.objects.get(dictionary_point=self.point)
        binding.warn_max = 50
        binding.save()
        self.assertEqual(self.activate().status_code, 200)
        binding.refresh_from_db()
        self.assertEqual(binding.warn_max, 50)
        self.point.unit = 'kelvin'
        self.point.save()
        self.assertEqual(self.activate().status_code, 200)
        binding.refresh_from_db()
        self.assertTrue(binding.active)
        self.assertIsNone(binding.warn_max)
        self.assertEqual(binding.unit, 'kelvin')
