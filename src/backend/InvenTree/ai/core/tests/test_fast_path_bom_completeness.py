"""Synthetic BOM fast-path completeness controls over the actual client."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai.core.agents.routing import RoutingDecision, UnifiedRouter, WorkflowType
from ai.core.config import Settings
from ai.core.integrations.data_provider import LiveDataProviderAsync
from ai.core.integrations.inventree.client import InvenTreeClient
from ai.core.workflows.root import RootWorkflow
from django.test import SimpleTestCase


class FastPathBomCompletenessTests(SimpleTestCase):
    """Use native client and router with an entirely scripted transport."""

    def _setup(self, response):
        calls = []

        async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
            calls.append((method, endpoint, dict(params or {})))
            if endpoint == "/part/":
                return {"results": [{"pk": 1, "name": "Synthetic assembly"}], "next": None}
            if endpoint == "/bom/":
                if (params or {}).get("offset", 0):
                    return {"results": [{"pk": 101}], "count": 101, "next": None}
                return response
            self.fail("Unexpected endpoint")

        client = InvenTreeClient.__new__(InvenTreeClient)
        client._request = request
        provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
        provider._client = client
        router = UnifiedRouter()
        router.fast_path._data_provider = provider
        return router, calls

    def _read(self, response):
        router, calls = self._setup(response)
        result = asyncio.run(
            router.fast_path._execute_fast_path(
                "bom", "synthetic assembly", authorize_next_read=AsyncMock(return_value=True)
            )
        )
        return result, calls

    def test_missing_or_inconsistent_paging_facts_cannot_prove_complete_bom(self):
        rows = [{"pk": 1}, {"pk": 2}]
        for response in (
            {"results": rows, "count": 2},
            {"results": rows, "next": None},
            {"results": rows, "count": 3, "next": None},
            {"results": rows, "count": 2, "next": ""},
            {"results": rows, "count": True, "next": None},
            {"results": rows, "count": 2, "next": False},
            rows,
        ):
            with self.subTest(response=response):
                result, _ = self._read(response)
                self.assertIsNone(result)

    def test_partial_page_is_not_used_even_if_followup_can_be_fetched(self):
        rows = [{"pk": i + 1} for i in range(100)]
        result, calls = self._read({"results": rows, "count": 101, "next": "more"})
        self.assertIsNone(result)
        self.assertEqual([call[1] for call in calls], ["/part/", "/bom/"])

    def test_complete_empty_short_and_full_pages_are_preserved(self):
        for count in (0, 2, 99, 100):
            with self.subTest(count=count):
                rows = [{"pk": i + 1} for i in range(count)]
                result, calls = self._read({"results": rows, "count": count, "next": None})
                self.assertEqual(result["bom_items"], rows)
                self.assertEqual([call[1] for call in calls], ["/part/", "/bom/"])
                self.assertEqual(calls[1][2]["part"], 1)
                self.assertEqual((calls[1][2]["limit"], calls[1][2]["offset"]), (100, 0))
                self.assertNotIn("inherited", calls[1][2])

    def test_duplicate_missing_invalid_and_boolean_ids_are_not_complete(self):
        for rows in ([{"pk": 1}, {"pk": 1}], [{"pk": None}], [{}], [{"pk": True}], [{"pk": 0}]):
            with self.subTest(rows=rows):
                result, _ = self._read({"results": rows, "count": len(rows), "next": None})
                self.assertIsNone(result)

    def test_root_voice_falls_back_without_incomplete_bom_count(self):
        async def exercise():
            router, calls = self._setup({"results": [{"pk": 1}], "count": 1})
            router.semantic.route = AsyncMock(
                return_value=RoutingDecision(
                    workflow_type=WorkflowType.T1_LOOKUP,
                    confidence=1.0,
                    reasoning="synthetic fallback",
                )
            )

            class Conversations:
                def get_or_create_state(self, thread_id, user_id):
                    return SimpleNamespace()

                async def gather_context(self, query, thread_id, user_id):
                    return {"modality": "voice"}

            class Fallback:
                async def run_stream(self, **kwargs):
                    self.decision = kwargs["decision"]
                    yield "synthetic fallback"

            fallback = Fallback()
            root = RootWorkflow(
                router=router,
                registry=SimpleNamespace(get_workflow=lambda _: fallback),
                conversation_manager=Conversations(),
            )
            settings = Settings(
                _env_file=None,
                TURN_WALL_CLOCK_CAP_S=10,
                TURN_ROUTING_BUDGET_S=5,
                FEATURE_VOICE_FAST_PATH=True,
            )
            with (
                patch("ai.core.workflows.root.get_settings", return_value=settings),
                patch("ai.core.config.get_settings", return_value=settings),
                patch(
                    "ai.core.auth.get_current_principal", return_value=SimpleNamespace(user_pk="1")
                ),
                patch(
                    "ai.core.tools.invocation_guard.fresh_permission_profile",
                    new=AsyncMock(return_value=frozenset({("part", "view")})),
                ),
            ):
                chunks = [
                    chunk
                    async for chunk in root.run_stream(
                        "Show bom for synthetic assembly", thread_id="synthetic", user_id="1"
                    )
                ]
            self.assertEqual(chunks, ["synthetic fallback"])
            self.assertIsNone(fallback.decision.fast_path_result)
            self.assertEqual([call[1] for call in calls], ["/part/", "/bom/"])

        asyncio.run(exercise())

    def test_root_voice_preserves_complete_bom_answer(self):
        async def exercise():
            router, calls = self._setup({
                "results": [{"pk": 1}, {"pk": 2}],
                "count": 2,
                "next": None,
            })

            class Conversations:
                def get_or_create_state(self, thread_id, user_id):
                    return SimpleNamespace()

                async def gather_context(self, query, thread_id, user_id):
                    return {"modality": "voice"}

            root = RootWorkflow(router=router, registry=None, conversation_manager=Conversations())
            settings = Settings(
                _env_file=None,
                TURN_WALL_CLOCK_CAP_S=10,
                TURN_ROUTING_BUDGET_S=5,
                FEATURE_VOICE_FAST_PATH=True,
            )
            with (
                patch("ai.core.workflows.root.get_settings", return_value=settings),
                patch("ai.core.config.get_settings", return_value=settings),
                patch(
                    "ai.core.auth.get_current_principal", return_value=SimpleNamespace(user_pk="1")
                ),
                patch(
                    "ai.core.tools.invocation_guard.fresh_permission_profile",
                    new=AsyncMock(return_value=frozenset({("part", "view")})),
                ),
            ):
                chunks = [
                    chunk
                    async for chunk in root.run_stream(
                        "Show bom for synthetic assembly", thread_id="synthetic", user_id="1"
                    )
                ]
            self.assertEqual(chunks, ["Synthetic assembly has 2 BOM lines."])
            self.assertEqual([call[1] for call in calls], ["/part/", "/bom/"])

        asyncio.run(exercise())
