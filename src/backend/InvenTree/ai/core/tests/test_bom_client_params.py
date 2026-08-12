"""P8-W0c: the BOM API call must never send the ``inherited`` param.

``/bom/``'s ``inherited`` is a plain ``BomItem.inherited`` FIELD filter, not
"include ancestor lines" (that is already the ``part`` filter's default).
Sending ``inherited=true`` restricts results to lines flagged for variant
inheritance and silently drops ordinary BOM lines — the Phase 7 battery's
partial-BOM defect.
"""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.integrations.inventree.client import InvenTreeClient


def test_get_bom_sends_no_inherited_param() -> None:
    client = InvenTreeClient.__new__(InvenTreeClient)
    captured: dict = {}

    def fake_request(method, endpoint, params=None, **kwargs):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["params"] = params or {}
        return {"results": [{"pk": 1, "inherited": True}]}

    with patch.object(client, "_request", new=AsyncMock(side_effect=fake_request)):
        rows = asyncio.run(client.get_bom(42))

    assert captured["endpoint"] == "/bom/"
    assert "inherited" not in captured["params"]
    assert captured["params"]["part"] == 42
    # The rows still carry the per-line inherited flag for client-side use.
    assert rows[0]["inherited"] is True


def test_get_bom_signature_has_no_include_inherited() -> None:
    """The old kwarg must be gone so no caller can reintroduce the filter."""
    import inspect

    signature = inspect.signature(InvenTreeClient.get_bom)
    assert "include_inherited" not in signature.parameters
