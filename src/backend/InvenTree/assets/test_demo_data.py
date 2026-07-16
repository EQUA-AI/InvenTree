"""Tests for the equipment-machine demo dataset extension."""

import datetime
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase

from tasks.models import KanbanCard

from assets.models import AssetMachine, AssetMaintenanceRecord, MachinePart
from company.models import Company
from part.models import Part


class AssetDemoDataTest(TestCase):
    """Verify the bundled machine demo data and its loader."""

    @classmethod
    def setUpTestData(cls):
        """Create the upstream demo parts intentionally reused by the extension."""
        for ipn, name in (
            ('TB1', 'Test Board 1'),
            ('TB2', 'Test Board 2'),
            ('TB3', 'Test Board 3'),
            ('002.02-PCB', 'Widget Board'),
        ):
            Part.objects.create(IPN=ipn, name=name)

    def load_demo_data(self, *, prune=False):
        """Load demo data while keeping command output out of test logs."""
        options = {'stdout': StringIO()}
        if prune:
            options['prune'] = True
        call_command('load_asset_demo_data', **options)

    @property
    def expected_work_order_count(self):
        """Linked-work-order count supported by this test backend."""
        return 18 if connection.vendor == 'postgresql' else 0

    def test_loads_rich_machine_dossiers(self):
        """Each demo machine has identity, components, and useful history."""
        self.load_demo_data()

        self.assertEqual(AssetMachine.objects.count(), 6)
        self.assertEqual(MachinePart.objects.count(), 31)
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 24)
        self.assertEqual(
            KanbanCard.objects.filter(reference__startswith='WO-DEMO-').count(),
            self.expected_work_order_count,
        )
        self.assertEqual(
            AssetMaintenanceRecord.objects.filter(work_order__isnull=True).count(),
            24 - self.expected_work_order_count,
        )

        for machine in AssetMachine.objects.all():
            with self.subTest(machine=machine.name):
                self.assertTrue(machine.location)
                self.assertTrue(machine.manufacturer)
                self.assertTrue(machine.model)
                self.assertTrue(machine.serial)
                self.assertGreater(len(machine.description), 200)
                self.assertGreaterEqual(machine.machine_parts.count(), 5)
                self.assertEqual(machine.maintenance_records.count(), 4)

        conveyor = AssetMachine.objects.get(name='Packaging Line 2 Conveyor')
        self.assertIn('1,200 cartons/hour', conveyor.description)
        self.assertIn('MCC-L2-07', conveyor.description)

        customer_stand = AssetMachine.objects.get(name='Customer Test Stand (ACME)')
        self.assertEqual(customer_stand.customer.name, 'ACME Manufacturing')

        photoeye = conveyor.machine_parts.get(part__IPN='EQ-CNV-PE-W4')
        self.assertEqual(photoeye.quantity, 6)
        self.assertIn('PE-201 through PE-206', photoeye.notes)

        repair = conveyor.maintenance_records.get(date='2026-01-18')
        if connection.vendor == 'postgresql':
            self.assertEqual(repair.work_order.reference, 'WO-DEMO-260118-001')
        else:
            self.assertIsNone(repair.work_order)
        self.assertIn('200-carton verification', repair.details)

    def test_loader_is_idempotent(self):
        """Reloading updates natural-keyed records without duplicating them."""
        self.load_demo_data()
        self.load_demo_data()

        self.assertEqual(AssetMachine.objects.count(), 6)
        self.assertEqual(MachinePart.objects.count(), 31)
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 24)
        self.assertEqual(
            KanbanCard.objects.filter(reference__startswith='WO-DEMO-').count(),
            self.expected_work_order_count,
        )

    def test_loader_adopts_linked_legacy_demo_customer(self):
        """The sparse legacy ACME machine can be enriched in place."""
        customer = Company.objects.create(name='ACME Manufacturing', is_customer=True)
        machine = AssetMachine.objects.create(
            name='Customer Test Stand (ACME)',
            customer=customer,
            description='Legacy sparse demo record',
            manufacturer='Equa',
            model='TS-200',
            serial='ACME-TS-200-01',
        )

        self.load_demo_data()

        customer.refresh_from_db()
        machine.refresh_from_db()
        self.assertEqual(
            customer.get_metadata('asset_demo_data'),
            {'kind': 'customer', 'schema_version': 1},
        )
        self.assertIn('64 digital I/O points', machine.description)

    def test_prune_removes_only_known_legacy_placeholder_rows(self):
        """Explicit pruning preserves technician-added machine records."""
        self.load_demo_data()

        machine = AssetMachine.objects.get(name='Packaging Line 2 Conveyor')
        legacy_part = Part.objects.get(IPN='TB1')
        legacy_link = MachinePart.objects.create(
            machine=machine,
            part=legacy_part,
            notes='Controller test board (placeholder link)',
        )
        modified_legacy_link = MachinePart.objects.create(
            machine=machine,
            part=Part.objects.get(IPN='TB2'),
            quantity=2,
            notes='sensor interface boards (placeholder link)',
        )
        custom_part = Part.objects.create(
            name='Technician-added Conveyor Guard', IPN='CUSTOM-CONVEYOR-GUARD'
        )
        custom_link = MachinePart.objects.create(
            machine=machine,
            part=custom_part,
            notes='Added after the demo dataset was loaded',
        )
        legacy_history = AssetMaintenanceRecord.objects.create(
            machine=machine,
            date=datetime.date(2026, 2, 2),
            summary='Tightened drive belt, checked VFD params',
            details='Belt tension adjusted; verified accel/decel and overload settings.',
            performed_by='M. Patel',
        )
        modified_legacy_history = AssetMaintenanceRecord.objects.create(
            machine=machine,
            date=datetime.date(2026, 1, 18),
            summary='Replaced discharge photoeye',
            details=(
                'Sensor was intermittently dropping; replaced and re-aligned '
                'bracket. Verified I/O in PLC.'
            ),
            performed_by='j. Rivera',
        )
        custom_history = AssetMaintenanceRecord.objects.create(
            machine=machine,
            date=datetime.date(2026, 7, 1),
            summary='Technician-added inspection',
            details='This row must survive demo-data refresh and pruning.',
        )

        self.load_demo_data()
        self.assertTrue(MachinePart.objects.filter(pk=legacy_link.pk).exists())
        self.assertTrue(MachinePart.objects.filter(pk=modified_legacy_link.pk).exists())
        self.assertTrue(MachinePart.objects.filter(pk=custom_link.pk).exists())
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(pk=legacy_history.pk).exists()
        )
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(
                pk=modified_legacy_history.pk
            ).exists()
        )
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(pk=custom_history.pk).exists()
        )

        self.load_demo_data(prune=True)
        self.assertFalse(MachinePart.objects.filter(pk=legacy_link.pk).exists())
        self.assertTrue(MachinePart.objects.filter(pk=modified_legacy_link.pk).exists())
        self.assertTrue(MachinePart.objects.filter(pk=custom_link.pk).exists())
        self.assertFalse(
            AssetMaintenanceRecord.objects.filter(pk=legacy_history.pk).exists()
        )
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(
                pk=modified_legacy_history.pk
            ).exists()
        )
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(pk=custom_history.pk).exists()
        )

    def test_loader_refuses_same_name_machine_with_different_identity(self):
        """A real machine with a coincidental demo name is never overwritten."""
        AssetMachine.objects.create(
            name='Air Compressor #4',
            manufacturer='Other Manufacturer',
            model='Plant Compressor X',
            serial='REAL-ASSET-004',
        )

        with self.assertRaisesMessage(CommandError, 'different manufacturer'):
            self.load_demo_data()

    def test_loader_refuses_unowned_maintenance_collision(self):
        """A same-date and same-title real history row is never overwritten."""
        machine = AssetMachine.objects.create(
            name='Unit 3 Switchgear Breaker',
            manufacturer='Schneider Electric',
            model='Masterpact MTZ',
            serial='U3-SWG-BKR-0138',
        )
        history = AssetMaintenanceRecord.objects.create(
            machine=machine,
            date=datetime.date(2025, 8, 20),
            summary='Quarterly infrared inspection',
            details='Technician-authored inspection details',
            performed_by='Site Electrician',
        )

        with self.assertRaisesMessage(CommandError, 'conflicts with an unowned'):
            self.load_demo_data()

        history.refresh_from_db()
        self.assertEqual(history.details, 'Technician-authored inspection details')
        self.assertEqual(history.performed_by, 'Site Electrician')

    def test_loader_reattaches_history_after_work_order_deletion(self):
        """Recreated demo work orders reuse their original maintenance row."""
        if connection.vendor != 'postgresql':
            self.skipTest('Demo work orders are only created on PostgreSQL')

        self.load_demo_data()
        history = AssetMaintenanceRecord.objects.get(
            work_order__reference='WO-DEMO-260118-001'
        )
        history_pk = history.pk
        history.work_order.delete()
        history.refresh_from_db()
        self.assertIsNone(history.work_order)

        self.load_demo_data()

        history.refresh_from_db()
        self.assertEqual(history.pk, history_pk)
        self.assertEqual(history.work_order.reference, 'WO-DEMO-260118-001')
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 24)

    def test_loader_refuses_unowned_work_order_reference(self):
        """A coincidental work-order reference is never silently overwritten."""
        if connection.vendor != 'postgresql':
            self.skipTest('Demo work orders are only created on PostgreSQL')

        KanbanCard.objects.create(
            title='Unrelated production work order',
            status=KanbanCard.STATUS_BACKLOG,
            priority=KanbanCard.PRIORITY_LOW,
            reference='WO-DEMO-250912-001',
        )

        with self.assertRaisesMessage(CommandError, 'not owned'):
            self.load_demo_data()

    def test_loader_refuses_ambiguous_part_ipn(self):
        """Duplicate upstream IPNs are not resolved arbitrarily."""
        Part.objects.create(IPN='TB1', name='Test Board 1', revision='B')

        with self.assertRaisesMessage(
            CommandError, "Multiple parts match demo IPN 'TB1'"
        ):
            self.load_demo_data()

    def test_loader_refuses_unowned_customer_name(self):
        """A matching real customer is not converted into demo data."""
        Company.objects.create(name='ACME Manufacturing', is_customer=True)

        with self.assertRaisesMessage(CommandError, 'not owned'):
            self.load_demo_data()

    def test_loader_refuses_unowned_part_ipn(self):
        """A matching real catalog record is not converted into a demo part."""
        Part.objects.create(IPN='EQ-CNV-DRV-2200', name='Conveyor Gearmotor, 2.2 kW')

        with self.assertRaisesMessage(CommandError, 'not owned'):
            self.load_demo_data()
