"""Synthetic copy fixture contracts, not native ownership or deletion proof."""

from integration_state.tests.assertions import assert_literal_bool

from dataclasses import FrozenInstanceError, replace
from unittest import TestCase

from assets.integration_contracts import ClientSourceIdentity
from machine_health.services import integration_s6_copy as copy
from machine_health.tests.test_integration_s6_delivery import payload
from machine_health.tests.test_integration_s6_wire import observation


class HistoryCopyFixtureTests(TestCase):
    """Independent fixture bytes/owners stay bounded, explicit and non-authoritative."""

    def fixture(self, **changes):
        """Use an explicit synthetic content source, never a historical-owner inference."""
        import hashlib

        fields = {
            'copy_key': ' private-fixture:7 ',
            'copy_kind': 'report',
            'source_digest': hashlib.sha256(payload()).hexdigest(),
        }
        fields.update(changes)
        return copy.FixtureHistoryCopy(**fields)

    def test_defaults_preserve_unknowns_and_indefinite_retention(self):
        """An explicitly supplied fixture is not discovered native copy inventory."""
        value = self.fixture()
        self.assertIsNone(value.content)
        self.assertIsNone(value.owners)
        self.assertEqual(value.attribution, 'unknown')
        self.assertEqual(value.retention, 'indefinite')
        assert_literal_bool(self, value.synthetic_only, True)
        frozen_field = 'retention'
        with self.assertRaises(FrozenInstanceError):
            setattr(value, frozen_field, 'reviewable')

    def test_original_empty_and_unknown_bytes_and_literal_keys_stay_distinct(self):
        """Do not repair IDs or substitute empty content for unknown bytes."""
        missing, empty = self.fixture(), self.fixture(content=b'')
        self.assertNotEqual(copy._copy_key(missing), copy._copy_key(empty))
        self.assertNotEqual(
            copy._copy_key(empty),
            copy._copy_key(replace(empty, copy_key='private-fixture:7')),
        )
        value = self.fixture(
            content=b'private-copy-body-sentinel',
            owners=(ClientSourceIdentity(environment='s6-test', pk=7),),
        )
        self.assertNotIn('private-copy-body-sentinel', repr(value))
        self.assertEqual(value.content, b'private-copy-body-sentinel')

    def test_unsupported_fixture_scalars_and_false_native_claims_are_refused(self):
        """Only exact bounded builtin bytes and independently typed owners qualify."""
        for changes in (
            {'content': bytearray(b'fixture')},
            {'content': 'fixture'},
            {'content': b'x' * (copy.MAX_HISTORY_BYTES + 1)},
            {'copy_kind': 'native-delete'},
            {'source_digest': 'unknown'},
            {'copy_key': ' '},
            {'owners': [ClientSourceIdentity(environment='s6-test', pk=7)]},
            {'owners': (ClientSourceIdentity(environment='s6-test', pk=7),) * 2},
            {'retention': 'delete-now'},
        ):
            with self.subTest(fields=tuple(changes)), self.assertRaises(ValueError):
                self.fixture(**changes)
        value = self.fixture()
        object.__setattr__(value, 'synthetic_only', False)
        with self.assertRaises(copy.HistoryCopyError):
            copy._copy_key(value)

    def test_foreign_carriers_are_rejected_before_property_access(self):
        """A matching property name cannot install fixture or owner authority."""
        reads = []

        class Foreign:
            @property
            def copy_key(self):
                reads.append(True)
                return 'foreign'

            @property
            def environment(self):
                reads.append(True)
                return 's6-test'

        for check in (
            copy._copy_key,
            copy._client_key,
            copy._source_key,
            copy._intent_key,
            copy._impact_key,
        ):
            with (
                self.subTest(check=check.__name__),
                self.assertRaises(copy.HistoryCopyError),
            ):
                check(Foreign())
        self.assertEqual(reads, [])

    def test_complete_empty_source_and_incomplete_ties_are_not_interchangeable(self):
        """A partial source cannot create an apparently complete copy-planning scope."""
        empty = copy._coordinates(payload(records=()))
        self.assertEqual(empty[:4], ('s6-test', 23, 7, 29))
        records = tuple(observation(source_observation_id=f'tie:{i}') for i in range(3))
        with self.assertRaises(copy.HistoryCopyUnavailableError):
            copy._coordinates(payload(records=records, cap=2))
