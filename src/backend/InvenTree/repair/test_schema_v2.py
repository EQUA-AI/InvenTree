"""Tests for diagnosis schema v2: citations, provenance and v1 compatibility."""

from django.core.exceptions import ValidationError
from django.test import TestCase

from .schema import (
    DIAGNOSIS_SCHEMA_VERSION,
    RELATION_SUPPORTS,
    RELATION_UNKNOWN,
    STATUS_AVAILABLE,
    STATUS_INSUFFICIENT,
    STATUS_UNAVAILABLE,
    coerce_diagnosis,
    empty_diagnosis,
    is_preliminary,
    merge_regenerated,
    validate_diagnosis,
)

V1_BLOB = {
    'likely_cause': 'Seal face wear',
    'confidence': 0.8,
    'confidence_label': 'high',
    'alternatives': ['Bearing wear'],
    'evidence': ['Vibration rose from 3.1 to 7.4 mm/s over two weeks'],
    'confirm_tests': ['Take a spectrum reading at the drive end'],
    'failure_mode': None,
    'schema_version': 1,
}


class SchemaVersionTest(TestCase):
    """Both supported versions stay readable."""

    def test_empty_diagnosis_is_valid_v2(self):
        """The pre-generation blob validates and is explicitly unavailable."""
        blob = empty_diagnosis()
        validate_diagnosis(blob)
        self.assertEqual(blob['schema_version'], DIAGNOSIS_SCHEMA_VERSION)
        self.assertEqual(blob['status'], STATUS_UNAVAILABLE)

    def test_v1_blob_still_validates(self):
        """A packet diagnosed before the upgrade keeps rendering."""
        validate_diagnosis(V1_BLOB)

    def test_unsupported_version_is_rejected(self):
        """An unknown schema version fails rather than being guessed at."""
        with self.assertRaises(ValidationError):
            validate_diagnosis({**V1_BLOB, 'schema_version': 99})

    def test_v1_is_upgraded_on_coercion(self):
        """Coercing a v1 blob yields a valid v2 blob."""
        upgraded = coerce_diagnosis(V1_BLOB)

        validate_diagnosis(upgraded)
        self.assertEqual(upgraded['schema_version'], 2)
        self.assertEqual(upgraded['likely_cause'], 'Seal face wear')
        # A v1 blob that stated a cause had something to say.
        self.assertEqual(upgraded['status'], STATUS_AVAILABLE)


class EvidenceCitationTest(TestCase):
    """Every observation declares where it came from and what it implies."""

    def test_v1_prose_evidence_is_not_promoted_to_supporting(self):
        """An uncited claim stays 'unknown', never 'supports'."""
        upgraded = coerce_diagnosis(V1_BLOB)

        [item] = upgraded['evidence']
        self.assertIsNone(item['snapshot_id'])
        self.assertEqual(item['relation'], RELATION_UNKNOWN)
        self.assertIn('Vibration rose', item['observation'])

    def test_cited_evidence_keeps_its_snapshot_and_relation(self):
        """A properly cited observation survives coercion intact."""
        blob = coerce_diagnosis({
            **V1_BLOB,
            'evidence': [
                {
                    'snapshot_id': 'b4f3c2d1-0000-0000-0000-000000000001',
                    'observation': 'Vibration read 7.4 mm/s',
                    'relation': RELATION_SUPPORTS,
                    'stale': False,
                }
            ],
        })

        [item] = blob['evidence']
        self.assertEqual(item['relation'], RELATION_SUPPORTS)
        self.assertTrue(item['snapshot_id'])
        self.assertFalse(item['stale'])

    def test_unknown_relation_value_falls_back_to_unknown(self):
        """An unrecognized relation is not trusted as supporting."""
        blob = coerce_diagnosis({
            **V1_BLOB,
            'evidence': [{'observation': 'x', 'relation': 'proves'}],
        })
        self.assertEqual(blob['evidence'][0]['relation'], RELATION_UNKNOWN)

    def test_invalid_relation_fails_strict_validation(self):
        """Strict validation refuses an evidence item with a bad relation."""
        blob = coerce_diagnosis(V1_BLOB)
        blob['evidence'] = [{'observation': 'x', 'relation': 'proves'}]
        with self.assertRaises(ValidationError):
            validate_diagnosis(blob)


