"""Tests for applying a reviewed dictionary mapping from a file.

The bulk path must not be a softer gate than the interactive one. Most of these
tests exist to prove a specific bad mapping is *refused*, because a tool that
approves 31 tags in one command is exactly where an unreviewed mapping would
slip through unnoticed.
"""

import json
import tempfile
from io import StringIO
from pathlib import Path
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from assets.models import Client
from assets.registry import register_station
from assets.registry_models import AssetComponent, DictionaryPoint
from part.models import Part

SOURCE_UUID = uuid4()


class ApplyReviewTests(TestCase):
    """Applying decisions from a review file."""

    def setUp(self):
        """A station with a real catalogue component and two points."""
        call_command('load_pump_catalogue', stdout=StringIO())

        self.client_tenant = Client.objects.create(
            name='Review Tenant', code=f'review-{uuid4().hex[:8]}'
        )
        self.station = register_station(
            client=self.client_tenant,
            name='Effluent Pump Station 03',
            public_uuid=None,
            source_namespace='klsw',
            source_key='PH_3',
            source_entity_uuid=SOURCE_UUID,
            source_context={},
        )

        status_part = Part.objects.get(IPN='PS-STATUS')
        self.status_component = AssetComponent.objects.create(
            machine=self.station,
            part=status_part,
            code='PS-STATUS:1',
            name='Equipment Status Group',
        )
        # A parameter the catalogue leaves unitless, and one it defines in
        # metres - between them they cover both branches of the unit check.
        self.status_template = status_part.parameters_list.first().template

        forebay_part = Part.objects.get(IPN='PS-FOREBAY')
        self.level_component = AssetComponent.objects.create(
            machine=self.station,
            part=forebay_part,
            code='PS-FOREBAY:1',
            name='Forebay and Intake Structure',
        )
        self.level_template = forebay_part.parameters_list.first().template

        self.point = DictionaryPoint.objects.create(
            station=self.station,
            machine=self.station,
            component=self.status_component,
            template=self.status_template,
            path='/st',
            raw_tag='st',
            display_name='Running Status',
            data_type='status',
            status='draft',
            unit_status='unitless',
        )

    def write(self, payload):
        """Write a review file to a scratch path."""
        path = Path(tempfile.mkdtemp()) / 'review.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        return path

    def review(self, **overrides):
        """A review file approving the status point."""
        entry = {
            'paths': ['/st'],
            'data_type': 'status',
            'unit': '',
            'unit_status': 'unitless',
            'note': 'Confirmed run state; I=Idle, R=Running.',
        }
        entry.update(overrides)
        return {'station_source_uuid': str(SOURCE_UUID), 'approve': [entry]}

    def apply(self, payload, **kwargs):
        """Run the command against a written review file."""
        call_command(
            'apply_dictionary_review',
            review=self.write(payload),
            stdout=StringIO(),
            **kwargs,
        )

    def level_point(self, **overrides):
        """A second point bound to a parameter the catalogue defines in metres."""
        values = {
            'station': self.station,
            'machine': self.station,
            'component': self.level_component,
            'template': self.level_template,
            'path': '/dex/COMMAN_FORBAY_LEVEL',
            'raw_tag': 'COMMAN_FORBAY_LEVEL',
            'display_name': 'Forebay Water Level',
            'data_type': 'number',
            'status': 'draft',
            'unit': 'm',
            'unit_status': 'proposed',
        }
        values.update(overrides)
        return DictionaryPoint.objects.create(**values)

    def test_a_well_formed_approval_is_applied(self):
        """The happy path, so the refusals below mean something."""
        self.apply(self.review())

        self.point.refresh_from_db()
        self.assertEqual(self.point.status, 'approved')
        self.assertEqual(self.point.unit_status, 'unitless')
        self.assertTrue(self.point.review_note)
        self.assertIsNotNone(self.point.reviewed_at)

    def test_a_catalogue_unit_is_accepted_when_it_agrees(self):
        """Confirming the catalogue's own unit is the normal approval."""
        point = self.level_point()
        payload = self.review()
        payload['approve'] = [
            {
                'paths': ['/dex/COMMAN_FORBAY_LEVEL'],
                'data_type': 'number',
                'unit': 'm',
                'unit_status': 'verified',
                'note': 'Confirmed against the catalogue parameter, which defines metres.',
            }
        ]
        self.apply(payload)

        point.refresh_from_db()
        self.assertEqual(point.status, 'approved')
        self.assertEqual(point.unit, 'm')

    def test_a_unit_the_catalogue_cannot_reconcile_is_refused(self):
        """Degrees against a parameter defined in metres is a real mistake."""
        self.level_point()
        payload = self.review()
        payload['approve'] = [
            {
                'paths': ['/dex/COMMAN_FORBAY_LEVEL'],
                'data_type': 'number',
                'unit': 'degC',
                'unit_status': 'verified',
                'note': 'Wrong dimension entirely.',
            }
        ]

        with self.assertRaises(CommandError):
            self.apply(payload)

    def test_a_second_path_cannot_claim_the_same_parameter(self):
        """Two approved tags on one component parameter is ambiguous."""
        self.apply(self.review())
        duplicate = DictionaryPoint.objects.create(
            station=self.station,
            machine=self.station,
            component=self.status_component,
            template=self.status_template,
            path='/pd/P1/st',
            raw_tag='st',
            display_name='Running Status',
            data_type='status',
            status='draft',
            unit_status='unitless',
        )
        payload = self.review()
        payload['approve'][0]['paths'] = ['/pd/P1/st']

        with self.assertRaises(CommandError):
            self.apply(payload)

        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, 'draft')

    def test_a_point_without_a_parameter_is_refused(self):
        """The interactive endpoint demands one, so this must too."""
        self.point.template = None
        self.point.save(update_fields=['template'])

        with self.assertRaises(CommandError):
            self.apply(self.review())

        self.point.refresh_from_db()
        self.assertEqual(self.point.status, 'draft')

    def test_an_unknown_data_type_is_refused(self):
        """'unknown' means nobody worked out what the tag carries."""
        with self.assertRaises(CommandError):
            self.apply(self.review(data_type='unknown'))

    def test_an_empty_note_is_refused(self):
        """The note is the only record of why a mapping was believed."""
        with self.assertRaises(CommandError):
            self.apply(self.review(note='   '))

    def test_an_unsettled_unit_is_refused(self):
        """'proposed' means suggested by import, not confirmed by a person."""
        with self.assertRaises(CommandError):
            self.apply(self.review(unit_status='proposed'))

    def test_a_unitless_point_may_not_carry_a_unit(self):
        """Contradictory metadata is a mistake, not a preference."""
        with self.assertRaises(CommandError):
            self.apply(self.review(unit='degC', unit_status='unitless'))

    def test_a_verified_unit_requires_a_unit(self):
        """Otherwise 'verified' asserts something that is not there."""
        with self.assertRaises(CommandError):
            self.apply(self.review(unit='', unit_status='verified'))

    def test_a_nonsense_unit_is_refused(self):
        """The unit registry is what keeps conversions meaningful."""
        with self.assertRaises(CommandError):
            self.apply(self.review(unit='bananas', unit_status='verified'))

    def test_an_unknown_path_is_refused(self):
        """Silently skipping a typo would report success for work not done."""
        payload = self.review()
        payload['approve'][0]['paths'] = ['/does/not/exist']

        with self.assertRaises(CommandError):
            self.apply(payload)

    def test_an_unknown_station_is_refused(self):
        """A review file must name a station that exists."""
        payload = self.review()
        payload['station_source_uuid'] = str(uuid4())

        with self.assertRaises(CommandError):
            self.apply(payload)

    def test_nothing_is_written_when_one_entry_fails(self):
        """All or nothing: a half-applied review is worse than none."""
        second = self.level_point()
        payload = self.review()
        payload['approve'].append({
            'paths': ['/dex/COMMAN_FORBAY_LEVEL'],
            'data_type': 'unknown',
            'unit': '',
            'unit_status': 'unresolved',
            'note': 'nope',
        })

        with self.assertRaises(CommandError):
            self.apply(payload)

        self.point.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.point.status, 'draft')
        self.assertEqual(second.status, 'draft')

    def test_a_withheld_point_records_its_reason(self):
        """A tag nobody could settle is a finding worth keeping."""
        payload = {
            'station_source_uuid': str(SOURCE_UUID),
            'withhold': [
                {
                    'paths': ['/st'],
                    'reason': 'Instrument type unknown.',
                    'recommendation': 'Ask the site.',
                }
            ],
        }
        self.apply(payload)

        self.point.refresh_from_db()
        self.assertEqual(self.point.status, 'draft')
        self.assertIn('Instrument type unknown.', self.point.review_note)
        self.assertIn('Ask the site.', self.point.review_note)

    def test_dry_run_writes_nothing(self):
        """A preview that wrote would be worse than no preview."""
        payload = {
            'station_source_uuid': str(SOURCE_UUID),
            'withhold': [{'paths': ['/st'], 'reason': 'Pending.'}],
        }
        self.apply(payload, dry_run=True)

        self.point.refresh_from_db()
        self.assertEqual(self.point.review_note, '')


