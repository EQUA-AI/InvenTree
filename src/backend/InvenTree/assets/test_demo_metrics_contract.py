"""Contract-adapter tests for the EQUA demo metrics fixture and mapping.

Work package A: quality/threshold/clock/due-date/ownership translations must be
exact and fail closed. These tests run against the promoted tracked fixture
resources, never the ignored LocalDocs originals.
"""

import copy
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase

from assets.demo_metrics import contract, reference
from assets.demo_metrics.contract import ContractError
from assets.health_models import MachineSignalBinding

RESOURCES = Path(__file__).parent / 'demo_metrics' / 'resources'
FIXTURE_PATH = RESOURCES / 'demo_fixture.json'

# Reference values verified in the implementation plan (section 15.1).
EXPECTED_CANONICAL = 'd5e2c2b5e4103d966649b5ec570ba4c702aa9b4d6d0fe814bcf29f46b4b1c702'
EXPECTED_FILE = '64a7a97b31b323b25b1829b5fc4822c0b40be4a3ffb341979ff7646490442209'


def load_fixture():
    """Load the promoted fixture through the strict adapter."""
    return contract.load_fixture(FIXTURE_PATH)


def build_mapping(
    fixture, *, session_key='equa-test-1', machine_ids=None, location_ids=None
):
    """Build a fully resolved mapping dict for the promoted fixture."""
    machine_ids = machine_ids or {
        alias: 100 + index
        for index, alias in enumerate(
            sorted(m['key'] for m in fixture.data['machines'])
        )
    }
    location_ids = location_ids or {
        alias: 200 + index
        for index, alias in enumerate(
            sorted(n['key'] for n in fixture.data['locations'])
        )
    }
    return {
        'schema_version': 'equa.demo-target-mapping/1',
        'ready_for_apply': True,
        'warning': 'Synthetic demo mapping; resolved for a disposable test target.',
        'dataset_key': fixture.dataset_key,
        'session_key': session_key,
        'target': {
            'subscription_id': 'test-subscription',
            'resource_group': 'epconchat',
            'app_name': 'aimms-experimental',
            'region_reported': 'eastus2',
            'environment_resource_id': '/test/environment',
            'database_identity_fingerprint': 'test-db-fingerprint',
            'approved_commit_sha': 'dcc82279407f16a1623e6166b32ed7eacef9a74c',
            'approved_image_digest': 'sha256:test',
            'demo_owner_identity': 'demo-operator',
        },
        'locations': {
            alias: {
                'target_location_id': location_ids[alias],
                'expected_version': 0,
                'create': False,
                'reparent': False,
            }
            for alias in location_ids
        },
        'machines': {
            alias: {
                'target_machine_id': machine_ids[alias],
                'expected_version': 0,
                'verified_synthetic_ownership': True,
                'ownership_evidence': {
                    'method': 'managed_demo_part',
                    'part_ipns': [f'DEMO-PART-{alias}'],
                },
                'create': False,
                'move': False,
                'rename': False,
                'client_code': 'internal',
            }
            for alias in machine_ids
        },
        'state_mapping': dict(contract.STATE_TRANSLATION),
        'priority_mapping': dict(contract.PRIORITY_TRANSLATION),
        'ingestion': {
            'adapter_id': 'equa-demo-metrics-v1',
            'verified_signal_identifiers': {},
            'verified_units': {
                signal['key']: signal['unit']
                for signal in fixture.data['signal_definitions']
            },
            'verified_stale_threshold_seconds': 300,
            'external_side_effects_isolated': True,
        },
        'reporting_timezone': 'UTC',
        'side_effect_policy': ['database_write'],
        'expires_at': '2030-01-01T00:00:00Z',
        'expected_counts': contract.expected_counts(fixture),
        'input_hashes': {
            'fixture_canonical_sha256': fixture.canonical_sha256,
            'fixture_file_sha256': fixture.file_sha256,
        },
        'source_scope': {
            'DEMO-SOURCE-A': {
                'client_code': 'internal',
                'site_key': 'epcon-experimental',
            },
            'DEMO-SOURCE-B': {
                'client_code': 'internal',
                'site_key': 'epcon-experimental',
            },
        },
        'assignees': {},
        'control_records': {
            'WO-08': {'mode': 'synthetic_import', 'ownership': 'session_created'},
            'WO-09': {'mode': 'synthetic_import', 'ownership': 'session_created'},
        },
        'history_import_approved': False,
        'approval': {'plan_sha256': None, 'approved_by': None, 'approved_at': None},
    }


