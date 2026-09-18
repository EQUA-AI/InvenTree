"""Deletion proof prevents exact claims and their known answer lineage returning."""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from pydantic import SecretStr

from aichat.services import memory_lifecycle
from aichat.services import memory_summary_guard as guard


class MemorySummaryGuardTests(SimpleTestCase):
    """Keyed proof and conservative limits require no provider or stored prose."""

    def setUp(self):
        """Use a disposable fingerprint key, never a configured secret."""
        self.enterContext(
            mock.patch.object(
                memory_lifecycle,
                'get_settings',
                return_value=SimpleNamespace(
                    aimms_memory_fingerprint_key=SecretStr('fixture-key-' * 4)
                ),
            )
        )
        self.proof = guard.SummaryMemoryGuard(
            frozenset({memory_lifecycle.claim_fingerprint('Pump seal worn')}),
            frozenset({'deleted-fact'}),
        )

    def test_matching_contiguous_claim_removes_whole_item_and_narrative(self):
        """Punctuation, case and surrounding words cannot evade exact proof."""
        body, hits = guard.scrub_body(
            {
                'machine_facts': ['Yesterday: PUMP seal worn.', 'Valve intact'],
                'narrative': 'An earlier diagnosis.',
            },
            self.proof,
        )
        self.assertEqual(hits, 1)
        self.assertEqual(body['machine_facts'], ['Valve intact'])
        self.assertEqual(body['narrative'], '')
        self.assertNotIn('Pump seal worn', repr(self.proof))
        self.assertTrue(guard.text_is_blocked('pump seal worn', self.proof))

    def test_deleted_answer_lineage_and_malformed_metadata_are_blocked(self):
        """An explicitly revived new fact cannot authorize the deleted old ID."""
        for values in [['deleted-fact'], 'bad', [None], ['safe'] * 13]:
            self.assertTrue(
                guard.source_is_blocked({'memory_fact_ids': values}, self.proof)
            )
        self.assertFalse(
            guard.source_is_blocked({'memory_fact_ids': ['new-fact']}, self.proof)
        )

    def test_budget_or_missing_key_withholds_entire_summary(self):
        """Resource bounds and unavailable fingerprint configuration fail closed."""
        with mock.patch.object(guard, 'MAX_COMPARISONS', 1):
            body, hits = guard.scrub_body(
                {'machine_facts': ['unrelated longer text']}, self.proof
            )
        self.assertEqual((body['machine_facts'], hits), ([], 1))
        with mock.patch.object(guard, 'claim_fingerprint', side_effect=ValueError):
            self.assertTrue(guard.text_is_blocked('any text', self.proof))
        self.assertTrue(
            guard.text_is_blocked('any text', guard.SummaryMemoryGuard(overflow=True))
        )