class ShippedReviewFileTests(TestCase):
    """The review file committed for the PH_3 pilot."""

    REVIEW = (
        Path(__file__).resolve().parents[4]
        / 'contrib'
        / 'pump-cassandra'
        / 'PH_3.review.json'
    )

    def setUp(self):
        """Load the shipped review file."""
        self.review = json.loads(self.REVIEW.read_text(encoding='utf-8'))

    def test_every_approval_carries_a_note(self):
        """An approval without a stated reason is an unreviewed approval."""
        for entry in self.review['approve']:
            with self.subTest(paths=entry['paths'][:1]):
                self.assertTrue(entry['note'].strip())

    def test_every_withheld_entry_says_what_is_missing(self):
        """'Not approved' is only useful if it says what would settle it."""
        for entry in self.review['withhold']:
            with self.subTest(paths=entry['paths'][:1]):
                self.assertTrue(entry['reason'].strip())

    def test_no_path_is_both_approved_and_withheld(self):
        """The file must express one decision per tag."""
        approved = {p for e in self.review['approve'] for p in e['paths']}
        withheld = {p for e in self.review['withhold'] for p in e['paths']}

        self.assertEqual(approved & withheld, set())

    def test_reactive_power_is_not_approved_as_apparent_power(self):
        """MVA validates and MVar does not, which makes this an easy wrong turn.

        Recording reactive power under an apparent-power unit would be a false
        statement about the measurement, so the tag stays withheld until the
        unit registry can express var.
        """
        withheld = {p for e in self.review['withhold'] for p in e['paths']}

        self.assertIn('/dex/PUMP5_PUMP_REACTIVE_POWER', withheld)
        for entry in self.review['approve']:
            with self.subTest(paths=entry['paths'][:1]):
                self.assertNotIn(entry.get('unit'), {'MVA', 'MVar'})

    def test_vibration_tags_are_not_given_a_guessed_unit(self):
        """mm/s and um differ by orders of magnitude; a guess is not a unit."""
        withheld = {p for e in self.review['withhold'] for p in e['paths']}

        for path in (
            '/dex/PUMP12_MTR_NDE_BRG_VBRTN2_PROCESS_VALUE',
            '/dex/PUMP4_PMP_THRST_BRG_VBRTN2_PROCESS_VALUE',
            '/dex/PUMP3_PUMP_MOTOR_DE_VIBRATION2',
        ):
            with self.subTest(path=path):
                self.assertIn(path, withheld)

    def test_surge_pool_level_is_not_merged_into_forebay_level(self):
        """Two distinct bodies of water must not collapse into one signal."""
        withheld = {p for e in self.review['withhold'] for p in e['paths']}

        self.assertIn('/sl', withheld)
