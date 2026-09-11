"""Offline consistency tests for the PH_3 mapping proposal, not an ingest adapter."""

import json
import re
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parent.parent


class MappingDraftTests(unittest.TestCase):
    """Validate identifiers and catalogue references without Django or Cassandra."""

    @classmethod
    def setUpClass(cls):
        """Load the proposed mapping and expand the existing catalogue channels."""
        cls.draft = json.loads(
            (DIRECTORY / 'PH_3.mapping.draft.json').read_text(encoding='utf-8')
        )
        catalogue = json.loads(
            (
                ROOT / 'src/backend/InvenTree/part/catalogues/pump_systems.json'
            ).read_text(encoding='utf-8')
        )
        cls.catalogue = {}
        for component in catalogue['components']:
            for parameter in component['parameters']:
                channels = parameter.get('channels')
                tags = (
                    [
                        parameter['tag'].format(channel=n)
                        for n in range(channels[0], channels[1] + 1)
                    ]
                    if channels
                    else [parameter['tag']]
                )
                for tag in tags:
                    if tag in cls.catalogue:
                        raise ValueError(f'Ambiguous catalogue tag: {tag}')
                    cls.catalogue[tag] = component['code']

    def candidate(self, raw_tag):
        """Exercise the proposed matching order on representative sample keys."""
        prefix = re.match(self.draft['mapping_policy']['pump_prefix_pattern'], raw_tag)
        if not prefix:
            if raw_tag == 'COMMAN_FORBAY_LEVEL':
                return raw_tag, 'FOREBAY'
            return None
        pump_key = f'P{prefix.group("number")}'
        if pump_key not in {p['source_key'] for p in self.draft['pumps']}:
            return None
        local_tag = raw_tag[prefix.end() :]
        if local_tag in self.catalogue:
            return local_tag, self.catalogue[local_tag]
        matches = []
        for alias in self.draft['aliases']:
            match = re.fullmatch(alias['local_pattern'], local_tag)
            if match:
                target = alias['catalogue_tag'].format(**match.groupdict())
                self.assertEqual(self.catalogue[target], alias['family_code'])
                matches.append((target, alias['family_code']))
        self.assertLessEqual(len(matches), 1, 'Ambiguous alias must not be selected')
        return matches[0] if matches else None

    def test_stable_unique_identities(self):
        """A station and its fourteen pump slots have unique, reproducible UUIDs."""
        namespace = UUID(self.draft['station']['uuid'])
        self.assertEqual(namespace.version, 4)
        pumps = self.draft['pumps']
        self.assertEqual(
            {p['source_key'] for p in pumps}, {f'P{i}' for i in range(1, 15)}
        )
        identifiers = [namespace]
        for pump in pumps:
            identifier = UUID(pump['uuid'])
            self.assertEqual(identifier, uuid5(namespace, f'pump:{pump["source_key"]}'))
            identifiers.append(identifier)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for key in ['parent_entity_uuid', 'entity_uuid']:
            self.assertNotIn(UUID(self.draft['source_row'][key]), identifiers)

    def test_confirmed_source_identity_and_constants(self):
        """Use the pumphouse entity UUID, not the shared parent, for station identity."""
        row = self.draft['source_row']
        layout = self.draft['source_layout']
        self.assertEqual(layout['station_uuid_field'], 'entity_uuid')
        self.assertEqual(self.draft['station']['source_uuid'], row['entity_uuid'])
        self.assertNotEqual(row['entity_uuid'], row['parent_entity_uuid'])
        self.assertEqual(
            set(layout['constant_fields']),
            {
                'parent_entity_uuid',
                'location_type',
                'component_type',
                'event_value_type',
            },
        )
        for field, value in layout['constant_fields'].items():
            self.assertEqual(row[field], value)
        self.assertIn('entity_uuid', layout['logical_sample_identity_fields'])
        self.assertIn('sub_time_period', layout['logical_sample_identity_fields'])
        self.assertIsNone(row['primary_key_definition'])

    def test_hour_bucket_boundaries_and_cadence(self):
        """Keep exact sample milliseconds within a half-open hourly bucket."""
        row = self.draft['source_row']
        layout = self.draft['source_layout']
        self.assertEqual(layout['hour_bucket_field'], 'time_period')
        self.assertEqual(layout['sample_timestamp_field'], 'sub_time_period')
        duration = layout['hour_bucket_duration_ms']
        interval = layout['nominal_sample_interval_ms']
        self.assertEqual(duration, 3_600_000)
        self.assertEqual(interval, 5_000)
        self.assertEqual(duration // interval, 720)
        start = row['time_period']
        end = start + duration
        for timestamp, expected in [
            (start - 1, False),
            (start, True),
            (row['sub_time_period'], True),
            (end - 1, True),
            (end, False),
        ]:
            with self.subTest(timestamp=timestamp):
                self.assertEqual(start <= timestamp < end, expected)
        self.assertFalse(layout['require_five_second_grid_alignment'])
        self.assertNotEqual(row['sub_time_period'] % interval, 0)

    def test_sample_timestamp_consistency(self):
        """Check payload timestamp arithmetic against the confirmed row sample time."""
        sample = self.draft['sample_time']
        row = self.draft['source_row']
        self.assertEqual(Decimal(sample['dex_TIMESTAMP']) * 1000, sample['egt'])
        self.assertEqual(row['sub_time_period'], sample['egt'])
        self.assertEqual((sample['ext'] - sample['egt']) / 1000, 300)
        observed = datetime.fromtimestamp(sample['egt'] / 1000, timezone.utc)
        self.assertEqual(
            observed,
            datetime.fromisoformat(sample['epoch_millisecond_interpretation_utc']),
        )
        self.assertLessEqual(row['time_period'], sample['egt'])
        self.assertLess(sample['egt'] - row['time_period'], 3600 * 1000)

    def test_explicit_alias_examples(self):
        """Each documented spelling variation points to a real catalogue family."""
        for alias in self.draft['aliases']:
            with self.subTest(tag=alias['example_source_tag']):
                candidate = self.candidate(alias['example_source_tag'])
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate[1], alias['family_code'])

    def test_exact_spelling_and_prefix_exceptions(self):
        """Do not strip valve suffixes, repeated PUMP, spaces, or supplied typos."""
        examples = {
            'PUMP4_HOPD_VALVE_POS_PROCESS_VALUE': (
                'HOPD_VALVE_POS_PROCESS_VALUE',
                'HOPD-VALVE',
            ),
            'PUMP12_EOPD_VALVE_POS_PROCESS_VALUE': (
                'EOPD_VALVE_POS_PROCESS_VALUE',
                'EOPD-VALVE',
            ),
            'PUMP12_PUMP_PUMP_INLET_COOLING_WATER_TEMPERATURED3': (
                'PUMP_PUMP_INLET_COOLING_WATER_TEMPERATURED3',
                'WATER-COOLING',
            ),
            'PUMP6_PUMP_MOTOR_HOT_AIR TEMP2': (
                'PUMP_MOTOR_HOT_AIR TEMP2',
                'AIR-COOLING',
            ),
            'PUMP2_PUMP_MOTOR_COLD_AIR TEMP4': (
                'PUMP_MOTOR_COLD_AIR TEMP4',
                'AIR-COOLING',
            ),
            'PUMP14_PUMP_POWERFATCOR': ('PUMP_POWERFATCOR', 'ELECTRICAL'),
            'PUMP7_MOTOR_DE_VIBRATION1': ('MOTOR_DE_VIBRATION1', 'MOTOR-DE-BRG'),
            'COMMAN_FORBAY_LEVEL': ('COMMAN_FORBAY_LEVEL', 'FOREBAY'),
        }
        for tag, expected in examples.items():
            with self.subTest(tag=tag):
                self.assertEqual(self.candidate(tag), expected)

    def test_unknown_tags_and_metadata_stay_unmapped(self):
        """Unknown channels, unregistered pumps and envelope metadata need review."""
        for tag in [
            'ID',
            'TIMESTAMP',
            'st',
            'pd.P1.st',
            'PUMP15_MOTOR_CORE_RTD1_PROCESS_VALUE',
            'PUMP2_MOTOR_CORE_RTD7_PROCESS_VALUE',
            'PUMP2_UNKNOWN_PROCESS_VALUE',
        ]:
            with self.subTest(tag=tag):
                self.assertIsNone(self.candidate(tag))
        self.assertEqual(self.draft['status'], 'draft')
        self.assertFalse(self.draft['latest_state_ingestion_enabled'])
        self.assertIsNone(self.draft['source_row']['primary_key_definition'])


if __name__ == '__main__':
    unittest.main()
