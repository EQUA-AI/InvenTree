"""Equipment registry regression tests; never use live Cassandra or demo resets."""

import json
from io import StringIO
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import Part

from .models import (
    AssetComponent,
    AssetMachine,
    Client,
    DictionaryPoint,
    MachineSignalState,
)
from .registry import (
    decode_upload,
    ensure_pump,
    import_dictionary,
    plan_dictionary,
    register_station,
)


def test_scope(actor):
    """Explicit test-only Client grant, independent of actor staff flags."""
    return [
        {
            'customer_id': None,
            'site_key': None,
            'client_id': Client.objects.get(code='registry-test').pk,
        }
    ]


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=test_scope)
class RegistryTests(InvenTreeAPITestCase):
    """Exercise real registered URLs, roles, imports and review serializers."""

    is_staff = False
    roles = ['work_order.view', 'work_order.add', 'work_order.change']

    @classmethod
    def setUpTestData(cls):
        """Load isolated catalogue data and a scoped station."""
        ContentType.objects.clear_cache()
        super().setUpTestData()
        call_command('load_pump_catalogue', stdout=StringIO())
        cls.tenant = Client.objects.create(code='registry-test', name='Registry Test')
        cls.station = register_station(
            client=cls.tenant,
            name='Test station',
            source_namespace='test-source',
            source_entity_uuid=uuid4(),
            source_key='PH_3',
        )

    def setUp(self):
        """Reset content-type cache between rollback-based tests."""
        ContentType.objects.clear_cache()
        super().setUp()
        self.addCleanup(ContentType.objects.clear_cache)

    def raw(self, **extra):
        """Small representative payload, not a fabricated full dictionary."""
        return json.dumps(
            {
                'st': 'I',
                'pd': {'P1': {'st': 'I'}, 'P2': {'st': 'I'}},
                'dex': {
                    'ID': 'PH_3',
                    'TIMESTAMP': '1.752854398616E9',
                    'PUMP1_MOTOR_CORE_RTD1_PROCESS_VALUE': '39.2',
                    'COMMAN_FORBAY_LEVEL': '132.45',
                    **extra,
                },
            }
        ).encode()

    def upload(self, action, raw=None, **fields):
        """Post a multipart file through the actual registry routes."""
        data = {
            'data_file': SimpleUploadedFile(
                'sample.json', raw or self.raw(), content_type='application/json'
            ),
            **fields,
        }
        return self.client.post(
            f'/api/assets/registry/{self.station.pk}/{action}/',
            data=data,
            format='multipart',
        )

    def imported(self):
        """Import a tiny dictionary and return its motor-temperature point."""
        raw = self.raw()
        plan = plan_dictionary(self.station, raw)
        import_dictionary(self.station, raw, expected_hash=plan['source_hash'])
        return DictionaryPoint.objects.get(
            station=self.station, raw_tag='PUMP1_MOTOR_CORE_RTD1_PROCESS_VALUE'
        )

    def test_registration_and_hierarchy(self):
        """Retain stable identities and reject a self-parent cycle."""
        same = register_station(
            client=self.tenant,
            name='Do not rename',
            source_namespace='test-source',
            source_entity_uuid=self.station.source_entity_uuid,
            source_key='PH_3',
        )
        self.assertEqual(same.pk, self.station.pk)
        pump = ensure_pump(self.station, 'P1')
        self.assertEqual(ensure_pump(self.station, 'P1').uuid, pump.uuid)
        self.assertEqual(pump.client_id, self.tenant.pk)
        pump.parent = pump
        with self.assertRaises(ValidationError):
            pump.save()
        response = self.client.get(f'/api/assets/registry/{self.station.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['children']), 1)

    def test_preview_is_read_only(self):
        """Planner has no writes and HTTP preview leaves domain rows unchanged."""
        counts = [
            m.objects.count()
            for m in [AssetMachine, AssetComponent, DictionaryPoint, MachineSignalState]
        ]
        with CaptureQueriesContext(connection) as queries:
            plan_dictionary(self.station, self.raw())
        response = self.upload('preview')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['counts']['total'], 5)
        self.assertEqual(response.data['pumps'], ['P1', 'P2'])
        self.assertFalse(
            any(
                q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
                for q in queries
            )
        )
        self.assertEqual(
            counts,
            [
                m.objects.count()
                for m in [
                    AssetMachine,
                    AssetComponent,
                    DictionaryPoint,
                    MachineSignalState,
                ]
            ],
        )

    def test_import_is_additive_and_preserves_reviews(self):
        """Repeated imports preserve point identities and human review decisions."""
        point = self.imported()
        point.review_note = 'Keep a human decision'
        point.status = 'rejected'
        point.save()
        before = set(DictionaryPoint.objects.values_list('uuid', flat=True))
        plan = plan_dictionary(self.station, self.raw())
        result = self.upload('import', source_hash=plan['source_hash'])
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['created'], 0)
        self.assertEqual(
            before, set(DictionaryPoint.objects.values_list('uuid', flat=True))
        )
        point.refresh_from_db()
        self.assertEqual(point.review_note, 'Keep a human decision')
        self.assertEqual(point.status, 'rejected')
        self.assertFalse(AssetComponent.objects.exclude(status='draft').exists())

    def test_file_mismatch_rejected(self):
        """A file must match the preview hash before import."""
        response = self.upload('import', source_hash='not-previewed')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DictionaryPoint.objects.exists())

    def test_duplicate_alias_import_is_unresolved(self):
        """An alias competing with an existing point requires review."""
        self.imported()
        plan = plan_dictionary(self.station, self.raw(PUMP1_MOTOR_CORE_RTD1='39.2'))
        alias = next(
            p for p in plan['points'] if p['raw_tag'] == 'PUMP1_MOTOR_CORE_RTD1'
        )
        self.assertEqual(alias['match_method'], 'conflict')
        self.assertTrue(alias['issue'])

    def test_types_and_malformed_json(self):
        """Detect type conflicts and reject duplicate/non-finite JSON literals."""
        rows = []
        for value in ['39.2', None, 'not numeric']:
            payload = json.loads(self.raw())
            payload['dex']['PUMP1_MOTOR_CORE_RTD1_PROCESS_VALUE'] = value
            rows.append(payload)
        plan = plan_dictionary(self.station, json.dumps(rows).encode())
        self.assertEqual(
            next(p for p in plan['points'] if p['match_method'] == 'conflict')[
                'raw_tag'
            ],
            'PUMP1_MOTOR_CORE_RTD1_PROCESS_VALUE',
        )
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'[]']:
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                plan_dictionary(self.station, raw)
        self.assertTrue(decode_upload(b'{"x":1e999}')['x'].is_finite())

    def test_source_identity_and_timestamp(self):
        """Reject another station's row and timestamps outside their hour."""
        row = {'entity_uuid': str(uuid4()), 'data1': json.loads(self.raw())}
        with self.assertRaises(ValidationError):
            plan_dictionary(self.station, json.dumps(row).encode())
        row.update(
            entity_uuid=str(self.station.source_entity_uuid),
            time_period=1752850800000,
            sub_time_period=1752854400000,
        )
        with self.assertRaises(ValidationError):
            plan_dictionary(self.station, json.dumps(row).encode())
        row['sub_time_period'] -= 1
        self.assertEqual(
            plan_dictionary(self.station, json.dumps(row).encode())['counts']['total'],
            5,
        )

    def test_foreign_client_and_unresolved_scope(self):
        """Reject cross-Client, unresolved and mixed-identity grants."""
        other = Client.objects.create(code='registry-other', name='Other Client')
        station = register_station(
            client=other,
            name='Private station',
            source_namespace='private',
            source_entity_uuid=uuid4(),
            source_key='PH_3',
        )
        for url in [
            f'/api/assets/registry/{station.pk}/',
            f'/api/assets/machines/{station.pk}/',
        ]:
            self.assertEqual(self.client.get(url).status_code, 404)
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: []):
            self.assertEqual(self.client.get('/api/assets/registry/').status_code, 403)
        mixed = lambda actor: [
            {'client_id': self.tenant.pk, 'customer_id': 99, 'site_key': None}
        ]
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=mixed):
            self.assertEqual(self.client.get('/api/assets/registry/').data['count'], 0)

    def test_view_only_cannot_import(self):
        """Read authority permits preview, not import or anonymous access."""
        self.clearRoles()
        self.assignRole('work_order.view')
        self.assertEqual(self.upload('preview').status_code, 200)
        self.assertEqual(self.upload('import', source_hash='anything').status_code, 403)
        self.logout()
        self.assertIn(self.client.get('/api/assets/registry/').status_code, [401, 403])

    def test_review_mapping(self):
        """Approval validates component ownership and physical unit dimensions."""
        point = self.imported()
        body = {
            'machine': point.machine_id,
            'component': point.component_id,
            'template': point.template_id,
            'display_name': 'Core temperature',
            'data_type': 'number',
            'unit': 'degC',
            'unit_status': 'verified',
            'status': 'approved',
            'review_note': 'Verified against the test fixture tag definition',
        }
        url = f'/api/assets/registry/{self.station.pk}/dictionary/{point.pk}/review/'
        response = self.client.patch(url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'approved')
        body['unit'] = 'm'
        self.assertEqual(self.client.patch(url, body, format='json').status_code, 400)
        other = ensure_pump(self.station, 'P2')
        body.update(machine=other.pk, unit='degC')
        self.assertEqual(self.client.patch(url, body, format='json').status_code, 404)

    def test_repeated_components_and_foreign_slot(self):
        """Support repeated Parts but reject incompatible existing inference slots."""
        pump = ensure_pump(self.station, 'P1')
        motor = Part.objects.get(IPN='PS-MOTOR')
        for code in ['motor-a', 'motor-b']:
            response = self.client.post(
                f'/api/assets/registry/{pump.pk}/components/',
                {'part': motor.pk, 'code': code, 'name': code},
                format='json',
            )
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            AssetComponent.objects.filter(machine=pump, part=motor).count(), 2
        )
        AssetComponent.objects.create(
            machine=pump,
            part=Part.objects.get(IPN='PS-FOREBAY'),
            code='PS-MOTOR:1',
            name='Wrong part',
        )
        with self.assertRaises(ValidationError):
            import_dictionary(
                self.station,
                self.raw(),
                expected_hash=plan_dictionary(self.station, self.raw())['source_hash'],
            )
        self.assertFalse(DictionaryPoint.objects.exists())
