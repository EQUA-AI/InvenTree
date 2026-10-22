"""Workbook population is validated up front, previewable, and repeatable."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from openpyxl import Workbook

from assets.models import (
    AssetLocation,
    AssetMachine,
    Client,
    MachineLocationTransfer,
    MachinePlacementHistory,
)
from assets.population import PopulationError, apply_population, plan_population
from assets.population_workbook import WorkbookError, load_population_workbook

STATION_UUID = str(uuid4())
CLIENT = 'wb-test-client'

LOCATION_HEADER = [
    'code',
    'name',
    'kind',
    'parent_code',
    'timezone',
    'description',
    'client_code',
    'archived',
    'reason_for_change',
]
MACHINE_HEADER = [
    'machine_key',
    'name',
    'asset_type',
    'asset_location_code',
    'legacy_location_label',
    'manufacturer',
    'model',
    'serial',
    'description',
    'active',
    'client_code',
    'source_key',
    'source_namespace',
    'source_entity_uuid',
    'criticality',
    'maintenance_strategy',
    'entry_status',
]
PROFILE_HEADER = [
    'machine_key',
    'criticality',
    'maintenance_strategy',
    'components_json',
    'energy_sources_json',
    'fault_codes_json',
    'approved_spares_json',
]
COMPONENT_HEADER = [
    'owner_machine_key',
    'occurrence_code',
    'display_name',
    'catalogue_part_ipn',
    'quantity',
]
LINK_HEADER = ['machine_key', 'part_ipn', 'quantity', 'notes']
PART_HEADER = [
    'ipn',
    'name',
    'category',
    'description',
    'manufacturer',
    'manufacturer_part_number',
    'part_type',
    'virtual',
    'purchaseable',
    'trackable',
    'consumable',
    'units',
    'minimum_stock',
    'source_url',
    'notes',
]


def default_sheets():
    """A minimal but complete workbook: 3 locations, station, 2 pumps, 1 equipment."""
    return {
        'Facility Locations': [
            LOCATION_HEADER,
            ['WB-SITE', 'Workbook Site', 'site', None, 'America/Chicago', 'Root', CLIENT, False, 'init'],
            ['WB-FEPS', 'Workbook Station Building', 'building', 'WB-SITE', None, '', CLIENT, False, 'init'],
            ['WB-BAY-01', 'Pump Bay 01', 'area', 'WB-FEPS', None, '', CLIENT, False, 'init'],
        ],
        'Machine Records': [
            MACHINE_HEADER,
            ['ST', 'Workbook Station', 'pumphouse', 'WB-FEPS', 'Station', '', 'Model S', 'SN-ST', 'Station desc', True, CLIENT, 'WB_ST', 'wb-ns', STATION_UUID, 'critical', 'predictive', 'STATION_REGISTRATION_REQUIRED'],
            ['P01', 'Workbook Station / Pump 01', 'pump', 'WB-BAY-01', 'Pump Bay 01', 'Multiple OEMs', 'Train Rev A', 'SN-P1', 'Pump one', True, CLIENT, 'P1', 'wb-ns', None, 'high', 'predictive', 'REGISTERED_PUMP_SLOT_REQUIRED'],
            ['P02', 'Workbook Station / Pump 02', 'pump', 'WB-BAY-01', 'Pump Bay 01', 'Multiple OEMs', 'Train Rev A', 'SN-P2', 'Pump two', True, CLIENT, 'P2', 'wb-ns', None, 'high', 'predictive', 'REGISTERED_PUMP_SLOT_REQUIRED'],
            ['GEN', 'Workbook Standby Generator', 'equipment', 'WB-FEPS', 'Yard', 'Caterpillar', 'C32', 'SN-GEN', 'Generator', True, CLIENT, '', '', None, 'medium', 'preventive', 'UI_API'],
        ],
        'Machine Profiles': [
            PROFILE_HEADER,
            ['P01', 'high', 'predictive', json.dumps([{'name': 'Pump', 'ref': 'PUMP'}, {'name': 'Impeller', 'ref': 'IMP', 'parent_ref': 'PUMP'}]), json.dumps(['electrical', 'hydraulic']), json.dumps(['WB-LOW-FLOW']), '[]'],
        ],
        'Component Occurrences': [
            COMPONENT_HEADER + ['engineering_status', 'actual_installed_compatibility', 'note'],
            ['P01', 'PUMP-01', 'Pump - Train 01', 'WB-REF-P01', 1, 'ENGINEERED_REFERENCE_PENDING_REVIEW', 'NOT_APPROVED', 'Reference occurrence.'],
            ['P01', 'MOTOR-01', 'Motor - Train 01', 'WB-REF-P02', 1, 'ENGINEERED_REFERENCE_PENDING_REVIEW', 'NOT_APPROVED', ''],
        ],
        'Machine Part Links': [
            LINK_HEADER,
            ['P01', 'WB-REF-P01', 1, 'reference'],
            ['P01', 'WB-REF-P02', 1, 'reference'],
        ],
        'Parts Catalogue': [
            PART_HEADER,
            ['WB-REF-P01', 'Main pump | Family A', 'Workbook / Reference Equipment', 'Reference pump family.', 'KSB', None, 'VIRTUAL_FAMILY_REFERENCE', True, False, False, False, 'pcs', 0, None, 'Not orderable.'],
            ['WB-REF-P02', 'Main motor | Family B', 'Workbook / Reference Equipment', 'Reference motor family.', 'WEG', None, 'VIRTUAL_FAMILY_REFERENCE', True, False, False, False, 'pcs', 0, None, 'Not orderable.'],
            ['WB-CAT-X1', 'Control power supply', 'Workbook / Orderable Candidates', 'Real OEM candidate.', 'Phoenix Contact', '2904602', 'REAL_OEM_PRODUCT_CANDIDATE', False, True, False, False, 'pcs', 2, 'https://example.invalid/2904602', 'Candidate only.'],
        ],
        'Warehouse Locations': [
            ['stock_code', 'name', 'parent_stock_code', 'structural', 'description', 'location_domain'],
            ['WB-WH', 'Workbook Spares Warehouse', None, True, 'Main warehouse', 'STOCK'],
            ['WB-WH-ELEC', 'Electrical Spares', 'WB-WH', True, '', 'STOCK'],
            ['WB-WH-ELEC-B1', 'Electrical Bin B1', 'WB-WH-ELEC', False, 'Bin', 'STOCK'],
        ],
        'Companies': [
            ['company_key', 'name', 'description', 'website', 'currency', 'phone', 'email', 'is_manufacturer', 'is_supplier', 'active', 'relationship_state', 'notes'],
            ['WB-CO-1', 'Phoenix Contact', 'Manufacturer identity only.', '', 'USD', '', '', True, False, True, 'IDENTITY_ONLY', ''],
            ['WB-CO-2', 'Workbook Distributor', 'Supplier identity only.', '', 'USD', '', '', False, True, True, 'IDENTITY_ONLY', ''],
        ],
        'Supplier Offers': [
            ['part_ipn', 'manufacturer', 'manufacturer_mpn', 'supplier', 'supplier_sku', 'pack_quantity', 'lead_time_days', 'price_usd', 'currency', 'status', 'source_url', 'required_action'],
            ['WB-CAT-X1', 'Phoenix Contact', '2904602', 'NOT_SELECTED', '', 1, '', '', 'USD', 'NO_VERIFIED_SUPPLIER_OFFER', 'https://example.invalid/2904602', 'Verify listing'],
        ],
        'Stock Items': [
            ['stock_record_key', 'part_ipn', 'location_code', 'quantity_on_hand', 'quantity_reserved', 'minimum_stock', 'currency', 'illustrative_unit_cost_usd', 'batch', 'status', 'supplier_part_key', 'expiry_date', 'financial_basis', 'receipt_status', 'source_identity', 'notes'],
            ['WB-STK-1', 'WB-CAT-X1', 'WB-WH-ELEC-B1', 3, 0, 2, 'USD', 650, 'WB-BATCH-1', 'OK', '', '', 'DEMO_ESTIMATE_NOT_VENDOR_QUOTE', 'PLANNED_SEED_NOT_POSTED', '2904602', ''],
        ],
        'Maintenance History': [
            ['history_id', 'machine_key', 'date', 'summary', 'details', 'performed_by', 'work_order_reference', 'history_truth', 'import_state', 'notes'],
            ['WB-MR-1', 'P01', '2025-01-18', 'Quarterly mechanical inspection', 'No abnormal noise.', 'Jordan Kim', '', 'INTERNAL_DEMO_DATA', 'STAGING_FOR_HISTORY_IMPORT', ''],
        ],
        'Work Orders': [
            ['work_order_key', 'machine_key', 'title', 'work_order_type', 'priority', 'fault_summary', 'symptom', 'criticality', 'production_impact', 'assignee', 'due_date', 'estimated_minutes', 'proposed_status', 'create_repair_packet', 'parts_list', 'scenario_id', 'notes'],
            ['WB-WO-1', 'P01', 'Pump 01 bearing vibration', 'corrective', 'high', 'Bearing wear', 'Vibration rising', 'high', 'Capacity', 'Jordan Kim', '2026-11-01', 120, 'DRAFT_ENTRY_NOT_CREATED', True, 'bearing', 'SC-01', ''],
            ['WB-WO-2', 'GEN', 'Generator monthly load test', 'preventive', 'medium', '', '', 'medium', '', 'Avery Patel', '2026-11-15', 60, 'PLANNED_ENTRY_NOT_CREATED', False, '', '', ''],
        ],
        'Staff Roles': [
            ['user_key', 'display_name', 'permission_state'],
            ['WB-USR-1', 'Maya Torres', 'CREATE_SANDBOX_USERS_IF_NEEDED'],
        ],
        'PM Plans': [
            ['plan_id', 'machine_key', 'task_title'],
            ['WB-PM-1', 'P01', 'Operator round'],
            ['WB-PM-2', 'P01', 'Bearing temperature review'],
        ],
    }


def write_workbook(directory, sheets):
    """Write the sheet dict to disk and return the path."""
    book = Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    path = Path(directory) / 'population.xlsx'
    book.save(path)
    return path


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
class WorkbookParserTests(TestCase):
    """The parser refuses anything the planner could not apply unambiguously."""

    def parse(self, mutate=None):
        sheets = default_sheets()
        if mutate:
            mutate(sheets)
        with TemporaryDirectory() as directory:
            return load_population_workbook(write_workbook(directory, sheets))

    def test_valid_workbook_summary(self):
        book = self.parse()
        self.assertEqual(book.client_codes, (CLIENT,))
        self.assertEqual(len(book.locations), 3)
        self.assertEqual(book.station.source_key, 'WB_ST')
        self.assertEqual([p.source_key for p in book.pumps], ['P1', 'P2'])
        self.assertEqual(len(book.equipment), 1)
        self.assertIn('P01', book.profiles)
        self.assertEqual(len(book.sha256), 64)
        # Codes are normalised to the lower-case form AssetLocation stores.
        self.assertEqual(book.locations[0].code, 'wb-site')
        self.assertEqual(book.pumps[0].location_code, 'wb-bay-01')

    def test_dangling_parent_rejected(self):
        def mutate(sheets):
            sheets['Facility Locations'][3][3] = 'NOPE'

        with self.assertRaisesRegex(WorkbookError, 'parent nope is not in the sheet'):
            self.parse(mutate)

    def test_root_without_timezone_rejected(self):
        def mutate(sheets):
            sheets['Facility Locations'][1][4] = None

        with self.assertRaisesRegex(WorkbookError, 'root location needs a timezone'):
            self.parse(mutate)

    def test_duplicate_machine_name_rejected(self):
        def mutate(sheets):
            sheets['Machine Records'][4][1] = 'Workbook Station / Pump 01'

        with self.assertRaisesRegex(WorkbookError, 'name duplicates row'):
            self.parse(mutate)

    def test_bad_pump_key_rejected(self):
        def mutate(sheets):
            sheets['Machine Records'][2][11] = 'PUMP1'

        with self.assertRaisesRegex(WorkbookError, 'P1..P9999'):
            self.parse(mutate)

    def test_machine_location_must_exist(self):
        def mutate(sheets):
            sheets['Machine Records'][4][3] = 'WB-MISSING'

        with self.assertRaisesRegex(WorkbookError, 'asset_location_code wb-missing'):
            self.parse(mutate)

    def test_profile_for_unknown_machine_rejected(self):
        def mutate(sheets):
            sheets['Machine Profiles'][1][0] = 'GHOST'

        with self.assertRaisesRegex(WorkbookError, 'GHOST is not in Machine Records'):
            self.parse(mutate)

    def test_missing_required_sheet(self):
        def mutate(sheets):
            del sheets['Machine Records']

        with self.assertRaisesRegex(WorkbookError, 'Missing sheet: Machine Records'):
            self.parse(mutate)

    def test_two_clients_rejected(self):
        def mutate(sheets):
            sheets['Machine Records'][4][10] = 'another'

        with self.assertRaisesRegex(WorkbookError, 'exactly one client_code'):
            self.parse(mutate)

    def test_component_ipn_must_be_in_catalogue(self):
        def mutate(sheets):
            sheets['Component Occurrences'][1][3] = 'WB-REF-P99'

        with self.assertRaisesRegex(WorkbookError, 'not in the catalogue: WB-REF-P99'):
            self.parse(mutate)

    def test_virtual_part_cannot_be_purchaseable(self):
        def mutate(sheets):
            sheets['Parts Catalogue'][1][8] = True

        with self.assertRaisesRegex(WorkbookError, 'cannot be purchaseable'):
            self.parse(mutate)

    def test_parts_sheet_parsed(self):
        book = self.parse()
        self.assertTrue(book.has_parts_sheet)
        self.assertEqual([p.ipn for p in book.parts], ['WB-REF-P01', 'WB-REF-P02', 'WB-CAT-X1'])
        self.assertEqual(book.parts[0].category, ('Workbook', 'Reference Equipment'))
        self.assertIn('engineering=ENGINEERED_REFERENCE_PENDING_REVIEW', book.components[0].review_note)
        self.assertEqual(book.referenced_ipns, {'WB-REF-P01', 'WB-REF-P02'})

    def test_operations_sheets_parsed(self):
        book = self.parse()
        self.assertEqual([s.code for s in book.stock_locations], ['wb-wh', 'wb-wh-elec', 'wb-wh-elec-b1'])
        self.assertEqual([c.name for c in book.companies], ['Phoenix Contact', 'Workbook Distributor'])
        self.assertFalse(book.offers[0].has_supplier_offer)
        self.assertEqual(book.stock_items[0].status, 10)
        self.assertIn('Illustrative unit cost 650 USD', book.stock_items[0].notes)
        self.assertEqual(str(book.history[0].date), '2025-01-18')
        self.assertEqual(
            [(w.key, w.target_status) for w in book.work_orders],
            [('WB-WO-1', 'draft'), ('WB-WO-2', 'planned')],
        )
        self.assertIn('Fault: Bearing wear', book.work_orders[0].description)
        self.assertEqual(book.manual, {'Staff Roles': 1, 'PM Plans': 2})

    def test_warehouse_code_cannot_shadow_a_physical_location(self):
        def mutate(sheets):
            sheets['Warehouse Locations'][3][0] = 'WB-BAY-01'
            sheets['Stock Items'][1][2] = 'WB-BAY-01'

        with self.assertRaisesRegex(WorkbookError, 'codes also used by Facility Locations'):
            self.parse(mutate)

    def test_stock_on_virtual_part_rejected(self):
        def mutate(sheets):
            sheets['Stock Items'][1][1] = 'WB-REF-P01'

        with self.assertRaisesRegex(WorkbookError, 'virtual reference part'):
            self.parse(mutate)

    def test_stock_in_structural_bin_rejected(self):
        def mutate(sheets):
            sheets['Stock Items'][1][2] = 'WB-WH-ELEC'

        with self.assertRaisesRegex(WorkbookError, 'structural and cannot hold stock'):
            self.parse(mutate)

    def test_history_cannot_claim_a_work_order(self):
        def mutate(sheets):
            sheets['Maintenance History'][1][6] = 'WO-000001'

        with self.assertRaisesRegex(WorkbookError, 'cannot reference a work order'):
            self.parse(mutate)

    def test_work_order_cannot_be_staged_past_planned(self):
        def mutate(sheets):
            sheets['Work Orders'][1][12] = 'completed'

        with self.assertRaisesRegex(WorkbookError, 'not a staging state'):
            self.parse(mutate)

    def test_offer_manufacturer_must_be_a_company(self):
        def mutate(sheets):
            sheets['Supplier Offers'][1][1] = 'Unknown Maker'

        with self.assertRaisesRegex(WorkbookError, 'is not a manufacturer in Companies'):
            self.parse(mutate)


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
class PopulationApplyTests(TestCase):
    """Writes are previewable, idempotent and update rather than duplicate."""

    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username='wb-operator', password=None
        )
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def book(self, mutate=None):
        sheets = default_sheets()
        if mutate:
            mutate(sheets)
        return load_population_workbook(write_workbook(self.directory.name, sheets))

    def test_plan_on_empty_database_needs_client_name(self):
        with self.assertRaisesRegex(PopulationError, 'pass --client-name'):
            plan_population(self.book())

    def test_plan_is_read_only(self):
        plan = plan_population(self.book(), client_name='Workbook Client')
        self.assertEqual(plan.client_action, 'create')
        self.assertEqual(plan.conflicts, [])
        self.assertEqual(plan.counts()['location'], {'create': 3})
        self.assertEqual(plan.counts()['station'], {'register': 1})
        self.assertEqual(plan.counts()['pump'], {'create': 2})
        self.assertEqual(plan.counts()['equipment'], {'create': 1})
        self.assertEqual(plan.counts()['category'], {'create': 3})
        self.assertEqual(plan.counts()['part'], {'create': 3})
        self.assertEqual(plan.counts()['component'], {'create': 2})
        self.assertEqual(plan.counts()['part_link'], {'create': 2})
        self.assertEqual(plan.blocked, {})
        self.assertFalse(Client.objects.filter(code=CLIENT).exists())
        self.assertEqual(AssetMachine.objects.count(), 0)

    def test_plan_without_catalogue_reports_blocked_parts(self):
        def mutate(sheets):
            del sheets['Parts Catalogue']

        plan = plan_population(self.book(mutate), client_name='Workbook Client')
        self.assertEqual(plan.blocked['parts_missing'], ['WB-REF-P01', 'WB-REF-P02'])
        self.assertNotIn('part', plan.counts())

    def test_dry_run_rolls_back_everything(self):
        report = apply_population(
            self.book(), self.actor, dry_run=True, client_name='Workbook Client'
        )
        self.assertTrue(report['dry_run'])
        self.assertEqual(report['locations']['created'], 3)
        self.assertEqual(report['pumps']['created'], 2)
        self.assertEqual(report['station']['name'], 'Workbook Station')
        self.assertFalse(Client.objects.filter(code=CLIENT).exists())
        self.assertEqual(AssetLocation.objects.count(), 0)
        self.assertEqual(AssetMachine.objects.count(), 0)
        self.assertEqual(MachineLocationTransfer.objects.count(), 0)

    def test_apply_then_replay_is_idempotent(self):
        first = apply_population(self.book(), self.actor, client_name='Workbook Client')
        self.assertFalse(first['dry_run'])
        client = Client.objects.get(code=CLIENT)
        self.assertEqual(client.name, 'Workbook Client')

        station = AssetMachine.objects.get(asset_type='pumphouse')
        self.assertEqual(str(station.source_entity_uuid), STATION_UUID)
        self.assertEqual(station.client, client)
        self.assertEqual(station.profile['criticality'], 'critical')
        pumps = list(station.children.order_by('source_key'))
        self.assertEqual([p.name for p in pumps], [
            'Workbook Station / Pump 01',
            'Workbook Station / Pump 02',
        ])
        self.assertEqual(pumps[0].description, 'Pump one')
        self.assertEqual(pumps[0].profile['components'][1]['parent_ref'], 'PUMP')
        self.assertEqual(pumps[0].profile['fault_codes'], ['WB-LOW-FLOW'])
        self.assertEqual(pumps[1].profile, {
            'criticality': 'high',
            'maintenance_strategy': 'predictive',
        })

        bay = AssetLocation.objects.get(client=client, code='wb-bay-01')
        self.assertEqual(bay.parent.code, 'wb-feps')
        self.assertEqual(bay.parent.parent.timezone, 'America/Chicago')
        self.assertEqual(pumps[0].physical_location, bay)
        generator = AssetMachine.objects.get(name='Workbook Standby Generator')
        self.assertEqual(generator.asset_type, 'equipment')
        self.assertEqual(generator.physical_location.code, 'wb-feps')
        self.assertEqual(first['placements']['moved'], 4)
        self.assertEqual(MachinePlacementHistory.objects.count(), 4)

        # Parts stage: categories, parts, draft components, aggregated links.
        from part.models import Part, PartCategory

        from assets.models import AssetComponent, MachinePart

        self.assertEqual(first['categories'], {'created': 3, 'existing': 0})
        self.assertEqual(first['parts'], {'created': 3, 'updated': 0, 'unchanged': 0})
        self.assertEqual(first['components']['created'], 2)
        self.assertEqual(first['part_links']['created'], 2)
        family = Part.objects.get(IPN='WB-REF-P01')
        self.assertTrue(family.virtual)
        self.assertFalse(family.purchaseable)
        self.assertEqual(family.category.pathstring, 'Workbook/Reference Equipment')
        self.assertEqual(PartCategory.objects.get(name='Workbook').children.count(), 2)
        candidate = Part.objects.get(IPN='WB-CAT-X1')
        self.assertTrue(candidate.purchaseable)
        self.assertEqual(candidate.minimum_stock, 2)
        self.assertEqual(candidate.link, 'https://example.invalid/2904602')
        occurrence = AssetComponent.objects.get(machine=pumps[0], code='PUMP-01')
        self.assertEqual(occurrence.part, family)
        self.assertEqual(occurrence.status, 'draft')
        self.assertIn('compatibility=NOT_APPROVED', occurrence.review_note)
        import_provenance = f'Workbook population {first["workbook_sha256"][:12]}'
        self.assertEqual(occurrence.provenance, import_provenance)
        imported_at = occurrence.created_at
        self.assertEqual(
            MachinePart.objects.get(machine=pumps[0], part=family).quantity, 1
        )

        second = apply_population(self.book(), self.actor)
        self.assertEqual(second['locations'], {'created': 0, 'updated': 0, 'unchanged': 3})
        self.assertEqual(second['pumps'], {'created': 0, 'existing': 2})
        self.assertEqual(second['equipment'], {'created': 0, 'existing': 1})
        self.assertEqual(second['details']['updated'], 0)
        self.assertEqual(second['placements']['moved'], 0)
        self.assertEqual(second['parts'], {'created': 0, 'updated': 0, 'unchanged': 3})
        self.assertEqual(second['components']['unchanged'], 2)
        self.assertEqual(second['part_links']['unchanged'], 2)
        self.assertEqual(Part.objects.filter(IPN__startswith='WB-').count(), 3)
        self.assertEqual(AssetComponent.objects.count(), 2)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.provenance, import_provenance)
        self.assertEqual(occurrence.created_at, imported_at)

    def test_existing_equipment_is_updated_not_duplicated(self):
        client = Client.objects.create(code=CLIENT, name='Pre-existing')
        machine = AssetMachine.objects.create(
            name='Workbook Standby Generator',
            client=client,
            description='old text',
            manufacturer='',
        )
        plan = plan_population(self.book())
        self.assertEqual(plan.counts()['equipment'], {'exists': 1})
        self.assertIn(('details', 'GEN', 'update'), [
            (s.kind, s.key, s.action) for s in plan.steps
        ])
        apply_population(self.book(), self.actor)
        machine.refresh_from_db()
        self.assertEqual(machine.description, 'Generator')
        self.assertEqual(machine.manufacturer, 'Caterpillar')
        self.assertEqual(machine.profile['criticality'], 'medium')
        self.assertEqual(AssetMachine.objects.filter(asset_type='equipment').count(), 1)

    def test_station_is_renamed_in_place_and_pumps_follow(self):
        apply_population(self.book(), self.actor, client_name='Workbook Client')
        station = AssetMachine.objects.get(asset_type='pumphouse')

        def mutate(sheets):
            sheets['Machine Records'][1][1] = 'Renamed Station'
            sheets['Machine Records'][2][1] = 'Renamed Station / Pump 01'
            sheets['Machine Records'][3][1] = 'Renamed Station / Pump 02'

        plan = plan_population(self.book(mutate))
        self.assertEqual(plan.counts()['station'], {'rename': 1})
        apply_population(self.book(mutate), self.actor)
        station.refresh_from_db()
        self.assertEqual(station.name, 'Renamed Station')
        self.assertEqual(
            sorted(station.children.values_list('name', flat=True)),
            ['Renamed Station / Pump 01', 'Renamed Station / Pump 02'],
        )
        self.assertEqual(AssetMachine.objects.filter(asset_type='pumphouse').count(), 1)

    def test_identity_conflict_blocks_apply(self):
        apply_population(self.book(), self.actor, client_name='Workbook Client')

        def mutate(sheets):
            sheets['Machine Records'][1][11] = 'OTHER_KEY'

        plan = plan_population(self.book(mutate))
        self.assertTrue(any('identity is immutable' in c for c in plan.conflicts))
        with self.assertRaisesRegex(PopulationError, 'Refusing to apply'):
            apply_population(self.book(mutate), self.actor)

    def test_invalid_profile_is_a_conflict_not_a_crash(self):
        def mutate(sheets):
            sheets['Machine Profiles'][1][4] = json.dumps(['antimatter'])

        plan = plan_population(self.book(mutate), client_name='Workbook Client')
        self.assertTrue(any('P01: profile invalid' in c for c in plan.conflicts))

    def test_moving_a_machine_creates_new_history_interval(self):
        apply_population(self.book(), self.actor, client_name='Workbook Client')
        generator = AssetMachine.objects.get(name='Workbook Standby Generator')
        before = generator.placement_version

        def mutate(sheets):
            sheets['Machine Records'][4][3] = 'WB-BAY-01'

        report = apply_population(self.book(mutate), self.actor)
        generator.refresh_from_db()
        self.assertEqual(report['placements']['moved'], 1)
        self.assertEqual(generator.physical_location.code, 'wb-bay-01')
        self.assertEqual(generator.placement_version, before + 1)
        self.assertEqual(
            MachinePlacementHistory.objects.filter(machine=generator).count(), 2
        )

    def test_reserved_station_uuid_is_kept_and_guarded(self):
        reserved = uuid4()
        apply_population(
            self.book(), self.actor, client_name='Workbook Client', station_uuid=reserved
        )
        station = AssetMachine.objects.get(asset_type='pumphouse')
        self.assertEqual(station.uuid, reserved)
        # Replaying with the same reservation is a no-op; a different one is refused.
        plan = plan_population(self.book(), station_uuid=reserved)
        self.assertEqual(plan.conflicts, [])
        plan = plan_population(self.book(), station_uuid=uuid4())
        self.assertTrue(any('Public UUIDs are immutable' in c for c in plan.conflicts))

    def test_component_bound_to_other_part_is_a_conflict(self):
        apply_population(self.book(), self.actor, client_name='Workbook Client')

        def mutate(sheets):
            # Point PUMP-01 at the motor family instead.
            sheets['Component Occurrences'][1][3] = 'WB-REF-P02'

        plan = plan_population(self.book(mutate))
        self.assertTrue(any('is bound to part' in c for c in plan.conflicts))
        with self.assertRaisesRegex(PopulationError, 'Refusing to apply'):
            apply_population(self.book(mutate), self.actor)

    def test_part_link_quantity_update_and_part_field_update(self):
        apply_population(self.book(), self.actor, client_name='Workbook Client')

        def mutate(sheets):
            sheets['Machine Part Links'][1][2] = 3
            sheets['Parts Catalogue'][1][3] = 'Revised description.'

        plan = plan_population(self.book(mutate))
        self.assertEqual(plan.counts()['part_link'], {'update': 1, 'unchanged': 1})
        self.assertEqual(plan.counts()['part'], {'update': 1, 'unchanged': 2})
        report = apply_population(self.book(mutate), self.actor)
        self.assertEqual(report['part_links']['updated'], 1)
        self.assertEqual(report['parts']['updated'], 1)
        from part.models import Part

        from assets.models import MachinePart

        self.assertEqual(Part.objects.get(IPN='WB-REF-P01').description, 'Revised description.')
        self.assertEqual(
            MachinePart.objects.get(part__IPN='WB-REF-P01').quantity, 3
        )


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
class AdoptExistingStationTests(TestCase):
    """A plant registered from its source first can be enriched from a workbook.

    The deployed case: the station and its pump slots were registered under
    client ``internal`` with the telemetry source's identity, and the
    workbook arrives later carrying a provisional identity and its own
    client code. ``station_pk`` and ``client_code`` bind the workbook to the
    live rows instead of creating a duplicate plant nobody can see.
    """

    def setUp(self):
        from assets.registry import ensure_pump, register_station

        self.actor = get_user_model().objects.create_superuser(
            username='wb-adopter', password=None
        )
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client_row, _ = Client.objects.get_or_create(
            code='internal', defaults={'name': 'Internal'}
        )
        self.station = register_station(
            client=self.client_row,
            name='Live Station',
            source_namespace='klsw',
            source_entity_uuid=uuid4(),
            source_key='PH_9',
        )
        self.live_pumps = [ensure_pump(self.station, key) for key in ('P1', 'P2')]

    def book(self, mutate=None):
        sheets = default_sheets()
        if mutate:
            mutate(sheets)
        return load_population_workbook(write_workbook(self.directory.name, sheets))

    def test_without_adoption_plan_would_register_a_second_station(self):
        plan = plan_population(self.book(), client_name='Workbook Client')
        self.assertEqual(plan.client_action, 'create')
        self.assertEqual(plan.counts()['station'], {'register': 1})

    def test_client_code_must_name_an_existing_client(self):
        with self.assertRaisesRegex(PopulationError, 'must name an existing client'):
            plan_population(self.book(), client_code='nope', client_name='Nope')
        self.assertFalse(Client.objects.filter(code='nope').exists())

    def test_station_pk_must_be_a_registered_station(self):
        with self.assertRaisesRegex(PopulationError, 'No machine with id'):
            plan_population(self.book(), client_code='internal', station_pk=999999)
        equipment = AssetMachine.objects.create(
            name='Just Equipment', client=self.client_row, asset_type='equipment'
        )
        with self.assertRaisesRegex(PopulationError, 'not a registered station'):
            plan_population(
                self.book(), client_code='internal', station_pk=equipment.pk
            )

    def test_adopted_station_of_another_client_is_a_conflict(self):
        other = Client.objects.create(code='other', name='Other')
        plan = plan_population(self.book(), client_code='other', station_pk=self.station.pk)
        self.assertTrue(any('belongs to another client' in c for c in plan.conflicts))
        self.assertEqual(other.pk, Client.objects.get(code='other').pk)

    def test_adopt_keeps_identity_renames_and_reuses_pumps(self):
        plan = plan_population(
            self.book(), client_code='internal', station_pk=self.station.pk
        )
        self.assertEqual(plan.conflicts, [])
        self.assertEqual(plan.client_code, 'internal')
        self.assertEqual(plan.client_action, 'exists')
        self.assertEqual(plan.counts()['station'], {'rename': 1})
        self.assertEqual(plan.counts()['pump'], {'exists': 2})
        self.assertTrue(
            any('keeps its registered identity' in w for w in plan.warnings)
        )

        report = apply_population(
            self.book(), self.actor, client_code='internal', station_pk=self.station.pk
        )
        self.assertTrue(report['station']['adopted'])
        self.assertEqual(report['client']['code'], 'internal')
        self.assertEqual(report['pumps'], {'created': 0, 'existing': 2})
        self.assertFalse(Client.objects.filter(code=CLIENT).exists())
        self.assertEqual(AssetMachine.objects.filter(asset_type='pumphouse').count(), 1)

        self.station.refresh_from_db()
        self.assertEqual(self.station.name, 'Workbook Station')
        self.assertEqual(self.station.source_namespace, 'klsw')
        self.assertEqual(self.station.source_key, 'PH_9')
        self.assertEqual(self.station.profile['criticality'], 'critical')
        pumps = list(self.station.children.order_by('source_key'))
        self.assertEqual([p.pk for p in pumps], [p.pk for p in self.live_pumps])
        self.assertEqual(
            [p.name for p in pumps],
            ['Workbook Station / Pump 01', 'Workbook Station / Pump 02'],
        )
        self.assertEqual(pumps[0].description, 'Pump one')
        self.assertEqual(pumps[0].physical_location.code, 'wb-bay-01')
        self.assertEqual(pumps[0].physical_location.client, self.client_row)
        generator = AssetMachine.objects.get(name='Workbook Standby Generator')
        self.assertEqual(generator.client, self.client_row)

        from assets.models import AssetComponent

        self.assertEqual(AssetComponent.objects.filter(machine=pumps[0]).count(), 2)

        replay = apply_population(
            self.book(), self.actor, client_code='internal', station_pk=self.station.pk
        )
        self.assertEqual(replay['details']['updated'], 0)
        self.assertEqual(replay['placements']['moved'], 0)
        self.assertEqual(replay['components']['unchanged'], 2)

    def test_adopt_leaves_prior_components_alone(self):
        from part.models import Part

        from assets.models import AssetComponent

        crosswalk = Part.objects.create(name='PS Motor', IPN='PS-MOTOR', virtual=True)
        prior = AssetComponent.objects.create(
            machine=self.live_pumps[0],
            part=crosswalk,
            code='PS-MOTOR:1',
            name='Electric Motor',
            provenance='dictionary review',
        )
        apply_population(
            self.book(), self.actor, client_code='internal', station_pk=self.station.pk
        )
        prior.refresh_from_db()
        self.assertEqual(prior.part, crosswalk)
        codes = set(
            AssetComponent.objects.filter(machine=self.live_pumps[0]).values_list(
                'code', flat=True
            )
        )
        self.assertEqual(codes, {'PS-MOTOR:1', 'PUMP-01', 'MOTOR-01'})


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
class OperationsStageTests(TestCase):
    """Warehouse, companies, offers, stock, history and staged jobs follow the plant."""

    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username='wb-ops', password=None
        )
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def book(self, mutate=None):
        sheets = default_sheets()
        if mutate:
            mutate(sheets)
        return load_population_workbook(write_workbook(self.directory.name, sheets))

    def test_plan_lists_operations_and_manual_work(self):
        plan = plan_population(self.book(), client_name='Workbook Client')
        counts = plan.counts()
        self.assertEqual(counts['stock_location'], {'create': 3})
        self.assertEqual(counts['company'], {'create': 2})
        self.assertEqual(counts['manufacturer_part'], {'create': 1})
        self.assertNotIn('supplier_part', counts)
        self.assertEqual(counts['stock_item'], {'create': 1})
        self.assertEqual(counts['history'], {'create': 1})
        self.assertEqual(counts['work_order'], {'create': 2})
        self.assertEqual(
            set(plan.manual),
            {'staff_roles', 'pm_plans', 'repair_packets', 'supplier_offers'},
        )
        self.assertEqual(plan.manual['pm_plans']['rows'], 2)
        self.assertEqual(plan.manual['repair_packets']['rows'], 1)

    def test_apply_creates_operations_and_replay_is_a_no_op(self):
        from company.models import Company, ManufacturerPart, SupplierPart
        from stock.models import StockItem, StockLocation
        from tasks.models import WorkOrder

        from assets.models import AssetMaintenanceRecord

        first = apply_population(self.book(), self.actor, client_name='Workbook Client')
        self.assertEqual(first['stock_locations'], {'created': 3, 'updated': 0, 'unchanged': 0})
        self.assertEqual(first['companies'], {'created': 2, 'updated': 0, 'unchanged': 0})
        self.assertEqual(first['manufacturer_parts'], {'created': 1, 'existing': 0, 'skipped': 0})
        self.assertEqual(first['supplier_parts'], {'created': 0, 'existing': 0, 'blocked': 1})
        self.assertEqual(first['stock_items'], {'created': 1, 'existing': 0, 'skipped': 0})
        self.assertEqual(first['history'], {'created': 1, 'updated': 0, 'unchanged': 0, 'skipped': 0})
        self.assertEqual(first['work_orders'], {'created': 2, 'planned': 1, 'existing': 0, 'skipped': 0})
        self.assertEqual([w for w in first['warnings'] if 'left in draft' in w], [])

        bin_ = StockLocation.objects.get(name='Electrical Bin B1')
        self.assertEqual(bin_.parent.name, 'Electrical Spares')
        self.assertTrue(bin_.parent.structural)
        self.assertFalse(bin_.structural)
        self.assertEqual(bin_.parent.parent.pathstring, 'Workbook Spares Warehouse')

        maker = Company.objects.get(name='Phoenix Contact')
        self.assertTrue(maker.is_manufacturer)
        self.assertFalse(maker.is_supplier)
        manufacturer_part = ManufacturerPart.objects.get(MPN='2904602')
        self.assertEqual(manufacturer_part.manufacturer, maker)
        self.assertEqual(manufacturer_part.part.IPN, 'WB-CAT-X1')
        self.assertEqual(SupplierPart.objects.count(), 0)

        stock = StockItem.objects.get(batch='WB-BATCH-1')
        self.assertEqual(stock.location, bin_)
        self.assertEqual(int(stock.quantity), 3)
        self.assertEqual(stock.part.IPN, 'WB-CAT-X1')
        self.assertIsNone(stock.purchase_price)
        self.assertTrue(
            stock.notes.filter(content__contains='DEMO_ESTIMATE_NOT_VENDOR_QUOTE').exists()
        )
        self.assertEqual(stock.tracking_info.count(), 1)

        pump = AssetMachine.objects.get(source_key='P1')
        entry = AssetMaintenanceRecord.objects.get(machine=pump)
        self.assertEqual(str(entry.date), '2025-01-18')
        self.assertEqual(entry.performed_by, 'Jordan Kim')
        self.assertIsNone(entry.work_order)

        draft = WorkOrder.objects.get(job_number='WB-WO-1')
        self.assertEqual(draft.lifecycle_status, 'draft')
        self.assertEqual(draft.machine, pump)
        self.assertEqual(draft.work_order_type, 'corrective')
        self.assertEqual(draft.priority, 'high')
        self.assertEqual(draft.assignee, 'Jordan Kim')
        self.assertIsNone(draft.assigned_to)
        self.assertEqual(draft.requested_by, self.actor)
        self.assertIn('SC-01', draft.tags)
        self.assertTrue(draft.reference.startswith('WO-'))
        self.assertIsNotNone(draft.primary_card)
        planned = WorkOrder.objects.get(job_number='WB-WO-2')
        self.assertEqual(planned.lifecycle_status, 'planned')
        self.assertEqual(planned.lifecycle_version, 2)
        self.assertEqual(planned.machine.name, 'Workbook Standby Generator')

        second = apply_population(self.book(), self.actor)
        self.assertEqual(second['stock_locations'], {'created': 0, 'updated': 0, 'unchanged': 3})
        self.assertEqual(second['companies'], {'created': 0, 'updated': 0, 'unchanged': 2})
        self.assertEqual(second['manufacturer_parts']['existing'], 1)
        self.assertEqual(second['stock_items'], {'created': 0, 'existing': 1, 'skipped': 0})
        self.assertEqual(second['history']['unchanged'], 1)
        self.assertEqual(second['work_orders'], {'created': 0, 'planned': 0, 'existing': 2, 'skipped': 0})
        self.assertEqual(StockLocation.objects.count(), 3)
        self.assertEqual(Company.objects.filter(name='Phoenix Contact').count(), 1)
        self.assertEqual(StockItem.objects.count(), 1)
        self.assertEqual(AssetMaintenanceRecord.objects.count(), 1)
        self.assertEqual(WorkOrder.objects.count(), 2)
        self.assertEqual(int(StockItem.objects.get().quantity), 3)

    def test_dry_run_leaves_no_operations_rows(self):
        from company.models import Company
        from stock.models import StockItem, StockLocation
        from tasks.models import WorkOrder

        report = apply_population(
            self.book(), self.actor, dry_run=True, client_name='Workbook Client'
        )
        self.assertEqual(report['work_orders']['created'], 2)
        self.assertEqual(StockLocation.objects.count(), 0)
        self.assertEqual(Company.objects.filter(name='Phoenix Contact').count(), 0)
        self.assertEqual(StockItem.objects.count(), 0)
        self.assertEqual(WorkOrder.objects.count(), 0)

    def test_existing_work_order_is_not_overwritten(self):
        from tasks.models import WorkOrder

        apply_population(self.book(), self.actor, client_name='Workbook Client')
        work_order = WorkOrder.objects.get(job_number='WB-WO-1')
        work_order.title = 'Operator edited title'
        work_order.save(update_fields=['title'])

        def mutate(sheets):
            sheets['Work Orders'][1][2] = 'Workbook changed title'

        plan = plan_population(self.book(mutate))
        self.assertEqual(plan.counts()['work_order'], {'exists': 2})
        apply_population(self.book(mutate), self.actor)
        work_order.refresh_from_db()
        self.assertEqual(work_order.title, 'Operator edited title')

    def test_supplier_offer_is_created_only_with_supplier_and_sku(self):
        from company.models import SupplierPart

        def mutate(sheets):
            sheets['Supplier Offers'][1][3] = 'Workbook Distributor'
            sheets['Supplier Offers'][1][4] = 'DIST-2904602'

        plan = plan_population(self.book(mutate), client_name='Workbook Client')
        self.assertEqual(plan.counts()['supplier_part'], {'create': 1})
        self.assertNotIn('supplier_offers', plan.manual)
        report = apply_population(self.book(mutate), self.actor, client_name='Workbook Client')
        self.assertEqual(report['supplier_parts'], {'created': 1, 'existing': 0, 'blocked': 0})
        offer = SupplierPart.objects.get(SKU='DIST-2904602')
        self.assertEqual(offer.supplier.name, 'Workbook Distributor')
        self.assertEqual(offer.manufacturer_part.MPN, '2904602')


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None)
class PopulateCommandTests(TestCase):
    """The command defaults to plan and gates --apply behind a backup reference."""

    def setUp(self):
        get_user_model().objects.create_superuser(username='wb-cli', password=None)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = write_workbook(self.directory.name, default_sheets())

    def run_command(self, *args):
        out = StringIO()
        call_command('populate_from_workbook', str(self.path), *args, stdout=out)
        return out.getvalue()

    def test_default_mode_is_plan_and_writes_nothing(self):
        output = self.run_command('--client-name', 'Workbook Client', '--json')
        payload = json.loads(output)
        self.assertEqual(payload['mode'], 'plan')
        self.assertEqual(payload['client_action'], 'create')
        self.assertEqual(AssetMachine.objects.count(), 0)

    def test_dry_run_requires_actor(self):
        with self.assertRaisesRegex(CommandError, '--actor is required'):
            self.run_command('--dry-run', '--client-name', 'Workbook Client')

    def test_apply_requires_backup_reference(self):
        with self.assertRaisesRegex(CommandError, '--backup-ref'):
            self.run_command('--apply', '--actor', 'wb-cli', '--client-name', 'X')
        self.assertEqual(AssetMachine.objects.count(), 0)

    def test_apply_refuses_remote_host_without_flag(self):
        with patch(
            'assets.management.commands.populate_from_workbook.database_host',
            return_value='example.postgres.database.azure.com',
        ):
            with self.assertRaisesRegex(CommandError, '--allow-remote'):
                self.run_command(
                    '--apply',
                    '--actor',
                    'wb-cli',
                    '--client-name',
                    'X',
                    '--backup-ref',
                    'bk-1',
                )
        self.assertEqual(AssetMachine.objects.count(), 0)

    def test_dry_run_then_apply_via_command(self):
        preview = json.loads(
            self.run_command(
                '--dry-run', '--actor', 'wb-cli', '--client-name', 'Workbook Client', '--json'
            )
        )
        self.assertEqual(preview['mode'], 'dry-run')
        self.assertEqual(AssetMachine.objects.count(), 0)
        applied = json.loads(
            self.run_command(
                '--apply',
                '--actor',
                'wb-cli',
                '--client-name',
                'Workbook Client',
                '--backup-ref',
                'local-test',
                '--allow-remote',
                '--json',
            )
        )
        self.assertEqual(applied['mode'], 'apply')
        self.assertEqual(applied['backup_ref'], 'local-test')
        self.assertEqual(AssetMachine.objects.count(), 4)
        self.assertEqual(applied['parts']['created'], 3)

    def test_invalid_workbook_is_a_command_error(self):
        sheets = default_sheets()
        del sheets['Facility Locations']
        path = write_workbook(self.directory.name, sheets)
        with self.assertRaisesRegex(CommandError, 'Workbook rejected'):
            call_command('populate_from_workbook', str(path), stdout=StringIO())

    def test_adopt_flags_reach_the_planner(self):
        from assets.registry import register_station

        internal, _ = Client.objects.get_or_create(
            code='internal', defaults={'name': 'Internal'}
        )
        station = register_station(
            client=internal,
            name='Live Station',
            source_namespace='klsw',
            source_entity_uuid=uuid4(),
            source_key='PH_9',
        )
        payload = json.loads(
            self.run_command(
                '--client-code', 'internal', '--station', str(station.pk), '--json'
            )
        )
        self.assertEqual(payload['client_code'], 'internal')
        self.assertEqual(payload['client_action'], 'exists')
        self.assertEqual(payload['counts']['station'], {'rename': 1})
        self.assertEqual(payload['conflicts'], [])
        self.assertEqual(AssetMachine.objects.filter(asset_type='pumphouse').count(), 1)
