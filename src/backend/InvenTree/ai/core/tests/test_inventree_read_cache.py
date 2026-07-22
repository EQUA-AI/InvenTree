"""Short-TTL GET read cache on the InvenTree client.

Opt-in (read_cache_ttl_s > 0). Fresh GETs are served without a round-trip;
different params miss; any write clears the cache; disabled by default.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

from ai.core.integrations.inventree.client import InvenTreeClient  # noqa: E402
from django.test import SimpleTestCase  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self.text = ""
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _CountingTransport:
    """Counts transport calls so cache hits (no call) are observable."""

    def __init__(self) -> None:
        self.calls = 0

    async def request(self, method, url, params=None, json=None):
        self.calls += 1
        return _FakeResponse({"n": self.calls})


def _client_with_transport(ttl: float):
    client = InvenTreeClient(base_url="https://inventree.invalid", token="t")
    client.read_cache_ttl_s = ttl
    transport = _CountingTransport()

    @asynccontextmanager
    async def _fake_get_client():
        yield transport

    client._get_client = _fake_get_client
    return client, transport


class ReadCacheTests(SimpleTestCase):
    def test_stable_key_is_param_order_independent(self):
        self.assertEqual(
            InvenTreeClient._read_cache_key("part/", {"a": 1, "b": 2}),
            InvenTreeClient._read_cache_key("part/", {"b": 2, "a": 1}),
        )

    def test_hit_miss_and_write_invalidation(self):
        client, transport = _client_with_transport(ttl=30.0)

        r1 = asyncio.run(client._request("GET", "/part/", params={"a": 1}))
        r2 = asyncio.run(client._request("GET", "/part/", params={"a": 1}))
        # Second identical GET is served from cache (no extra transport call).
        self.assertEqual(transport.calls, 1)
        self.assertEqual(r1, r2)

        # Different params -> cache miss.
        asyncio.run(client._request("GET", "/part/", params={"a": 2}))
        self.assertEqual(transport.calls, 2)

        # A write clears the cache...
        asyncio.run(client._request("POST", "/part/", json_data={}))
        self.assertEqual(transport.calls, 3)
        # ...so the original GET now misses.
        asyncio.run(client._request("GET", "/part/", params={"a": 1}))
        self.assertEqual(transport.calls, 4)

    def test_disabled_by_default(self):
        client, transport = _client_with_transport(ttl=0.0)
        asyncio.run(client._request("GET", "/part/", params={"a": 1}))
        asyncio.run(client._request("GET", "/part/", params={"a": 1}))
        # No caching: both GETs hit the transport.
        self.assertEqual(transport.calls, 2)
