"""R6 P13: the Parts tab's component rows carry position, work and stock context."""

from datetime import datetime, timezone as dt_timezone
from io import StringIO
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from tasks.models import WorkOrder
from tasks.scope import ScopeError

from assets.models import Client, ClientScopeGrant
from assets.registry import ensure_pump, register_station
from assets.registry_models import AssetComponent
from part.models import Part
from stock.models import StockItem
from stock.status_codes import StockStatus
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class RegistryPartsContextTests(TestCase):
    """Real catalogue, registered station and pumps, work and stock rows."""

    @classmethod
    def setUpTestData(cls):
        """The pump catalogue as the loader writes it."""
        super().setUpTestData()
        call_command('load_pump_catalogue', stdout=StringIO())

    def setUp(self):
        """A Work reader without stock access, two pumps and their slots."""
        self.reader = get_user_model().objects.create_user(username='parts-reader')
        self.readers = Group.objects.create(name='Parts readers')
        self.reader.groups.add(self.readers)
        RuleSet.objects.update_or_create(
            group=self.readers, name='work_order', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(name='Parts', code='parts-context')
        ClientScopeGrant.objects.create(user=self.reader, client=self.tenant)
        self.station = register_station(
            client=self.tenant,
            name='Parts station',
            source_namespace='parts-source',
            source_entity_uuid=uuid4(),
            source_key='PH_8',
        )
        self.pump = ensure_pump(self.station, 'P1')
        self.other = ensure_pump(self.station, 'P2')
        self.bearing_part = Part.objects.get(IPN='PS-MOTOR-DE-BRG')

        def slot(machine, ipn):
            part = Part.objects.get(IPN=ipn)
            return AssetComponent.objects.create(
                machine=machine, part=part, code=f'{ipn}:1', name=part.name
            )

        slot(self.pump, 'PS-MOTOR-DE-BRG')
        slot(self.pump, 'PS-MOTOR')
        slot(self.pump, 'PS-THRUST-BRG')
        slot(self.other, 'PS-MOTOR-DE-BRG')

        def work(machine, ref, **extra):
            return WorkOrder.objects.create(
                title=f'{ref or "general"} {machine.pk}',
                machine=machine,
                priority='medium',
                lifecycle_status=extra.pop('lifecycle_status', 'planned'),
                affected_component_ref=ref,
                **extra,
            )

        work(self.pump, 'PS-MOTOR-DE-BRG:1')
        work(self.pump, 'PS-MOTOR-DE-BRG:1', lifecycle_status='completed')
        work(self.pump, 'PS-MOTOR-DE-BRG:1', is_active=False)
        work(self.pump, '')
        work(self.other, 'PS-MOTOR-DE-BRG:1')
        work(self.other, 'PS-MOTOR-DE-BRG:1')

        StockItem.objects.create(part=self.bearing_part, quantity=4)
        StockItem.objects.create(part=self.bearing_part, quantity=2)
        # Not in stock by the native rule (which does count damaged): rejected,
        # and empty.
        StockItem.objects.create(
            part=self.bearing_part, quantity=9, status=StockStatus.REJECTED.value
        )
        StockItem.objects.create(part=self.bearing_part, quantity=0)
        self.client.force_login(self.reader)

    def rows(self, machine=None):
        """Component rows by owner and code, as the Parts tab reads them."""
        response = self.client.get(
            f'/api/assets/registry/{(machine or self.pump).pk}/components/'
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {
            (row['machine'], row['code']): row for row in response.json()['results']
        }

    def test_each_slot_names_its_reference_position(self):
        """Only the families the owner rule maps have a 3D position."""
        rows = self.rows()
        positions = {code: row['reference_position'] for (_, code), row in rows.items()}
        self.assertEqual(
            positions,
            {
                'PS-MOTOR-DE-BRG:1': 'bearing',
                'PS-MOTOR:1': 'motor',
                'PS-THRUST-BRG:1': None,
            },
        )

    @override_settings(USE_TZ=True)
    def test_workbook_source_keeps_raw_provenance_and_stored_import_time(self):
        """Both pump and station reads name the import without leaking its hash."""
        raw = 'Workbook population f9006a91f8a9'
        imported_at = datetime(2026, 10, 10, 14, 30, tzinfo=dt_timezone.utc)
        slot = AssetComponent.objects.get(
            machine=self.pump, code='PS-MOTOR-DE-BRG:1'
        )
        AssetComponent.objects.filter(pk=slot.pk).update(
            provenance=raw,
            created_at=imported_at,
            reviewed_at=datetime(2026, 10, 20, 12, tzinfo=dt_timezone.utc),
        )
        for machine in (self.pump, self.station):
            with self.subTest(owner=machine.pk):
                row = self.rows(machine)[self.pump.pk, slot.code]
                self.assertEqual(row['provenance'], raw)
                self.assertEqual(row['provenance_display'], 'Equipment register import')
                self.assertNotIn('f9006a91f8a9', row['provenance_display'])
                self.assertEqual(
                    datetime.fromisoformat(row['provenance_imported_at']), imported_at
                )

    def test_scada_source_does_not_invent_an_import_date(self):
        """Mapping provenance is concise; creation and review do not imply import."""
        raw = 'Inferred from source tags; physical assignment is unverified.'
        slot = AssetComponent.objects.get(machine=self.pump, code='PS-MOTOR:1')
        AssetComponent.objects.filter(pk=slot.pk).update(provenance=raw)
        row = self.rows()[self.pump.pk, slot.code]
        self.assertEqual(row['provenance'], raw)
        self.assertEqual(row['provenance_display'], 'SCADA tag mapping')
        self.assertIsNone(row['provenance_imported_at'])

    def test_other_and_missing_sources_do_not_acquire_import_provenance(self):
        """Custom notes remain recorded text; an absent source stays absent."""
        slot = AssetComponent.objects.get(machine=self.pump, code='PS-MOTOR:1')
        for raw in ('Manually proposed assignment', '', 'Workbook population unknown'):
            with self.subTest(provenance=raw):
                AssetComponent.objects.filter(pk=slot.pk).update(provenance=raw)
                row = self.rows()[self.pump.pk, slot.code]
                self.assertEqual(row['provenance'], raw)
                self.assertEqual(row['provenance_display'], raw)
                self.assertIsNone(row['provenance_imported_at'])

    def test_open_work_is_the_slots_own_on_its_own_machine(self):
        """Closed, archived, unnamed and other pumps' work does not count."""
        rows = self.rows()
        self.assertEqual(
            rows[self.pump.pk, 'PS-MOTOR-DE-BRG:1']['open_work'], {'count': 1}
        )
        self.assertEqual(rows[self.pump.pk, 'PS-MOTOR:1']['open_work'], {'count': 0})
        station = self.rows(self.station)
        self.assertEqual(
            station[self.other.pk, 'PS-MOTOR-DE-BRG:1']['open_work'], {'count': 2}
        )
        self.assertEqual(
            station[self.pump.pk, 'PS-MOTOR-DE-BRG:1']['open_work'], {'count': 1}
        )

    def test_shared_customer_work_is_counted_on_its_slot(self):
        """O24: a customer job on the slot is shared with Work viewers.

        Before O24 the customer value hid it from the count.
        """
        from company.models import Company

        WorkOrder.objects.create(
            title='Customer bearing job',
            machine=self.pump,
            priority='high',
            lifecycle_status='planned',
            affected_component_ref='PS-MOTOR-DE-BRG:1',
            customer=Company.objects.create(name='Elsewhere co', is_customer=True),
        )
        rows = self.rows()
        self.assertEqual(
            rows[self.pump.pk, 'PS-MOTOR-DE-BRG:1']['open_work'], {'count': 2}
        )

    def test_station_slots_and_virtual_parts_have_no_position_or_stock(self):
        """Only a pump has the model; a logical group is never stocked."""
        RuleSet.objects.update_or_create(
            group=self.readers, name='stock', defaults={'can_view': True}
        )
        discharge = Part.objects.get(IPN='PS-DISCHARGE')
        AssetComponent.objects.create(
            machine=self.station, part=discharge, code='PS-DISCHARGE:station'
        )
        group = Part.objects.filter(IPN__startswith='PS-', virtual=True).first()
        self.assertIsNotNone(group)
        AssetComponent.objects.create(
            machine=self.pump, part=group, code=f'{group.IPN}:1', name=group.name
        )
        rows = self.rows(self.station)
        self.assertIsNone(
            rows[self.station.pk, 'PS-DISCHARGE:station']['reference_position']
        )
        # Natively unstockable: null, not "0 in stock".
        self.assertIsNone(rows[self.pump.pk, f'{group.IPN}:1']['stock'])

    def test_unresolved_work_scope_is_unknown_not_zero(self):
        """Without a Work scope each slot's work is null."""
        with mock.patch(
            'tasks.scope.work_order_scope_filter', side_effect=ScopeError('down')
        ):
            rows = self.rows()
        self.assertTrue(all(row['open_work'] is None for row in rows.values()))

    def test_stock_is_shown_only_to_stock_readers_and_counts_only_in_stock(self):
        """No stock role: null. With it: the native in-stock quantity only."""
        rows = self.rows()
        self.assertIsNone(rows[self.pump.pk, 'PS-MOTOR-DE-BRG:1']['stock'])
        RuleSet.objects.update_or_create(
            group=self.readers, name='stock', defaults={'can_view': True}
        )
        rows = self.rows()
        self.assertEqual(
            rows[self.pump.pk, 'PS-MOTOR-DE-BRG:1']['stock'], {'quantity': 6.0}
        )
        self.assertEqual(rows[self.pump.pk, 'PS-MOTOR:1']['stock'], {'quantity': 0.0})

    def test_context_costs_the_same_queries_for_more_slots(self):
        """Work and stock come from subqueries, not a request per row."""
        RuleSet.objects.update_or_create(
            group=self.readers, name='stock', defaults={'can_view': True}
        )
        self.rows(self.station)
        with CaptureQueriesContext(connection) as before:
            self.rows(self.station)
        for ipn in ('PS-CASING', 'PS-DISCHARGE', 'PS-GUIDE-BRG'):
            part = Part.objects.get(IPN=ipn)
            AssetComponent.objects.create(
                machine=self.pump, part=part, code=f'{ipn}:1', name=part.name
            )
        with CaptureQueriesContext(connection) as after:
            rows = self.rows(self.station)
        self.assertEqual(len(rows), 7)
        self.assertEqual(len(after), len(before))
