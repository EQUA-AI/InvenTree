"""Synthetic native-filter witness for fast-path stock totals."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai.core.agents.routing import RoutingDecision, UnifiedRouter, WorkflowType
from ai.core.config import Settings
from ai.core.integrations.data_provider import LiveDataProviderAsync
from ai.core.integrations.inventree.client import InvenTreeClient
from ai.core.workflows.root import RootWorkflow
from django.test import TestCase
from part.models import Part
from stock.api import StockFilter
from stock.models import StockItem
from stock.status_codes import StockStatus


class FastPathNativeStockStatusTests(TestCase):
    """Native stock eligibility and the actual fast-path answer must agree."""

    @patch("django_q.tasks.AsyncTask.run", return_value=None)
    def test_voice_stock_total_excludes_native_destroyed_stock(self, _offload):
        """A terminal page containing destroyed stock must not become on-hand stock."""
        part = Part.objects.create(name="Synthetic eligible item")
        StockItem.objects.create(part=part, quantity=2, status=StockStatus.OK.value)
        StockItem.objects.create(part=part, quantity=5, status=StockStatus.DESTROYED.value)
        all_rows = list(
            StockFilter(data={"part": str(part.pk)}, queryset=StockItem.objects.all()).qs
        )
        eligible_rows = list(
            StockFilter(
                data={"part": str(part.pk), "in_stock": "true"}, queryset=StockItem.objects.all()
            ).qs
        )
        self.assertEqual(sum(row.quantity for row in all_rows), 7)
        self.assertEqual(sum(row.quantity for row in eligible_rows), 2)
        calls = []

        async def request(method, endpoint, params=None, **kwargs):  # noqa: RUF029
            if endpoint == "/part/":
                return {"results": [{"pk": part.pk, "name": part.name}], "next": None}
            assert endpoint == "/stock/" and params is not None
            calls.append(dict(params))
            selected = eligible_rows if params.get("in_stock") == "true" else all_rows
            rows = [
                {"pk": row.pk, "part": part.pk, "quantity": float(row.quantity)} for row in selected
            ]
            return {"results": rows, "count": len(rows), "next": None}

        class Conversations:
            def get_or_create_state(self, thread_id, user_id):
                return SimpleNamespace()

            async def gather_context(self, query, thread_id, user_id):
                return {"modality": "voice"}

        class Fallback:
            async def run_stream(self, **kwargs):
                yield "synthetic fallback"

        async def execute():
            client = InvenTreeClient.__new__(InvenTreeClient)
            client._request = request
            provider = LiveDataProviderAsync.__new__(LiveDataProviderAsync)
            provider._client = client
            router = UnifiedRouter()
            router.fast_path._data_provider = provider
            router.semantic.route = AsyncMock(
                return_value=RoutingDecision(
                    workflow_type=WorkflowType.T1_LOOKUP, confidence=1.0, reasoning="fallback"
                )
            )
            root = RootWorkflow(
                router=router,
                registry=SimpleNamespace(get_workflow=lambda _: Fallback()),
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
                return [
                    chunk
                    async for chunk in root.run_stream(
                        "How much stock of synthetic eligible item?",
                        thread_id="synthetic",
                        user_id="1",
                    )
                ]

        chunks = asyncio.run(execute())
        self.assertEqual(chunks, ["Synthetic eligible item has 2.0 in stock."])
        self.assertEqual(calls[0].get("in_stock"), "true")
