"""Machine knowledge profile (S25): schema, write enforcement, readers.

Runs under the full InvenTree settings (the invoke runner); skipped in the
minimal ai-only settings because it exercises the assets/repair model graph.
"""

from __future__ import annotations

import unittest

from django.apps import apps

if not apps.is_installed('tasks'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.core.exceptions import ValidationError
from django.test import TestCase

from assets import ai_read
from assets.machine_profile import (
    MACHINE_PROFILE_CLASS,
    declared_profile,
    observed_energy_sources,
    profile_claim_section,
    validate_machine_profile,
)
from assets.models import AssetMachine
from assets.serializers import AssetMachineSerializer


def _valid_profile() -> dict:
    return {
        'criticality': 'high',
        'maintenance_strategy': 'preventive',
        'components': [
            {'name': 'Pump unit', 'ref': 'PMP'},
            {'name': 'Impeller', 'ref': 'IMP', 'parent_ref': 'PMP'},
        ],
        'energy_sources': ['electrical', 'gravity'],
        'fault_codes': ['AL-OVERTEMP', 'AL-CLOG'],
        'approved_spares': ['EQ-INF-SEL-0080'],
    }


class ValidateMachineProfileTests(TestCase):
    """The schema gate every stored profile passed through."""

    def test_empty_profile_is_valid(self) -> None:
        """No declared profile is the default state of every machine."""
        self.assertEqual(validate_machine_profile({}), {})
        self.assertEqual(validate_machine_profile(None), {})

    def test_full_profile_round_trips_normalized(self) -> None:
        """A valid profile is returned cleaned, with strings stripped."""
        profile = _valid_profile()
        profile['fault_codes'] = ['  AL-OVERTEMP ', 'AL-CLOG']
        cleaned = validate_machine_profile(profile)
        self.assertEqual(cleaned['fault_codes'], ['AL-OVERTEMP', 'AL-CLOG'])
        self.assertEqual(cleaned['criticality'], 'high')
        self.assertEqual(len(cleaned['components']), 2)

    def test_unknown_top_level_key_is_rejected(self) -> None:
        """A typo fails loudly instead of storing dead data."""
        with self.assertRaises(ValidationError):
            validate_machine_profile({'fault_code': ['AL-1']})

    def test_non_object_profile_is_rejected(self) -> None:
        """A list or scalar is not a profile."""
        with self.assertRaises(ValidationError):
            validate_machine_profile(['not', 'an', 'object'])

    def test_enum_fields_reject_values_outside_vocabulary(self) -> None:
        """Criticality, strategy and energy sources are closed vocabularies."""
        with self.assertRaises(ValidationError):
            validate_machine_profile({'criticality': 'catastrophic'})
        with self.assertRaises(ValidationError):
            validate_machine_profile({'maintenance_strategy': 'hope'})
        with self.assertRaises(ValidationError):
            # 'steam' is not in the LockoutPoint.EnergySource vocabulary.
            validate_machine_profile({'energy_sources': ['electrical', 'steam']})

    def test_string_lists_are_bounded_typed_and_unique(self) -> None:
        """Code and spare lists are capped, string-typed and de-duplicated."""
        with self.assertRaises(ValidationError):
            validate_machine_profile({'fault_codes': [f'C{i}' for i in range(51)]})
        with self.assertRaises(ValidationError):
            validate_machine_profile({'fault_codes': ['AL-1', 'AL-1']})
        with self.assertRaises(ValidationError):
            validate_machine_profile({'fault_codes': ['AL-1', 42]})
        with self.assertRaises(ValidationError):
            validate_machine_profile({'approved_spares': ['']})

    def test_components_require_unique_refs_and_real_parents(self) -> None:
        """Refs are unique; parents must exist and differ from the child."""
        with self.assertRaises(ValidationError):
            validate_machine_profile({'components': [{'name': 'Pump'}]})
        with self.assertRaises(ValidationError):
            validate_machine_profile({
                'components': [{'name': 'A', 'ref': 'X'}, {'name': 'B', 'ref': 'X'}]
            })
        with self.assertRaises(ValidationError):
            # A dangling parent silently flattens the hierarchy for readers.
            validate_machine_profile({
                'components': [{'name': 'A', 'ref': 'X', 'parent_ref': 'MISSING'}]
            })
        with self.assertRaises(ValidationError):
            validate_machine_profile({
                'components': [{'name': 'A', 'ref': 'X', 'parent_ref': 'X'}]
            })
        with self.assertRaises(ValidationError):
            validate_machine_profile({
                'components': [{'name': 'A', 'ref': 'X', 'color': 'red'}]
            })


class SerializerEnforcementTests(TestCase):
    """The serializer is the only write surface; the DB stays permissive."""

    def test_valid_profile_saves_cleaned(self) -> None:
        """A schema-valid profile is stored normalized."""
        serializer = AssetMachineSerializer(
            data={'name': 'Profile Pump 1', 'profile': _valid_profile()}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        machine = serializer.save()
        self.assertEqual(machine.profile['criticality'], 'high')

    def test_invalid_profile_is_refused_with_the_schema_reason(self) -> None:
        """The API refuses out-of-schema profiles with a field error."""
        serializer = AssetMachineSerializer(
            data={'name': 'Profile Pump 2', 'profile': {'criticality': 'nope'}}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('profile', serializer.errors)

    def test_absent_profile_stays_an_empty_dict(self) -> None:
        """No profile supplied means an empty dict, not null."""
        serializer = AssetMachineSerializer(data={'name': 'Profile Pump 3'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        machine = serializer.save()
        self.assertEqual(machine.profile, {})


class ProfileReadersTests(TestCase):
    """Declared beside observed, each with its provenance."""

    @classmethod
    def setUpTestData(cls):
        """Create one machine with a full declared profile."""
        cls.machine = AssetMachine.objects.create(
            name='Reader Pump', profile=_valid_profile()
        )

    def test_declared_profile_degrades_on_stored_drift(self) -> None:
        """A stored value that no longer validates reads as nothing declared."""
        machine = AssetMachine.objects.create(
            name='Drifted Pump', profile={'criticality': 'catastrophic'}
        )
        self.assertEqual(declared_profile(machine), {})

    def test_observed_energy_sources_come_from_lockout_history(self) -> None:
        """Observed sources are distinct, machine-scoped lockout values."""
        from repair.models import LockoutPoint, RepairPacket, RepairPacketGate

        packet = RepairPacket.objects.create(
            machine=self.machine, fault_summary='Seal leak'
        )
        gate = RepairPacketGate.objects.create(
            packet=packet, name='LOTO', gate_type='loto'
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='MCC-HW-01',
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='MCC-HW-02',
        )
        self.assertEqual(observed_energy_sources(self.machine), ['electrical'])
        # Another machine's lockouts never leak into this one's observation.
        other = AssetMachine.objects.create(name='Other Pump')
        self.assertEqual(observed_energy_sources(other), [])

    def test_machine_profile_reader_separates_and_fences(self) -> None:
        """Declared and observed stay separate; operator text is fenced."""
        projection = ai_read.machine_profile(None, self.machine)
        self.assertEqual(projection['profile_class'], MACHINE_PROFILE_CLASS)
        self.assertEqual(projection['declared']['source'], 'operator-declared profile')
        self.assertEqual(projection['declared']['criticality'], 'high')
        # Operator-authored strings arrive fenced, like every projection.
        self.assertIn(
            ai_read.UNTRUSTED_CONTENT_BEGIN, projection['declared']['fault_codes'][0]
        )
        self.assertIn('energy_sources', projection['observed'])
        self.assertIn('installed_spares', projection['observed'])

    def test_machine_overview_carries_the_profile_section(self) -> None:
        """The composite briefing includes the profile."""
        overview = ai_read.machine_overview(None, self.machine)
        self.assertEqual(overview['profile']['profile_class'], MACHINE_PROFILE_CLASS)

    def test_claim_section_is_compact_and_raw(self) -> None:
        """The Luna claim section is compact and unfenced."""
        section = profile_claim_section(self.machine)
        self.assertEqual(section['profile_class'], MACHINE_PROFILE_CLASS)
        self.assertEqual(section['criticality'], 'high')
        self.assertEqual(section['component_count'], 2)
        # Raw values: the whole diagnostic claim is marked untrusted later.
        self.assertEqual(section['fault_codes'][0], 'AL-OVERTEMP')
        self.assertNotIn('components', section)
