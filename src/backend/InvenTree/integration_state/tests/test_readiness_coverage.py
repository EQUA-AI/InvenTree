"""Current coverage findings remain partial, scoped and non-erasing."""

from dataclasses import replace
from unittest import TestCase
from uuid import uuid4

from integration_state.readiness_coverage import scope_coverage
from integration_state.s7_contracts import SourceAspectIdentity


class ScopeCoverageTests(TestCase):
    """Known capture is not a complete deployed writer/copy population oracle."""

    def setUp(self):
        """Use exact synthetic declared coordinates with no source records."""
        self.source = SourceAspectIdentity(
            environment='synthetic-coverage',
            epoch=uuid4(),
            entity_kind='machine',
            aspect='placement',
            source_id=7,
        )

    def test_verified_placement_capture_still_cannot_claim_complete_inventory(self):
        """All DB placement paths do not prove backup/copy/history completeness."""
        result = scope_coverage((self.source,), placement_capture_verified=True)
        row = result['domains'][0]
        self.assertEqual(row['capture_state'], 'transactional_placement_verified')
        self.assertIn('database_delete', row['known_writer_paths'])
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(row['copy_instance_inventory'], 'unqualified')
        self.assertNotIn('source_id', row)

    def test_partial_future_and_unknown_domains_keep_specific_gaps(self):
        """Single-record markers cannot authorize bulk, relink or storage writers."""
        scope = tuple(
            replace(self.source, entity_kind=k, aspect='metadata')
            for k in ['work', 'maintenance', 'attachment', 'unsupported_domain']
        )
        result = scope_coverage(scope, placement_capture_verified=False)
        self.assertEqual(len(result['domains']), 4)
        for row in result['domains']:
            self.assertFalse(row['coverage_complete'])
            self.assertEqual(row['historical_attribution'], 'unproven')
            if row['entity_kind'] != 'unsupported_domain':
                self.assertIn('bulk_insert', row['unqualified_writer_paths'])
            else:
                self.assertEqual(row['capture_state'], 'unqualified')

    def test_missing_capture_deduplication_and_exact_types(self):
        """Missing/ambiguous coordinates cannot become verified completeness."""
        result = scope_coverage(
            (self.source, self.source), placement_capture_verified=False
        )
        self.assertEqual(len(result['domains']), 1)
        self.assertEqual(
            result['domains'][0]['capture_state'], 'placement_capture_unavailable'
        )
        for scope, verified in [
            ([], False),
            ((), False),
            ((self.source,), 1),
            ((object(),), True),
        ]:
            with (
                self.subTest(scope_type=type(scope).__name__, verified=verified),
                self.assertRaises(ValueError),
            ):
                scope_coverage(scope, placement_capture_verified=verified)


from django.test import TestCase as DjangoTestCase

from integration_state import native_authority
from integration_state.tests.test_native_authority import NativeAuthorityFixture


class NativeCoverageReviewTests(NativeAuthorityFixture, DjangoTestCase):
    """Real native review binds the specific current incomplete impact shape."""

    def test_real_review_exposes_precise_scope_gaps_without_erasure(self):
        """A valid readiness approval remains a partial review, never deletion."""
        row = self.readiness()
        view = native_authority.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )
        impact = view['impact']
        self.assertEqual(impact['schema'], 'aimms.readiness-impact.v2')
        self.assertFalse(impact['coverage_complete'])
        self.assertFalse(impact['erasure_authorized'])
        self.assertEqual(
            impact['scope_coverage']['domains'][0]['entity_kind'], 'machine'
        )
        self.assertEqual(impact['scope_coverage']['domains'][0]['aspect'], 'metadata')
        self.assertEqual(
            impact['scope_coverage']['domains'][0]['capture_state'], 'unqualified'
        )
        self.approve(row)
        current = native_authority.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )
        self.assertEqual(current['review_status'], 'approved_review')
        self.assertFalse(current['impact']['erasure_authorized'])