class FixtureIdentityTest(SimpleTestCase):
    """The promoted fixture keeps its reviewed identity hashes."""

    def test_canonical_and_file_hashes_match_the_reviewed_reference(self):
        """Pin the reviewed canonical and byte-level fixture hashes."""
        fixture = load_fixture()
        self.assertEqual(fixture.canonical_sha256, EXPECTED_CANONICAL)
        self.assertEqual(fixture.file_sha256, EXPECTED_FILE)

    def test_byte_level_and_canonical_hashes_are_distinct(self):
        """Keep the byte-level and canonical fixture hashes distinct."""
        fixture = load_fixture()
        self.assertNotEqual(fixture.file_sha256, fixture.canonical_sha256)

    def test_expected_cohort_counts_are_derived_from_the_fixture(self):
        """Derive every expected cohort count from the promoted fixture."""
        self.assertEqual(
            contract.expected_counts(load_fixture()),
            {
                'bindings': 15,
                'observations': 12,
                'work_orders': 9,
                'open_work_orders': 7,
                'completed_controls': 2,
                'configured_machines': 5,
            },
        )

    def test_non_finite_numbers_are_rejected(self):
        """Reject fixture readings carrying non-finite values."""
        import tempfile

        data = json.loads(FIXTURE_PATH.read_text())
        data['readings'][0]['value'] = float('inf')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad_fixture.json'
            path.write_text(json.dumps(data))
            with self.assertRaises(ContractError) as caught:
                contract.load_fixture(path)
        self.assertEqual(caught.exception.code, 'NOT_FINITE')


class QualityAndThresholdTest(TestCase):
    """Fixture quality and threshold semantics translate exactly once."""

    def test_fixture_valid_quality_translates_to_backend_good(self):
        """Translate the fixture's valid quality to the backend good quality."""
        self.assertEqual(contract.translate_quality('valid'), 'good')

    def test_unknown_fixture_quality_is_rejected(self):
        """Reject every quality outside the fixture vocabulary."""
        for bad in ('GOOD', 'good', 'suspect', '', None):
            with self.assertRaises(ContractError) as caught:
                contract.translate_quality(bad)
            self.assertEqual(caught.exception.code, 'BAD_QUALITY')

    def test_backend_thresholds_are_strict_greater_than(self):
        """Classify thresholds with strict greater-than backend semantics."""
        # Backend semantics: value == warn_max stays normal (strict '>').
        binding = MachineSignalBinding(
            machine=None,
            source=None,
            external_key='x',
            display_name='x',
            unit='degC',
            warn_max=75.0,
            critical_max=90.0,
        )
        self.assertEqual(binding.classify(75.0), 'normal')
        self.assertEqual(binding.classify(75.0001), 'warning')
        self.assertEqual(binding.classify(90.0), 'warning')
        self.assertEqual(binding.classify(90.0001), 'critical')

    def test_fixture_reference_thresholds_are_greater_or_equal(self):
        """Pin the frozen reference contract's greater-or-equal thresholds."""
        # The frozen reference contract uses '>='; it is documented, not changed.
        data = load_fixture().data
        data = copy.deepcopy(data)
        for reading in data['readings']:
            if (
                reading['machine_key'] == 'A01'
                and reading['signal_key'] == 'bearing_temperature'
            ):
                reading['value'] = 75.0  # exactly warning_at for bearing_temperature
        state = reference.machine_state(data, 'A01')
        self.assertEqual(state['condition'], 'warning')

    def test_priority_translation_is_the_approved_demo_mapping(self):
        """Map priorities only through the approved demo translation."""
        self.assertEqual(
            {
                code: contract.translate_priority(code)
                for code in ('P1', 'P2', 'P3', 'P4')
            },
            {'P1': 'high', 'P2': 'high', 'P3': 'medium', 'P4': 'low'},
        )
        with self.assertRaises(ContractError):
            contract.translate_priority('P5')