class UncitedAIDiagnosisTest(TestCase):
    """An AI diagnosis with no evidence must not read as trustworthy (S9)."""

    def test_ai_generated_empty_evidence_is_forced_insufficient(self):
        """Whatever wf7 claimed, no citations means insufficient + no confidence."""
        blob = coerce_diagnosis({
            'likely_cause': 'Confident-sounding but uncited cause',
            'confidence': 0.9,
            'status': STATUS_AVAILABLE,
            'evidence': [],
            'generator': 'wf7',
        })
        self.assertEqual(blob['status'], STATUS_INSUFFICIENT)
        self.assertEqual(blob['confidence'], 0.0)
        self.assertEqual(blob['confidence_label'], 'unknown')
        validate_diagnosis(blob)

    def test_ai_generated_cited_diagnosis_keeps_its_claim(self):
        """Real citations keep the declared status and confidence."""
        blob = coerce_diagnosis({
            'likely_cause': 'Bearing wear',
            'confidence': 0.8,
            'status': STATUS_AVAILABLE,
            'evidence': [
                {
                    'snapshot_id': 'machine:44@r7',
                    'observation': 'Temperature trend',
                    'relation': RELATION_SUPPORTS,
                }
            ],
            'generator': 'wf7',
        })
        self.assertEqual(blob['status'], STATUS_AVAILABLE)
        self.assertEqual(blob['confidence'], 0.8)

    def test_heuristic_generator_is_exempt(self):
        """The offline keyword fallback never claimed analysis.

        It keeps its low confidence and availability rather than masquerading
        as an AI abstention.
        """
        blob = coerce_diagnosis({
            'likely_cause': 'Suspected fault related to: pump noise',
            'confidence': 0.3,
            'evidence': [],
            'generator': 'heuristic',
        })
        self.assertEqual(blob['status'], STATUS_AVAILABLE)
        self.assertEqual(blob['confidence'], 0.3)


class VerificationTest(TestCase):
    """Preliminary until a person says otherwise."""

    def test_generated_output_is_preliminary(self):
        """Nothing a generator produces is a diagnosis on its own."""
        self.assertTrue(is_preliminary(coerce_diagnosis(V1_BLOB)))

    def test_non_dict_is_treated_as_preliminary(self):
        """Failing towards preliminary is the safe direction."""
        self.assertTrue(is_preliminary(None))
        self.assertTrue(is_preliminary('a string'))

    def test_verified_blob_is_no_longer_preliminary(self):
        """Explicit verification is what promotes it to a diagnosis."""
        blob = coerce_diagnosis({**V1_BLOB, 'verified_by_user': True})
        self.assertFalse(is_preliminary(blob))

    def test_regeneration_preserves_verification_and_amendments(self):
        """A later model run cannot retract what a technician recorded."""
        previous = coerce_diagnosis({
            **V1_BLOB,
            'verified_by_user': True,
            'verified_at': '2026-07-01T10:00:00+00:00',
            'verified_by': 7,
            'amendments': [{'note': 'Wear ring was also scored', 'by': 7}],
        })

        merged = merge_regenerated(
            previous, {**V1_BLOB, 'likely_cause': 'Bearing failure'}
        )

        self.assertEqual(merged['likely_cause'], 'Bearing failure')
        self.assertTrue(merged['verified_by_user'])
        self.assertEqual(merged['verified_by'], 7)
        self.assertEqual(len(merged['amendments']), 1)

    def test_regeneration_without_prior_verification_stays_preliminary(self):
        """Regeneration does not invent a verification that never happened."""
        merged = merge_regenerated(coerce_diagnosis(V1_BLOB), V1_BLOB)
        self.assertFalse(merged['verified_by_user'])
        self.assertTrue(is_preliminary(merged))
