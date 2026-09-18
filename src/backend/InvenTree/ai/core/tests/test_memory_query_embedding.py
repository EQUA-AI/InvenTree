"""Authored query embedding admission/usage cases; provider calls are substituted."""

import time
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from ai.core.integrations.memory_providers import EmbeddingResult
from ai.core.memory.semantic_context import _query_vector
from ai.core.usage import TurnUsageLedger, turn_usage_ledger


class QueryEmbeddingTests(IsolatedAsyncioTestCase):
    """A foreground embedding needs a writable turn ledger and a bounded input."""

    def setUp(self):
        self.settings = SimpleNamespace(memory_embedding_deployment="fixture")
        self.ledger = TurnUsageLedger()
        token = turn_usage_ledger.set(self.ledger)
        self.addCleanup(turn_usage_ledger.reset, token)
        self.result = EmbeddingResult(vector=(1.0,) * 1536, profile="fixture:1536", attempted=True)

    async def test_one_call_is_conservatively_charged_without_persisting_query(self):
        """Repeated preparation never charges or calls a second embedding."""
        with (
            mock.patch("aichat.services.memory_policy.validate_source_text"),
            mock.patch(
                "ai.core.integrations.memory_providers.embed_memory",
                return_value=self.result,
            ) as provider,
        ):
            vector, reason = await _query_vector(
                "query fixture", settings=self.settings, deadline=time.perf_counter() + 1
            )
            self.assertEqual(vector, self.result.vector)
            self.assertEqual(reason, "available")
            vector, reason = await _query_vector(
                "query fixture", settings=self.settings, deadline=time.perf_counter() + 1
            )
        self.assertIsNone(vector)
        self.assertEqual(reason, "query_embedding_budget_unavailable")
        provider.assert_called_once()
        self.assertEqual(len(self.ledger.events), 1)
        self.assertEqual(self.ledger.events[0]["estimated"], 1)
        self.assertNotIn("query fixture", str(self.ledger.events))

    async def test_sensitive_or_oversized_query_never_calls_provider(self):
        """Policy exclusion and byte bounds are applied before any request."""
        with (
            mock.patch(
                "aichat.services.memory_policy.validate_source_text", side_effect=ValueError
            ),
            mock.patch(
                "ai.core.integrations.memory_providers.embed_memory",
                return_value=self.result,
            ) as provider,
        ):
            for text in ("private query", "x" * 2001):
                vector, _ = await _query_vector(
                    text, settings=self.settings, deadline=time.perf_counter() + 1
                )
                self.assertIsNone(vector)
        provider.assert_not_called()
        self.assertEqual(self.ledger.events, [])

    async def test_wrong_embedding_profile_is_unavailable_and_bound_is_retained(self):
        """A different deployment cannot silently rank the memory vector column."""
        with (
            mock.patch("aichat.services.memory_policy.validate_source_text"),
            mock.patch(
                "ai.core.integrations.memory_providers.embed_memory",
                return_value=EmbeddingResult(vector=(1.0,) * 1536, profile="other:1536"),
            ),
        ):
            vector, reason = await _query_vector(
                "query fixture", settings=self.settings, deadline=time.perf_counter() + 1
            )
        self.assertIsNone(vector)
        self.assertEqual(reason, "query_embedding_unavailable")
        self.assertEqual(self.ledger.events[0]["input_tokens"], len("query fixture") + 256)