class DueDateTranslationTest(SimpleTestCase):
    """Fixture due instants become application due dates once, in the reporting zone."""

    def anchor(self):
        """Return the fixed session anchor for the due-date tests."""
        return dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc)

    def test_due_date_uses_the_reporting_timezone(self):
        """Resolve the due date in the reporting timezone, not UTC."""
        # 23:30 UTC is already the next day in Tokyo.
        due = dt.datetime(2026, 9, 24, 23, 30, tzinfo=dt.timezone.utc)
        day, original = contract.translate_due_date(
            due, self.anchor(), self.anchor(), 'Asia/Tokyo'
        )
        self.assertEqual(day, dt.date(2026, 9, 25))
        self.assertEqual(original, '2026-09-24T23:30:00Z')

    def test_due_instant_on_local_midnight_is_that_date(self):
        """Map a due instant on local midnight to that local date."""
        due = dt.datetime(2026, 9, 27, 0, 0, tzinfo=dt.timezone.utc)
        day, _ = contract.translate_due_date(due, self.anchor(), self.anchor(), 'UTC')
        self.assertEqual(day, dt.date(2026, 9, 27))

    def test_due_at_anchor_equality_maps_to_anchor_date(self):
        """Map a due instant equal to the anchor to the anchor's date."""
        day, _ = contract.translate_due_date(
            self.anchor(), self.anchor(), self.anchor(), 'UTC'
        )
        self.assertEqual(day, dt.date(2026, 9, 24))

    def test_offset_survives_anchor_rebase(self):
        """Keep the fixture's due offset when the session anchor is rebased."""
        # Fixture WO-01 is due 3 days after its anchor: the date follows the
        # session anchor, not the fixture anchor.
        due = self.anchor() + dt.timedelta(days=3)
        day, _ = contract.translate_due_date(due, self.anchor(), self.anchor(), 'UTC')
        self.assertEqual(day, dt.date(2026, 9, 27))
        rebased = dt.datetime(2026, 11, 1, 23, 0, tzinfo=dt.timezone.utc)
        day, _ = contract.translate_due_date(due, self.anchor(), rebased, 'UTC')
        self.assertEqual(day, dt.date(2026, 11, 4))

    def test_dst_boundary_uses_zone_rules(self):
        """Keep the local due date across a daylight-saving boundary."""
        # US DST ends 2026-11-01; a due instant just after keeps its local date.
        fixture_as_of = dt.datetime(2026, 10, 20, 12, 0, tzinfo=dt.timezone.utc)
        due = dt.datetime(2026, 11, 1, 6, 30, tzinfo=dt.timezone.utc)
        day, _ = contract.translate_due_date(
            due, fixture_as_of, fixture_as_of, 'America/Chicago'
        )
        self.assertEqual(day, dt.date(2026, 11, 1))
        # Sanity: the zone really is DST-aware (offset changes across the boundary).
        zone = ZoneInfo('America/Chicago')
        self.assertNotEqual(
            dt
            .datetime(2026, 10, 31, 12, tzinfo=dt.timezone.utc)
            .astimezone(zone)
            .utcoffset(),
            dt
            .datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc)
            .astimezone(zone)
            .utcoffset(),
        )

    def test_unknown_timezone_is_rejected(self):
        """Reject reporting timezones unknown to the zone database."""
        with self.assertRaises(ContractError) as caught:
            contract.translate_due_date(
                self.anchor(), self.anchor(), self.anchor(), 'Mars/Olympus'
            )
        self.assertEqual(caught.exception.code, 'BAD_TIMEZONE')


class ReadingTranslationTest(SimpleTestCase):
    """Readings translate to namespaced ingestion payloads anchored to the session."""

    def setUp(self):
        """Load the fixture and validate its resolved mapping."""
        self.fixture = load_fixture()
        self.mapping = contract.validate_mapping(
            build_mapping(self.fixture), self.fixture, require_ready=True
        )
        self.anchor = dt.datetime(2026, 9, 25, 18, 0, tzinfo=dt.timezone.utc)

    def test_twelve_readings_translate_with_namespaced_unique_tags(self):
        """Translate twelve readings into uniquely namespaced ingestion tags."""
        readings = contract.translate_readings(self.fixture, self.mapping, self.anchor)
        self.assertEqual(len(readings), 12)
        self.assertEqual(len({r.external_key for r in readings}), 12)
        for reading in readings:
            self.assertTrue(
                reading.external_key.startswith('equa-demo-metrics-v1/equa-test-1/')
            )
            self.assertEqual(reading.quality, 'good')

    def test_observed_offsets_follow_the_session_anchor(self):
        """Anchor observed instants to fixed offsets before the session anchor."""
        readings = {
            r.item_key: r
            for r in contract.translate_readings(
                self.fixture, self.mapping, self.anchor
            )
        }
        fresh = readings['initial/A01/bearing_temperature']
        stale = readings['initial/A03/bearing_temperature']
        self.assertEqual(fresh.observed_at, self.anchor - dt.timedelta(seconds=60))
        self.assertEqual(stale.observed_at, self.anchor - dt.timedelta(seconds=3600))

    def test_never_seen_and_unconfigured_machines_have_no_readings(self):
        """Emit no readings for never-seen or unconfigured machines."""
        readings = contract.translate_readings(self.fixture, self.mapping, self.anchor)
        aliases = {r.machine_alias for r in readings}
        self.assertEqual(aliases, {'A01', 'A02', 'A03', 'B01'})
        self.assertNotIn('B02', aliases)
        self.assertNotIn('B03', aliases)

    def test_sequence_is_monotonic_per_batch(self):
        """Assign strictly monotonic sequences to the translated readings."""
        readings = contract.translate_readings(self.fixture, self.mapping, self.anchor)
        sequences = [r.sequence for r in readings]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(set(sequences)), len(sequences))


