"""Synthetic identity controls for inventory fast-path part selection."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai.core.agents.routing import FastPathRouter, RoutingDecision, UnifiedRouter, WorkflowType
from ai.core.config import Settings
from ai.core.workflows.root import RootWorkflow
from django.test import SimpleTestCase


class FastPathPartIdentityTests(SimpleTestCase):
    """The real router uses synthetic provider operations only."""

    def _provider(self, selected, details=None):
        class Provider:
            search_parts = AsyncMock(return_value=[selected])
            get_part = AsyncMock(return_value=details)
            get_stock_page = AsyncMock(
                return_value={
                    "results": [],
                    "population_count": 0,
                    "has_more": False,
                }
            )
            get_bom_page = AsyncMock(
                return_value={
                    "results": [],
                    "population_count": 0,
                    "has_more": False,
                }
            )

        return Provider()

    def test_missing_or_mismatched_detail_cannot_become_part_answer(self):
        """A lookup result must identify the same positive native part."""
        for details in (None, {}, {"pk": True}, {"pk": 2, "name": "Other"}):
            with self.subTest(details=details):
                router = FastPathRouter()
                router._data_provider = self._provider({"pk": 1, "name": "Wanted"}, details)
                self.assertIsNone(
                    asyncio.run(
                        router._execute_fast_path(
                            "part", "wanted", authorize_next_read=AsyncMock(return_value=True)
                        )
                    )
                )

    def test_valid_part_details_remain_available(self):
        """A matching selected part and detail remain available."""
        details = {"pk": 1, "name": "Wanted", "description": "Synthetic"}
        router = FastPathRouter()
        router._data_provider = self._provider({"pk": 1, "name": "Wanted"}, details)
        result = asyncio.run(
            router._execute_fast_path(
                "part", "wanted", authorize_next_read=AsyncMock(return_value=True)
            )
        )
        self.assertEqual(result["part"], details)
        router._data_provider.get_part.assert_awaited_once_with(1)

    def test_invalid_search_identity_never_dispatches_a_dependent_read(self):
        """Malformed IDs cannot select a different part for stock, BOM or detail."""
        for category in ("part", "stock", "bom", "location"):
            for selected in ({}, {"pk": True}, {"pk": 0}, {"pk": "1"}):
                with self.subTest(category=category, selected=selected):
                    router = FastPathRouter()
                    provider = self._provider(selected, {"pk": 1})
                    router._data_provider = provider
                    self.assertIsNone(
                        asyncio.run(
                            router._execute_fast_path(
                                category, "wanted", authorize_next_read=AsyncMock(return_value=True)
                            )
                        )
                    )
                    provider.get_part.assert_not_awaited()
                    provider.get_stock_page.assert_not_awaited()
                    provider.get_bom_page.assert_not_awaited()

    def test_voice_falls_back_for_mismatched_detail_and_keeps_valid_detail(self):
        """The actual voice root does not announce another part's detail."""

        async def exercise(details):
            provider = self._provider({"pk": 1, "name": "Wanted"}, details)
            router = UnifiedRouter()
            router.fast_path._data_provider = provider
            router.semantic.route = AsyncMock(
                return_value=RoutingDecision(
                    workflow_type=WorkflowType.T1_LOOKUP, confidence=1.0, reasoning="fallback"
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
                        "Tell me about part wanted", thread_id="synthetic", user_id="1"
                    )
                ]
            return chunks, fallback

        chunks, fallback = asyncio.run(exercise({"pk": 2, "name": "Other"}))
        self.assertEqual(chunks, ["synthetic fallback"])
        self.assertIsNone(fallback.decision.fast_path_result)
        chunks, _ = asyncio.run(exercise({"pk": 1, "name": "Wanted"}))
        self.assertEqual(chunks, ["Wanted."])
