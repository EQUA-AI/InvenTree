"""Synthetic fast-path group totals must not use incomplete search pages."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai.core.agents.routing import FastPathRouter, RoutingDecision, UnifiedRouter, WorkflowType
from ai.core.config import Settings
from ai.core.integrations.data_provider import LiveDataProviderAsync
from ai.core.integrations.inventree.client import InvenTreeClient
from ai.core.workflows.root import RootWorkflow
from django.test import SimpleTestCase


class FastPathGroupCompletenessTests(SimpleTestCase):
    """Exercise the real router, provider and client with a scripted transport."""

    def _group(self, response):
        calls = []

        async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
            self.assertEqual((method, endpoint), ("GET", "/part/"))
            assert params is not None
            calls.append(params.copy())
            return response

        client = InvenTreeClient.__new__(InvenTreeClient)
        client._request = request
        provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
        provider._client = client
        router = FastPathRouter()
        router._data_provider = provider
        result = asyncio.run(
            router._execute_fast_path(
                "stock_group", "resistors", authorize_next_read=AsyncMock(return_value=True)
            )
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["limit"], 100)
        self.assertEqual(calls[0]["active"], "true")
        return result

    def test_short_server_capped_page_never_yields_a_complete_total(self):
        """A short page with more rows must not be formatted as a total."""
        rows = [{"pk": 1, "in_stock": 1}, {"pk": 2, "in_stock": 1}]
        self.assertIsNone(self._group({"results": rows, "count": 3, "next": "more"}))

    def test_known_complete_short_page_remains_a_valid_total(self):
        """A trustworthy complete first page retains its existing result."""
        rows = [{"pk": 1, "in_stock": 1}, {"pk": 2, "in_stock": 2}]
        result = self._group({"results": rows, "count": 2, "next": None})
        self.assertEqual(result["part_count"], 2)
        self.assertEqual(result["total_quantity"], 3)

    def test_missing_or_inconsistent_page_metadata_is_not_complete(self):
        """Unknown next, missing count and contradictory counts are not proof."""
        rows = [{"pk": 1, "in_stock": 1}, {"pk": 2, "in_stock": 1}]
        for response in (
            {"results": rows, "count": 2},
            {"results": rows, "next": None},
            {"results": rows, "count": 3, "next": None},
            {"results": rows, "count": 2, "next": ""},
            rows,
        ):
            with self.subTest(response=response):
                self.assertIsNone(self._group(response))

    def test_duplicate_part_identity_is_not_a_complete_population(self):
        """Repeated part identities cannot be used to total a population."""
        rows = [{"pk": 1, "in_stock": 1}, {"pk": 1, "in_stock": 1}]
        self.assertIsNone(self._group({"results": rows, "count": 2, "next": None}))

    def test_incomplete_group_does_not_fall_through_to_single_part_fast_path(self):
        """An overlapping pattern must not reinterpret a group as one part."""
        calls = []

        async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
            calls.append(endpoint)
            if endpoint == "/part/":
                return {
                    "results": [{"pk": 1, "in_stock": 1}, {"pk": 2, "in_stock": 1}],
                    "count": 3,
                    "next": "more",
                }
            if endpoint == "/stock/":
                return {"results": [{"quantity": 1}], "count": 1, "next": None}
            self.fail("Unexpected endpoint")

        client = InvenTreeClient.__new__(InvenTreeClient)
        client._request = request
        provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
        provider._client = client
        router = FastPathRouter()
        router._data_provider = provider
        with (
            patch("ai.core.auth.get_current_principal", return_value=SimpleNamespace(user_pk="1")),
            patch(
                "ai.core.tools.invocation_guard.fresh_permission_profile",
                new=AsyncMock(return_value=frozenset({("part", "view"), ("stock", "view")})),
            ),
        ):
            decision = asyncio.run(
                router.try_fast_path("How many resistors do we have in stock?", "synthetic")
            )
        self.assertIsNone(decision)
        self.assertEqual(calls, ["/part/"])

    def test_boundaries_below_at_and_above_requested_limit(self):
        """Exact count and terminal marker are required at the 100-item boundary."""
        for total in (99, 100, 101):
            rows = [{"pk": i + 1, "in_stock": 1} for i in range(min(total, 100))]
            result = self._group({
                "results": rows,
                "count": total,
                "next": "more" if total > 100 else None,
            })
            with self.subTest(total=total):
                if total > 100:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result["total_quantity"], total)

    def test_root_stream_does_not_emit_the_partial_group_total(self):
        """A short capped page reaches fallback without a fast-path answer."""

        async def exercise():
            calls = []

            async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
                assert params is not None
                calls.append((endpoint, dict(params)))
                return {
                    "results": [{"pk": 1, "in_stock": 1}, {"pk": 2, "in_stock": 1}],
                    "count": 3,
                    "next": "more",
                }

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
                FEATURE_VOICE_FAST_PATH=False,
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
                        "How many resistors do we have in stock?",
                        thread_id="synthetic",
                        user_id="1",
                    )
                ]
            self.assertEqual(chunks, ["synthetic fallback"])
            self.assertIsNone(fallback.decision.fast_path_result)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["offset"], 0)
            self.assertEqual(calls[0][1]["limit"], 100)

        asyncio.run(exercise())

    def test_root_stream_preserves_authorized_complete_group_answer(self):
        """A complete synthetic page still yields the native voice answer."""

        async def exercise():
            async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
                self.assertEqual(endpoint, "/part/")
                return {
                    "results": [{"pk": 1, "in_stock": 1}, {"pk": 2, "in_stock": 2}],
                    "count": 2,
                    "next": None,
                }

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
                FEATURE_VOICE_FAST_PATH=False,
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
                        "How many resistors do we have in stock?",
                        thread_id="synthetic",
                        user_id="1",
                    )
                ]
            self.assertEqual(chunks, ["We have 3 resistors in stock across 2 parts."])

        asyncio.run(exercise())