class MappingValidationTest(SimpleTestCase):
    """The resolved mapping fails closed on every unresolved or mutated field."""

    def setUp(self):
        """Load the promoted fixture for mapping validation."""
        self.fixture = load_fixture()

    def validate(self, data, require_ready=True):
        """Validate mapping data against the loaded fixture."""
        return contract.validate_mapping(
            data, self.fixture, require_ready=require_ready
        )

    def assertRejected(self, data, code, require_ready=True):
        """Assert the mapping is rejected with the expected error code."""
        with self.assertRaises(ContractError) as caught:
            self.validate(data, require_ready=require_ready)
        self.assertEqual(caught.exception.code, code)

    def test_resolved_mapping_is_accepted(self):
        """Accept the fully resolved mapping for the promoted fixture."""
        mapping = self.validate(build_mapping(self.fixture))
        self.assertEqual(mapping.session_key, 'equa-test-1')
        self.assertEqual(mapping.side_effect_policy, ('database_write',))

    def test_unknown_top_level_field_is_rejected(self):
        """Reject unknown top-level mapping fields."""
        data = build_mapping(self.fixture)
        data['surprise'] = 1
        self.assertRejected(data, 'UNKNOWN_FIELD')

    def test_unknown_machine_field_is_rejected(self):
        """Reject unknown per-machine mapping fields."""
        data = build_mapping(self.fixture)
        data['machines']['A01']['mystery'] = 1
        self.assertRejected(data, 'UNKNOWN_FIELD')

    def test_null_machine_identity_is_rejected(self):
        """Reject a null target machine identity."""
        data = build_mapping(self.fixture)
        data['machines']['A01']['target_machine_id'] = None
        self.assertRejected(data, 'UNRESOLVED_MAPPING', require_ready=False)

    def test_duplicate_machine_targets_are_rejected(self):
        """Reject two machines resolving to the same target."""
        data = build_mapping(self.fixture)
        ids = dict.fromkeys(data['machines'], 42)
        data['machines'] = build_mapping(self.fixture, machine_ids=ids)['machines']
        self.assertRejected(data, 'DUPLICATE_TARGET', require_ready=False)

    def test_unproven_synthetic_ownership_is_rejected(self):
        """Reject machines without proven synthetic ownership."""
        data = build_mapping(self.fixture)
        data['machines']['A01']['verified_synthetic_ownership'] = False
        self.assertRejected(data, 'OWNERSHIP_UNPROVEN', require_ready=False)

    def test_mapping_boolean_is_never_ownership_evidence(self):
        """Reject a bare boolean posing as ownership evidence."""
        data = build_mapping(self.fixture)
        data['machines']['A01']['ownership_evidence'] = True
        self.assertRejected(data, 'MAPPING_INVALID', require_ready=False)

    def test_untrusted_ownership_evidence_method_is_rejected(self):
        """Reject ownership evidence from an untrusted method."""
        data = build_mapping(self.fixture)
        data['machines']['A01']['ownership_evidence'] = {
            'method': 'verified_synthetic_ownership'
        }
        self.assertRejected(data, 'OWNERSHIP_UNPROVEN', require_ready=False)

    def test_ownership_evidence_without_managed_parts_is_rejected(self):
        """Reject managed-part evidence that names no managed parts."""
        data = build_mapping(self.fixture)
        data['machines']['A01']['ownership_evidence'] = {
            'method': 'managed_demo_part',
            'part_ipns': [],
        }
        self.assertRejected(data, 'OWNERSHIP_UNPROVEN', require_ready=False)

    def test_physical_alias_is_never_a_security_scope(self):
        """Reject a fixture location alias used as a security site key."""
        data = build_mapping(self.fixture)
        data['source_scope']['DEMO-SOURCE-A'] = {
            'client_code': 'internal',
            'site_key': 'SITE-A',
        }
        self.assertRejected(data, 'SOURCE_SCOPE_MISMATCH', require_ready=False)

    def test_source_client_must_match_the_mapped_machines(self):
        """Require the source client to match the mapped machines' client."""
        data = build_mapping(self.fixture)
        data['source_scope']['DEMO-SOURCE-A'] = {
            'client_code': 'other-client',
            'site_key': 'epcon-experimental',
        }
        self.assertRejected(data, 'SOURCE_SCOPE_MISMATCH', require_ready=False)

    def test_missing_security_site_key_is_rejected(self):
        """Require every source scope to declare a security site key."""
        data = build_mapping(self.fixture)
        data['source_scope']['DEMO-SOURCE-A'] = {'client_code': 'internal'}
        self.assertRejected(data, 'UNRESOLVED_MAPPING', require_ready=False)

    def test_history_import_approval_must_be_boolean(self):
        """Require the history import approval flag to be boolean."""
        data = build_mapping(self.fixture)
        data['history_import_approved'] = 'yes'
        self.assertRejected(data, 'MAPPING_INVALID', require_ready=False)

    def test_moving_a_machine_is_rejected(self):
        """Reject mappings that move an existing machine."""
        data = build_mapping(self.fixture)
        data['machines']['A01']['move'] = True
        self.assertRejected(data, 'MUTATING_MAPPING', require_ready=False)

    def test_fixture_hash_mismatch_is_rejected(self):
        """Reject mappings whose fixture hash no longer matches."""
        data = build_mapping(self.fixture)
        data['input_hashes']['fixture_canonical_sha256'] = '0' * 64
        self.assertRejected(data, 'HASH_MISMATCH', require_ready=False)

    def test_wrong_expected_counts_are_rejected(self):
        """Reject mappings whose expected counts drift from the fixture."""
        data = build_mapping(self.fixture)
        data['expected_counts']['bindings'] = 14
        self.assertRejected(data, 'MAPPING_INVALID', require_ready=False)

    def test_changed_priority_mapping_is_rejected(self):
        """Reject mutations of the approved priority mapping."""
        data = build_mapping(self.fixture)
        data['priority_mapping'] = {**data['priority_mapping'], 'P1': 'low'}
        self.assertRejected(data, 'MAPPING_INVALID', require_ready=False)

    def test_unisolated_side_effect_policy_is_rejected(self):
        """Reject side effect policies beyond the isolated database write."""
        data = build_mapping(self.fixture)
        data['side_effect_policy'] = ['database_write', 'webhook']
        self.assertRejected(data, 'EFFECTS_NOT_ISOLATED', require_ready=False)

    def test_freshness_policy_must_match_the_fixture(self):
        """Require the verified stale threshold to match the fixture."""
        data = build_mapping(self.fixture)
        data['ingestion']['verified_stale_threshold_seconds'] = 900
        self.assertRejected(data, 'MAPPING_INVALID', require_ready=False)

    def test_not_ready_mapping_cannot_be_applied(self):
        """Refuse to apply a mapping that is not marked ready."""
        data = build_mapping(self.fixture)
        data['ready_for_apply'] = False
        self.assertRejected(data, 'MAPPING_NOT_READY')

    def test_missing_target_fingerprint_is_rejected_for_apply(self):
        """Require the target database identity fingerprint before apply."""
        data = build_mapping(self.fixture)
        data['target']['database_identity_fingerprint'] = None
        self.assertRejected(data, 'UNRESOLVED_MAPPING')

    def test_session_key_must_be_a_simple_slug(self):
        """Reject session keys that are not simple slugs."""
        data = build_mapping(self.fixture)
        data['session_key'] = '../etc/passwd'
        self.assertRejected(data, 'MAPPING_INVALID', require_ready=False)

    def test_dataset_key_mismatch_is_rejected(self):
        """Reject mappings whose dataset key does not match the fixture."""
        data = build_mapping(self.fixture)
        data['dataset_key'] = 'other-dataset'
        self.assertRejected(data, 'MAPPING_INVALID', require_ready=False)

    def test_expired_mapping_cannot_be_applied(self):
        """Reject mappings carrying an unparsable expiry timestamp."""
        data = build_mapping(self.fixture)
        data['expires_at'] = 'not-a-timestamp'
        self.assertRejected(data, 'BAD_TIMESTAMP', require_ready=False)
