"""Strict direct-core read outcomes and source-fault propagation controls."""

from integration_state.tests.assertions import assert_literal_bool

from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


class CoreOutcomeTests(TestCase):
    """Pure direct-record outcomes never stand for a complete population."""

    def test_direct_core_outcome_distinguishes_available_from_nondisclosing_unavailable(
        self,
    ):
        """An absent/denied record is not an empty successful snapshot."""
        from assets import integration_contracts as contracts

        make = getattr(contracts, 'source_read_outcome', None)
        self.assertTrue(callable(make), 'A typed direct-core outcome is required')
        assert callable(make)
        record = contracts.MachineSourceRecord(
            contracts.MachineSourceIdentity(' s1-source ', 1),
            's1-sensitive-outcome-name',
            True,
        )
        result = make(record)
        self.assertEqual(result.schema_version, 'aimms.source.read-result.v1')
        self.assertEqual(result.state, 'available')
        self.assertIs(result.record, record)
        assert_literal_bool(self, result.population_complete, False)
        assert_literal_bool(self, result.revision_complete, False)
        self.assertNotIn('s1-sensitive-outcome-name', repr((result,)))
        unavailable = make(None)
        self.assertEqual(unavailable.state, 'unavailable')
        self.assertIsNone(unavailable.record)
        assert_literal_bool(self, unavailable.population_complete, False)
        assert_literal_bool(self, unavailable.revision_complete, False)
        for value in ([], {}, object()):
            with self.subTest(value_type=type(value)), self.assertRaises(ValueError):
                make(value)

    def test_outcome_rejects_inconsistent_states_and_malformed_known_records(self):
        """Known class labels cannot bless bad identity/scalar/schema payloads."""
        from assets.integration_contracts import (
            LocationSourceIdentity,
            MachineProfileSourceRecord,
            MachineSourceIdentity,
            MachineSourceRecord,
            SourceReadOutcome,
            source_read_outcome,
        )

        class PretendsEqual(str):
            def __eq__(self, other):
                return True

            __hash__ = None

        identity = MachineSourceIdentity('s1-test', 1)
        record = MachineSourceRecord(identity, 's1-name', True)
        for state, body in (
            ('available', None),
            ('unavailable', record),
            ('unknown', None),
            (True, None),
            (PretendsEqual('available'), record),
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                SourceReadOutcome(state, body)
        for field, value in (
            ('identity', LocationSourceIdentity('s1-test', 1)),
            ('name', None),
            ('active', 1),
            ('schema_version', 'unrecognized-version'),
            ('schema_version', PretendsEqual('aimms.machine.basic.v1')),
        ):
            broken = deepcopy(record)
            object.__setattr__(broken, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                source_read_outcome(broken)
        invalid_profile = MachineProfileSourceRecord(
            identity, 'invalid_withheld', {'s1-private': 'must-not-be-valid'}
        )
        with self.assertRaises(ValueError):
            source_read_outcome(invalid_profile)
        self.assertEqual(source_read_outcome(record).state, 'available')

    def test_new_direct_contracts_refuse_missing_or_coerced_native_coordinates(self):
        """New constructor types preserve real blanks/nulls, not bool/int coercion."""
        from uuid import UUID

        from assets.integration_contracts import (
            ClientSourceIdentity,
            ClientStructureSourceRecord,
            MachineBarcodeSourceRecord,
            MachineRegistrationSourceRecord,
            MachineSourceIdentity,
        )
        from assets.integration_machine_location import MachinePlacementSourceRecord

        machine = MachineSourceIdentity('s1-test', 1)
        client = ClientSourceIdentity('s1-test', 1)
        samples = (
            (
                ClientStructureSourceRecord,
                {'identity': client, 'location_revision': 0},
                (
                    {'identity': machine},
                    {'location_revision': True},
                    {'location_revision': None},
                    {'location_revision': -1},
                ),
            ),
            (
                MachineRegistrationSourceRecord,
                {
                    'identity': machine,
                    'uuid': UUID('12345678-1234-5678-1234-567812345678'),
                    'source_namespace': '',
                    'source_entity_uuid': None,
                    'source_key': '',
                },
                (
                    {'identity': client},
                    {'uuid': None},
                    {'uuid': 'not-a-uuid'},
                    {'source_namespace': None},
                    {'source_entity_uuid': True},
                    {'source_key': None},
                ),
            ),
            (
                MachineBarcodeSourceRecord,
                {
                    'identity': machine,
                    'barcode_hash': '',
                    'api_url': '/api/',
                    'web_url': '/web/',
                },
                ({'identity': client}, {'barcode_hash': None}, {'api_url': None}),
            ),
            (
                MachinePlacementSourceRecord,
                {
                    'machine': machine,
                    'state': 'unassigned',
                    'location': None,
                    'placement_version': 0,
                },
                (
                    {'placement_version': True},
                    {'placement_version': None},
                    {'placement_version': -1},
                ),
            ),
        )
        for constructor, valid, invalid in samples:
            self.assertIsNotNone(constructor(**valid))
            for changed in invalid:
                with self.subTest(constructor=constructor.__name__, changed=changed):
                    with self.assertRaises(ValueError):
                        constructor(**(valid | changed))

    def test_every_direct_core_record_has_a_supported_noncomplete_outcome(self):
        """Explicitly admitted versions preserve native blanks/nulls/timestamps."""
        from datetime import datetime, timezone
        from uuid import UUID

        from assets import integration_contracts as c

        client = c.ClientSourceIdentity('s1-test', 1)
        location = c.LocationSourceIdentity('s1-test', 1)
        machine = c.MachineSourceIdentity('s1-test', 1)
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
        records = (
            c.ClientSourceRecord(client, 'client', '', True),
            c.ClientDetailsSourceRecord(
                client, 'client', '', True, timestamp, timestamp
            ),
            c.ClientStructureSourceRecord(client, 0),
            c.LocationSourceRecord(location, 'location', True),
            c.LocationDetailsSourceRecord(
                location, 'location', '', 'site', '', True, 1
            ),
            c.LocationPropertiesSourceRecord(
                location, 'location', '', 'site', '', '', True, 1, timestamp, timestamp
            ),
            c.MachineSourceRecord(machine, 'machine', False),
            c.MachineDetailsSourceRecord(
                machine,
                'machine',
                '',
                False,
                'equipment',
                '',
                '',
                '',
                '',
                timestamp,
                timestamp,
            ),
            c.MachineRegistrationSourceRecord(
                machine, UUID('12345678-1234-5678-1234-567812345678'), '', None, ''
            ),
            c.MachineBarcodeSourceRecord(machine, '', '/api/', '/web/'),
            c.MachineProfileSourceRecord(machine, 'valid', {}),
            c.MachineProfileSourceRecord(machine, 'invalid_withheld', None),
        )
        for record in records:
            with self.subTest(schema=record.schema_version):
                result = c.source_read_outcome(record)
                self.assertIs(result.record, record)
                self.assertEqual(result.state, 'available')
                assert_literal_bool(self, result.population_complete, False)
                assert_literal_bool(self, result.revision_complete, False)

    def test_source_faults_propagate_unchanged_instead_of_empty_success(self):
        """Fault injection replaces only the source seam, never an authority proof."""
        from django.db import OperationalError

        from assets import integration_sources as sources
        from assets.integration_contracts import source_read_outcome

        actor = SimpleNamespace(pk=1, is_authenticated=True)
        readers = (
            (sources.read_client, {'client_pk': 1}, '_authorized_client'),
            (sources.read_client_details, {'client_pk': 1}, '_authorized_client'),
            (sources.read_client_revision, {'client_pk': 1}, '_authorized_client'),
            (sources.read_location, {'location_pk': 1}, '_authorized_location'),
            (sources.read_location_details, {'location_pk': 1}, '_authorized_location'),
            (
                sources.read_location_properties,
                {'location_pk': 1},
                '_authorized_location',
            ),
            (sources.read_machine, {'machine_pk': 1}, '_authorized_machine_source'),
            (
                sources.read_machine_details,
                {'machine_pk': 1},
                '_authorized_machine_source',
            ),
            (
                sources.read_machine_profile,
                {'machine_pk': 1},
                '_authorized_machine_source',
            ),
            (
                sources.read_machine_registration,
                {'machine_pk': 1},
                '_authorized_machine_source',
            ),
            (
                sources.read_machine_barcode,
                {'machine_pk': 1},
                '_authorized_machine_source',
            ),
        )
        for reader, args, seam in readers:
            for fault in (
                OperationalError('s1 synthetic DB fault'),
                TimeoutError('s1 synthetic timeout'),
            ):
                with self.subTest(reader=reader.__name__, fault=type(fault).__name__):
                    with patch.object(sources, seam, side_effect=fault):
                        with self.assertRaises(type(fault)) as raised:
                            source_read_outcome(
                                reader(actor, environment='s1-test', **args)
                            )
                        self.assertIs(raised.exception, fault)
            with patch.object(sources, seam, return_value=None):
                self.assertEqual(
                    source_read_outcome(
                        reader(actor, environment='s1-test', **args)
                    ).state,
                    'unavailable',
                )

    def test_edge_and_hierarchy_faults_remain_faults_not_roots_or_null_placements(self):
        """Known unavailable edges and operational faults are different results."""
        from assets import integration_location_parents as locations
        from assets import integration_machine_client as clients
        from assets import integration_machine_location as placements
        from assets import integration_machine_parent as parents

        actor = SimpleNamespace(pk=1, is_authenticated=True)
        surfaces = (
            (
                locations.read_location_client,
                locations,
                '_authorized_location',
                'location_pk',
            ),
            (
                locations.read_location_parent,
                locations,
                '_authorized_location',
                'location_pk',
            ),
            (
                locations.read_location_hierarchy,
                locations,
                '_authorized_location',
                'location_pk',
            ),
            (
                clients.read_machine_client,
                clients,
                '_authorized_machine_source',
                'machine_pk',
            ),
            (
                parents.read_machine_parent,
                parents,
                '_authorized_machine_source',
                'machine_pk',
            ),
            (
                placements.read_machine_location,
                placements,
                '_authorized_machine',
                'machine_pk',
            ),
            (
                placements.read_machine_placement,
                placements,
                '_authorized_machine',
                'machine_pk',
            ),
        )
        for reader, module, seam, key in surfaces:
            fault = TimeoutError('s1 synthetic edge timeout')
            with self.subTest(reader=reader.__name__):
                with patch.object(module, seam, side_effect=fault):
                    with self.assertRaises(TimeoutError) as raised:
                        reader(actor, environment='s1-test', **{key: 1})
                    self.assertIs(raised.exception, fault)
                with patch.object(module, seam, return_value=None):
                    self.assertIsNone(reader(actor, environment='s1-test', **{key: 1}))
