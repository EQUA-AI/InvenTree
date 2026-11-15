"""Synthetic stock and location fast paths must reject incomplete pages."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai.core.agents.routing import FastPathRouter, RoutingDecision, UnifiedRouter, WorkflowType
from ai.core.config import Settings
from ai.core.integrations.data_provider import LiveDataProviderAsync
from ai.core.integrations.inventree.client import InvenTreeClient
from ai.core.workflows.root import RootWorkflow
from django.test import SimpleTestCase


class FastPathStockCompletenessTests(SimpleTestCase):
    """Use the real router/provider/client and a scripted transport."""

    def _result(self, category, count, stock_response=None):
        rows = [
            {
                "pk": i + 1,
                "quantity": 1,
                "location": i + 1,
                "location_detail": {"pk": i + 1, "name": f"bin-{i}"},
            }
            for i in range(count)
        ]
        calls = []

        async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
            assert params is not None
            calls.append((endpoint, params.copy()))
            if endpoint == "/part/":
                return {"results": [{"pk": 1, "name": "Synthetic item"}], "next": None}
            if endpoint == "/stock/":
                if stock_response is not None:
                    return stock_response
                return {
                    "results": rows[:100],
                    "count": count,
                    "next": "more" if count > 100 else None,
                }
            self.fail("Unexpected endpoint")

        client = InvenTreeClient.__new__(InvenTreeClient)
        client._request = request
        provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
        provider._client = client
        router = FastPathRouter()
        router._data_provider = provider
        result = asyncio.run(
            router._execute_fast_path(
                category, "synthetic item", authorize_next_read=AsyncMock(return_value=True)
            )
        )
        self.assertEqual([call[0] for call in calls], ["/part/", "/stock/"])
        self.assertEqual(calls[1][1]["part"], 1)
        self.assertEqual(calls[1][1]["offset"], 0)
        self.assertEqual(calls[1][1]["limit"], 100)
        return result

    def test_stock_page_with_more_items_is_not_a_total(self):
        self.assertIsNone(self._result("stock", 101))

    def test_location_page_with_more_items_is_not_a_complete_location_set(self):
        self.assertIsNone(self._result("location", 101))

    def test_complete_short_stock_and_location_pages_are_preserved(self):
        self.assertEqual(self._result("stock", 3)["total_quantity"], 3)
        self.assertEqual(len(self._result("location", 3)["locations"]), 3)

    def test_unknown_page_completeness_never_becomes_a_total_or_location_set(self):
        rows = [{"pk": 1, "quantity": 1, "location_detail": {"name": "bin"}}]
        for category in ("stock", "location"):
            for response in (
                {"results": rows, "count": 1},
                {"results": rows, "next": None},
                {"results": rows, "count": 2, "next": None},
                {"results": rows, "count": 1, "next": ""},
                rows,
            ):
                with self.subTest(category=category, response=response):
                    self.assertIsNone(self._result(category, 1, response))

    def test_duplicate_stock_ids_are_not_a_complete_population(self):
        rows = [{"pk": 1, "quantity": 1}, {"pk": 1, "quantity": 1}]
        for category in ("stock", "location"):
            with self.subTest(category=category):
                self.assertIsNone(
                    self._result(category, 2, {"results": rows, "count": 2, "next": None})
                )

    def test_missing_or_nonfinite_stock_quantities_are_not_successful_totals(self):
        """Missing or invalid quantity must not silently become zero."""
        for quantity in (None, True, float("nan"), float("inf"), "invalid"):
            row = {"pk": 1, "quantity": quantity}
            with self.subTest(quantity=repr(quantity)):
                self.assertIsNone(
                    self._result("stock", 1, {"results": [row], "count": 1, "next": None})
                )
        self.assertIsNone(
            self._result("stock", 1, {"results": [{"pk": 1}], "count": 1, "next": None})
        )

    def test_finite_quantities_with_overflowing_sum_are_not_a_success(self):
        """Finite components can still produce a non-finite aggregate."""
        huge = [{"pk": 1, "quantity": 1e308}, {"pk": 2, "quantity": 1e308}]
        self.assertIsNone(self._result("stock", 2, {"results": huge, "count": 2, "next": None}))

    def test_nonfinite_group_quantity_is_not_a_successful_total(self):
        """The group sibling also rejects non-finite arithmetic."""

        async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
            return {
                "results": [{"pk": 1, "in_stock": float("nan")}],
                "count": 1,
                "next": None,
            }

        client = InvenTreeClient.__new__(InvenTreeClient)
        client._request = request
        provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
        provider._client = client
        router = FastPathRouter()
        router._data_provider = provider
        self.assertIsNone(
            asyncio.run(
                router._execute_fast_path(
                    "stock_group", "synthetic", authorize_next_read=AsyncMock(return_value=True)
                )
            )
        )

    def test_empty_and_limit_boundary_pages(self):
        """Empty, 99 and 100 complete rows differ from 101 rows with more."""
        for category in ("stock", "location"):
            for count in (0, 99, 100, 101):
                with self.subTest(category=category, count=count):
                    result = self._result(category, count)
                    if count > 100:
                        self.assertIsNone(result)
                    elif category == "stock":
                        self.assertEqual(result["total_quantity"], count)
                    else:
                        self.assertEqual(len(result["locations"]), count)

    def test_root_voice_falls_back_for_partial_stock(self):
        """The root cannot emit a first-page sum as the full stock answer."""

        async def exercise():
            calls = []

            async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
                calls.append(endpoint)
                if endpoint == "/part/":
                    return {"results": [{"pk": 1, "name": "Synthetic item"}], "next": None}
                if endpoint == "/stock/":
                    return {
                        "results": [{"pk": i + 1, "quantity": 1} for i in range(100)],
                        "count": 101,
                        "next": "more",
                    }
                self.fail("Unexpected endpoint")

            class Conversations:
                def get_or_create_state(self, thread_id, user_id):
                    return SimpleNamespace()

                async def gather_context(self, query, thread_id, user_id):
                    return {"modality": "voice"}

            class Fallback:
                async def run_stream(self, **kwargs):
                    self.decision = kwargs["decision"]
                    yield "synthetic fallback"

            client = InvenTreeClient.__new__(InvenTreeClient)
            client._request = request
            provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
            provider._client = client
            router = UnifiedRouter()
            router.fast_path._data_provider = provider
            router.semantic.route = AsyncMock(
                return_value=RoutingDecision(
                    workflow_type=WorkflowType.T1_LOOKUP,
                    confidence=1.0,
                    reasoning="synthetic fallback",
                )
            )
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
                    new=AsyncMock(return_value=frozenset({("part", "view"), ("stock", "view")})),
                ),
            ):
                chunks = [
                    chunk
                    async for chunk in root.run_stream(
                        "How much stock of synthetic item?",
                        thread_id="synthetic",
                        user_id="1",
                    )
                ]
            self.assertEqual(chunks, ["synthetic fallback"])
            self.assertIsNone(fallback.decision.fast_path_result)
            self.assertEqual(calls, ["/part/", "/stock/"])

        asyncio.run(exercise())

    def test_root_voice_preserves_complete_stock_answer(self):
        """An authorized synthetic complete page still produces its voice answer."""

        async def exercise():
            async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
                if endpoint == "/part/":
                    return {"results": [{"pk": 1, "name": "Synthetic item"}], "next": None}
                if endpoint == "/stock/":
                    return {
                        "results": [{"pk": 1, "quantity": 1}, {"pk": 2, "quantity": 2}],
                        "count": 2,
                        "next": None,
                    }
                self.fail("Unexpected endpoint")

            class Conversations:
                def get_or_create_state(self, thread_id, user_id):
                    return SimpleNamespace()

                async def gather_context(self, query, thread_id, user_id):
                    return {"modality": "voice"}

            client = InvenTreeClient.__new__(InvenTreeClient)
            client._request = request
            provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
            provider._client = client
            router = UnifiedRouter()
            router.fast_path._data_provider = provider
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
                    "ai.core.auth.get_current_principal",
                    return_value=SimpleNamespace(user_pk="1"),
                ),
                patch(
                    "ai.core.tools.invocation_guard.fresh_permission_profile",
                    new=AsyncMock(return_value=frozenset({("part", "view"), ("stock", "view")})),
                ),
            ):
                chunks = [
                    chunk
                    async for chunk in root.run_stream(
                        "How much stock of synthetic item?",
                        thread_id="synthetic",
                        user_id="1",
                    )
                ]
            self.assertEqual(chunks, ["Synthetic item has 3 in stock."])

        asyncio.run(exercise())
