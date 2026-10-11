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
            COMPONENT_HEADER,
            ['P01', 'PUMP-01', 'Pump - Train 01', 'WB-REF-P01', 1],
        ],
        'Machine Part Links': [LINK_HEADER, ['P01', 'WB-REF-P01', 1, 'reference']],
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
        self.assertEqual(plan.blocked['parts_missing'], ['WB-REF-P01'])
        self.assertFalse(Client.objects.filter(code=CLIENT).exists())
        self.assertEqual(AssetMachine.objects.count(), 0)

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

        second = apply_population(self.book(), self.actor)
        self.assertEqual(second['locations'], {'created': 0, 'updated': 0, 'unchanged': 3})
        self.assertEqual(second['pumps'], {'created': 0, 'existing': 2})
        self.assertEqual(second['equipment'], {'created': 0, 'existing': 1})
        self.assertEqual(second['details']['updated'], 0)
        self.assertEqual(second['placements']['moved'], 0)
        self.assertEqual(AssetMachine.objects.count(), 4)
        self.assertEqual(MachineLocationTransfer.objects.count(), first['placements']['transfers'])
        self.assertEqual(
            AssetMachine.objects.get(pk=station.pk).uuid, station.uuid
        )

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

    def test_invalid_workbook_is_a_command_error(self):
        sheets = default_sheets()
        del sheets['Facility Locations']
        path = write_workbook(self.directory.name, sheets)
        with self.assertRaisesRegex(CommandError, 'Workbook rejected'):
            call_command('populate_from_workbook', str(path), stdout=StringIO())
