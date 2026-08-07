"""The AI client speaks the current InvenTree API dialect.

Upstream v430 replaced the part-scoped parameter endpoints with the generic
/api/parameter/ family, and several detail blocks the AI read tools consume
are opt-in per request (the view-level output options default them off).
These tests pin the request shapes at the transport layer, and a source scan
guards against reintroducing any endpoint the upstream API has removed.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.integrations.inventree.client import InvenTreeClient  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402


class _FakeResponse:
    def __init__(self) -> None:
        self.status_code = 200
        self.text = ""

    def json(self) -> dict:
        return {"results": []}


class _RecordingTransport:
    """Captures every request so URL/param shapes are assertable."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def request(self, method, url, params=None, json=None):
        self.requests.append({
            "method": method,
            "url": url,
            "params": params or {},
            "json": json,
        })
        return _FakeResponse()


def _client() -> tuple[InvenTreeClient, _RecordingTransport]:
    client = InvenTreeClient(base_url="https://inventree.invalid", token="t")
    transport = _RecordingTransport()

    @asynccontextmanager
    async def _fake_get_client():
        yield transport

    client._get_client = _fake_get_client
    return client, transport


class ParameterEndpointTests(SimpleTestCase):
    """Parameters go through the generic /api/parameter/ family."""

    def test_get_part_parameters_uses_generic_endpoint(self):
        """The client filters by (model_type, model_id), not a part param."""
        client, transport = _client()
        asyncio.run(client.get_part_parameters(42))

        request = transport.requests[0]
        self.assertEqual(request["url"], "parameter/")
        self.assertEqual(request["params"]["model_type"], "part")
        self.assertEqual(request["params"]["model_id"], 42)
        self.assertEqual(request["params"]["template_detail"], "true")


class DetailParamTests(SimpleTestCase):
    """Consumed *_detail blocks are requested explicitly (view defaults: off)."""

    def test_supplier_parts_request_consumed_detail_blocks(self):
        """tools.py reads supplier/part/manufacturer details and price breaks."""
        client, transport = _client()
        asyncio.run(client.get_supplier_parts(part_id=1))

        params = transport.requests[0]["params"]
        for key in (
            "part_detail",
            "supplier_detail",
            "manufacturer_detail",
            "price_breaks",
        ):
            self.assertEqual(params.get(key), "true", key)

    def test_bom_requests_sub_part_detail(self):
        """routing.py and tools.py read sub_part_detail from BOM rows."""
        client, transport = _client()
        asyncio.run(client.get_bom(1))

        params = transport.requests[0]["params"]
        self.assertEqual(params.get("sub_part_detail"), "true")
        self.assertEqual(params.get("part_detail"), "true")

    def test_build_allocations_request_stock_detail(self):
        """builds.py reads stock_item_detail, embedded only via stock_detail."""
        client, transport = _client()
        asyncio.run(client.get_build_order_allocations(1))

        self.assertEqual(transport.requests[0]["params"].get("stock_detail"), "true")

    def test_part_search_requests_category_detail(self):
        """routing.py reads category_detail from part rows."""
        client, transport = _client()
        asyncio.run(client.search_parts(query="widget"))

        self.assertEqual(transport.requests[0]["params"].get("category_detail"), "true")


class RemovedEndpointScanTests(SimpleTestCase):
    """No AI-stack source may reference an endpoint upstream has removed."""

    # Built by concatenation so this file does not match its own scan
    STALE_PATTERNS: ClassVar[list[str]] = [
        "part/param" + "eter",  # -> /api/parameter/ (v430)
        "/part/co" + "py/",  # -> POST /part/ with duplicate options (v514)
        '"/stock/serial' + 'ize/"',  # -> /stock/{pk}/serialize/
        '"/stock/inst' + 'all/"',  # -> /stock/{pk}/install/
        '"/stock/uninst' + 'all/"',  # -> /stock/{pk}/uninstall/
        '"/stock/conv' + 'ert/"',  # -> /stock/{pk}/convert/
        "/order/so-alloc" + "ation/",  # -> POST /order/so/{pk}/allocate/
        "part/attachme" + "nt/",  # -> generic /attachment/ (v207)
        "stock/attachme" + "nt/",  # -> generic /attachment/ (v207)
        "manufacturer/param" + "eter",  # ManufacturerPartParameter removed (v430)
    ]

    def test_no_removed_endpoints_referenced(self):
        """Walk every AI-stack python source for stale endpoint strings."""
        ai_root = Path(__file__).resolve().parents[1]
        offenders = []

        for path in ai_root.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in self.STALE_PATTERNS:
                if pattern in text:
                    offenders.append(f"{path.relative_to(ai_root)}: {pattern}")

        self.assertEqual(
            offenders,
            [],
            "Stale (removed) API endpoints referenced:\n" + "\n".join(offenders),
        )
