"""Tests for the equipment-machine demo dataset extension."""

import datetime
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.db.models import Sum
from django.test import TestCase

from tasks.models import KanbanCard, WorkOrderLifecycle

from assets.management.commands.load_asset_demo_data import Command
from assets.models import AssetMachine, AssetMaintenanceRecord, MachinePart
from company.models import Company
from part.models import Part, PartCategory

RICH_MACHINE_NAMES = (
    'Air Compressor #4',
    'Boiler Feed Pump B',
    'Customer Test Stand (ACME)',
    'Electronics Test Bench 1',
    'Packaging Line 2 Conveyor',
    'Unit 3 Switchgear Breaker',
)

WATER_MACHINE_NAMES = (
    'Aeration Basin No. 3 Diffuser Grid',
    'Aeration Blower No. 2',
    'Cooling Water Train A',
    'Dewatering Centrifuge No. 2',
    'Influent Pump Station No. 1',
    'Mechanical Bar Screen No. 1',
    'Remote Lift Station 14 — Blue River',
    'Secondary Clarifier No. 4',
    'UF/RO Skid No. 1',
    'UV Disinfection Channel No. 1',
)


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

    def load_demo_data(self, *, dry_run=False, prune=False):
        """Load demo data while keeping command output out of test logs."""
        options = {'stdout': StringIO()}
        if dry_run:
            options['dry_run'] = True
        if prune:
            options['prune'] = True
        call_command('load_asset_demo_data', **options)

    @property
    def expected_work_order_count(self):
        """Linked-work-order count supported by this test backend.

        Every one of the 24 rich-machine history rows now declares a completed
        work order; none may load as an unlinked legacy record.
        """
        return 24 if connection.vendor == 'postgresql' else 0

    def test_loads_rich_machine_dossiers(self):
        """Each demo machine has identity, components, and useful history."""
        self.load_demo_data()

        self.assertEqual(AssetMachine.objects.count(), 16)
        self.assertEqual(MachinePart.objects.count(), 81)
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 24)
        self.assertEqual(
            KanbanCard.objects.filter(reference__startswith='WO-DEMO-').count(),
            self.expected_work_order_count,
        )
        self.assertEqual(
            AssetMaintenanceRecord.objects.filter(work_order__isnull=True).count(),
            24 - self.expected_work_order_count,
        )

        for machine in AssetMachine.objects.filter(name__in=RICH_MACHINE_NAMES):
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

    def test_every_history_row_has_a_completed_work_order(self):
        """No dataset-owned maintenance row loads as an unlinked legacy record."""
        if connection.vendor != 'postgresql':
            self.skipTest('Demo work orders are only created on PostgreSQL')

        self.load_demo_data()

        records = AssetMaintenanceRecord.objects.select_related('work_order')
        self.assertEqual(records.count(), 24)
        self.assertEqual(records.filter(work_order__isnull=True).count(), 0)

        for record in records:
            with self.subTest(record=record.summary):
                work_order = record.work_order
                self.assertEqual(work_order.machine_id, record.machine_id)
                self.assertEqual(work_order.card_kind, KanbanCard.KIND_WORK_ORDER)
                self.assertEqual(
                    work_order.lifecycle_status, WorkOrderLifecycle.COMPLETED
                )
                self.assertFalse(work_order.is_active)
                self.assertIsNotNone(work_order.actual_completed_at)
                self.assertEqual(work_order.actual_completed_at.date(), record.date)
                self.assertTrue(work_order.reference)
                self.assertTrue(work_order.work_order_type)
                # Imported history is explicitly marked as synthetic demo data.
                event = work_order.events.get(event_type='IMPORTED_HISTORY')
                self.assertEqual(event.metadata['source'], 'asset_demo_data')
                self.assertTrue(event.metadata['synthetic'])

    def test_backfilled_records_keep_their_primary_keys(self):
        """Attaching the six new work orders adopts the existing history rows."""
        if connection.vendor != 'postgresql':
            self.skipTest('Demo work orders are only created on PostgreSQL')

        backfilled = {
            'WO-DEMO-250820-001': ('Unit 3 Switchgear Breaker', 'inspection', 'medium'),
            'WO-DEMO-260602-001': ('Air Compressor #4', 'corrective', 'low'),
            'WO-DEMO-251119-001': ('Boiler Feed Pump B', 'inspection', 'high'),
            'WO-DEMO-260207-001': (
                'Customer Test Stand (ACME)',
                'corrective',
                'medium',
            ),
            'WO-DEMO-251103-001': ('Electronics Test Bench 1', 'inspection', 'medium'),
            'WO-DEMO-260613-001': ('Electronics Test Bench 1', 'preventive', 'medium'),
        }

        # Reproduce a database seeded by the previous manifest: the six rows
        # exist as unlinked history, so the loader must adopt them rather than
        # insert a duplicate alongside the newly declared work order.
        self.load_demo_data()
        seeded = {}
        for reference in backfilled:
            record = AssetMaintenanceRecord.objects.get(work_order__reference=reference)
            seeded[reference] = record.pk
            record.work_order.delete()

        self.assertEqual(
            AssetMaintenanceRecord.objects.filter(work_order__isnull=True).count(),
            len(backfilled),
        )

        self.load_demo_data()

        for reference, (machine_name, wo_type, priority) in backfilled.items():
            with self.subTest(reference=reference):
                work_order = KanbanCard.objects.get(reference=reference)
                record = work_order.maintenance_record
                self.assertEqual(record.machine.name, machine_name)
                self.assertEqual(work_order.work_order_type, wo_type)
                self.assertEqual(work_order.priority, priority)
                self.assertEqual(record.pk, seeded[reference])

        self.assertEqual(AssetMaintenanceRecord.objects.count(), 24)

    def test_manifest_requires_a_work_order_on_every_history_row(self):
        """A history row without work-order metadata fails manifest validation."""
        machines = [
            {
                'name': 'Demo Machine',
                'maintenance': [{'date': '2026-01-01', 'summary': 'x'}],
            }
        ]
        with self.assertRaisesMessage(
            CommandError, 'must declare a completed work order'
        ):
            Command._validate_maintenance_history(machines)

    def test_manifest_rejects_duplicate_work_order_references(self):
        """Two history rows may not claim the same work-order reference."""
        entry = {
            'date': '2026-01-01',
            'summary': 'x',
            'work_order': {
                'reference': 'WO-DEMO-DUP-001',
                'type': 'inspection',
                'priority': 'low',
            },
        }
        machines = [{'name': 'Demo Machine', 'maintenance': [entry, dict(entry)]}]
        with self.assertRaisesMessage(CommandError, 'is declared twice'):
            Command._validate_maintenance_history(machines)

    def test_loads_water_wastewater_dataset(self):
        """Water assets retain hierarchy, evidence, and where-used coverage."""
        self.load_demo_data()

        water_parts = Part.objects.filter(
            metadata__asset_demo_data__dataset='water_wastewater'
        )
        water_machines = AssetMachine.objects.filter(name__in=WATER_MACHINE_NAMES)
        water_links = MachinePart.objects.filter(machine__in=water_machines)
        managed_categories = PartCategory.objects.filter(
            metadata__asset_demo_data__kind='part_category'
        )

        self.assertEqual(water_parts.count(), 50)
        self.assertEqual(water_parts.filter(category__isnull=False).count(), 50)
        self.assertEqual(water_parts.filter(link__isnull=False).count(), 29)
        self.assertEqual(managed_categories.count(), 65)
        self.assertEqual(
            managed_categories.filter(parent__isnull=True, structural=True).count(), 20
        )
        self.assertEqual(
            managed_categories.filter(parent__isnull=False, structural=False).count(),
            45,
        )

        self.assertEqual(water_machines.count(), 10)
        self.assertEqual(water_links.count(), 50)
        self.assertEqual(water_links.aggregate(total=Sum('quantity'))['total'], 1686)
        self.assertSetEqual(
            set(water_links.values_list('part_id', flat=True)),
            set(water_parts.values_list('pk', flat=True)),
        )
        for part in water_parts:
            with self.subTest(part=part.IPN):
                self.assertEqual(part.machine_installations.count(), 1)

        self.assertFalse(
            AssetMaintenanceRecord.objects.filter(machine__in=water_machines).exists()
        )
        pump = Part.objects.get(IPN='EQ-INF-PMP-0750')
        self.assertEqual(pump.category.pathstring, 'Pumps/Submersible')
        self.assertEqual(
            pump.get_metadata('asset_demo_data')['reference_scope'], 'equipment_family'
        )
        self.assertEqual(
            Part.objects.get(IPN='EQ-BLR-RSN-CAT').name, 'Cation exchange resin, 1 ft³'
        )
        self.assertEqual(MachinePart.objects.get(part__IPN='EQ-BLW-CPL-ELM').notes, '')

    def test_dry_run_rolls_back_every_record(self):
        """Dry-run mode exercises the full loader without retaining writes."""
        initial_counts = {
            'categories': PartCategory.objects.count(),
            'parts': Part.objects.count(),
            'machines': AssetMachine.objects.count(),
            'links': MachinePart.objects.count(),
            'maintenance': AssetMaintenanceRecord.objects.count(),
            'work_orders': KanbanCard.objects.count(),
        }

        self.load_demo_data(dry_run=True)

        self.assertEqual(PartCategory.objects.count(), initial_counts['categories'])
        self.assertEqual(Part.objects.count(), initial_counts['parts'])
        self.assertEqual(AssetMachine.objects.count(), initial_counts['machines'])
        self.assertEqual(MachinePart.objects.count(), initial_counts['links'])
        self.assertEqual(
            AssetMaintenanceRecord.objects.count(), initial_counts['maintenance']
        )
        self.assertEqual(KanbanCard.objects.count(), initial_counts['work_orders'])

    def test_loader_is_idempotent(self):
        """Reloading updates natural-keyed records without duplicating them."""
        self.load_demo_data()
        self.load_demo_data()

        self.assertEqual(AssetMachine.objects.count(), 16)
        self.assertEqual(MachinePart.objects.count(), 81)
        self.assertEqual(PartCategory.objects.count(), 65)
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
        compressor = AssetMachine.objects.get(name='Air Compressor #4')
        legacy_compressor_links = []
        for revision in ('REV-A', 'REV-B'):
            compressor_part = Part.objects.create(
                name='Widget Board (assembled)', IPN='002.01-PCBA', revision=revision
            )
            legacy_compressor_links.append(
                MachinePart.objects.create(
                    machine=compressor,
                    part=compressor_part,
                    notes='Control PCB assembly (placeholder link)',
                )
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
            all(
                MachinePart.objects.filter(pk=link.pk).exists()
                for link in legacy_compressor_links
            )
        )
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
        self.assertTrue(
            all(
                not MachinePart.objects.filter(pk=link.pk).exists()
                for link in legacy_compressor_links
            )
        )
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

    def test_loader_refuses_unowned_machine_with_current_identity(self):
        """An exact machine identity is insufficient proof of demo ownership."""
        machine = AssetMachine.objects.create(
            name='Influent Pump Station No. 1',
            description='Technician-authored machine record',
            manufacturer='Xylem Flygt',
            model='NP 3301 MT',
            serial='TC-INF-PS1-001',
        )

        with self.assertRaisesMessage(CommandError, 'not owned'):
            self.load_demo_data()

        machine.refresh_from_db()
        self.assertEqual(machine.description, 'Technician-authored machine record')

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

    def test_loader_refuses_unowned_category_path(self):
        """A real category is not silently converted into demo-owned data."""
        PartCategory.objects.create(name='Pumps')

        with self.assertRaisesMessage(CommandError, 'not owned'):
            self.load_demo_data()
