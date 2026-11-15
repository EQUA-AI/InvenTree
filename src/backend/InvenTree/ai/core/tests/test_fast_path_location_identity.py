"""Location fast-path results must describe distinct native locations."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai.core.agents.routing import FastPathRouter, UnifiedRouter
from ai.core.config import Settings
from ai.core.integrations.data_provider import LiveDataProviderAsync
from ai.core.integrations.inventree.client import InvenTreeClient
from ai.core.workflows.fast_path import format_fast_path_answer
from ai.core.workflows.root import RootWorkflow
from django.test import SimpleTestCase


class FastPathLocationIdentityTests(SimpleTestCase):
    """Exercise the real fast path with native-shaped scripted rows."""

    def _read(self, rows):
        async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
            if endpoint == "/part/":
                return {"results": [{"pk": 1, "name": "Synthetic item"}], "next": None}
            if endpoint == "/stock/":
                return {"results": rows, "count": len(rows), "next": None}
            self.fail("Unexpected endpoint")

        client = InvenTreeClient.__new__(InvenTreeClient)
        client._request = request
        provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
        provider._client = client
        router = FastPathRouter()
        router._data_provider = provider
        return asyncio.run(
            router._execute_fast_path(
                "location", "synthetic item", authorize_next_read=AsyncMock(return_value=True)
            )
        )

    def test_same_location_stock_rows_are_aggregated(self):
        rows = [
            {"pk": 1, "location": 9, "location_detail": {"pk": 9, "name": "Bay"}, "quantity": 1},
            {"pk": 2, "location": 9, "location_detail": {"pk": 9, "name": "Bay"}, "quantity": 2},
        ]
        result = self._read(rows)
        self.assertEqual(
            format_fast_path_answer(result), "Synthetic item is in Bay, with 3 on hand."
        )

    def test_distinct_locations_with_the_same_name_remain_distinct(self):
        rows = [
            {"pk": 1, "location": 9, "location_detail": {"pk": 9, "name": "Bay"}, "quantity": 1},
            {"pk": 2, "location": 10, "location_detail": {"pk": 10, "name": "Bay"}, "quantity": 2},
        ]
        result = self._read(rows)
        self.assertEqual(
            format_fast_path_answer(result), "Synthetic item is in 2 locations, including Bay."
        )

    def test_unknown_location_cannot_be_counted_as_a_known_location(self):
        rows = [{"pk": 1, "location": None, "location_detail": None, "quantity": 1}]
        self.assertIsNone(self._read(rows))

    def test_unverified_or_conflicting_location_identity_falls_back(self):
        """Missing, mismatched or inconsistent identities cannot establish a count."""
        for rows in (
            [{"pk": 1, "location": 9, "location_detail": {"name": "Bay"}, "quantity": 1}],
            [{"pk": 1, "location": 9, "location_detail": {"pk": 10, "name": "Bay"}, "quantity": 1}],
            [
                {
                    "pk": 1,
                    "location": 9,
                    "location_detail": {"pk": 9, "name": "Bay"},
                    "quantity": 1,
                },
                {
                    "pk": 2,
                    "location": 9,
                    "location_detail": {"pk": 9, "name": "Other"},
                    "quantity": 2,
                },
            ],
        ):
            with self.subTest(rows=rows):
                self.assertIsNone(self._read(rows))

    def test_invalid_location_quantity_does_not_become_zero(self):
        row = {"pk": 1, "location": 9, "location_detail": {"pk": 9, "name": "Bay"}}
        self.assertIsNone(self._read([row]))

    def test_root_voice_reports_one_location_for_two_rows_in_same_bay(self):
        """The real root emits the aggregated location with faked permission."""

        async def exercise():
            async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
                if endpoint == "/part/":
                    return {"results": [{"pk": 1, "name": "Synthetic item"}], "next": None}
                return {
                    "results": [
                        {
                            "pk": 1,
                            "location": 9,
                            "location_detail": {"pk": 9, "name": "Bay"},
                            "quantity": 1,
                        },
                        {
                            "pk": 2,
                            "location": 9,
                            "location_detail": {"pk": 9, "name": "Bay"},
                            "quantity": 2,
                        },
                    ],
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
                        "Where is synthetic item?", thread_id="synthetic", user_id="1"
                    )
                ]
            self.assertEqual(chunks, ["Synthetic item is in Bay, with 3 on hand."])

        asyncio.run(exercise())
