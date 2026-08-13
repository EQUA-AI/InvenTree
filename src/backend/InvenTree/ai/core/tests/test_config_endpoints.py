"""S44: effective-config introspection + the retired runtime .env write."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core import app as ai_app
from fastapi import HTTPException


def test_data_switch_is_retired_with_410() -> None:
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(ai_app.switch_data_mode())
    assert excinfo.value.status_code == 410
    assert "USE_DEMO_DATASET" in excinfo.value.detail


def test_app_no_longer_imports_the_env_writer() -> None:
    """The runtime .env write must have no call site left in the app."""
    import inspect

    source = inspect.getsource(ai_app)
    assert "update_env_value" not in source


def test_redact_config_masks_secret_shaped_names() -> None:
    dumped = {
        "azure_openai_api_key": "sk-real",
        "azure_search_api_key": "also-real",
        "inventree_token": "tok",
        "feature_wf8_lookup": True,
        "nested": {"client_secret": "x", "endpoint": "https://ok"},
        "items": [{"password": "p"}],
    }
    redacted = {k: ai_app._redact_config(v, k) for k, v in dumped.items()}
    assert redacted["azure_openai_api_key"] == "***"
    assert redacted["azure_search_api_key"] == "***"
    assert redacted["inventree_token"] == "***"
    assert redacted["feature_wf8_lookup"] is True
    assert redacted["nested"]["client_secret"] == "***"
    assert redacted["nested"]["endpoint"] == "https://ok"
    assert redacted["items"][0]["password"] == "***"


def test_redact_config_name_markers_spare_non_string_flags() -> None:
    """feature_token_streaming and the token budgets are flags, not secrets —
    hiding them defeats the endpoint's purpose."""
    dumped = {
        "feature_token_streaming": False,
        "feature_token_budget_shadow": True,
        "ai_user_daily_token_budget": 250000,
    }
    redacted = {k: ai_app._redact_config(v, k) for k, v in dumped.items()}
    assert redacted == dumped


def test_redact_config_masks_secret_shaped_values() -> None:
    """A credential pasted into a non-secret-named field must not ship."""
    sas = "https://acct.search.windows.net/?sv=2024&sig=LIVECREDENTIAL123"
    userinfo = "https://user:hunter2@luna.internal/api"
    assert ai_app._redact_config(sas, "azure_search_endpoint") == "***"
    assert ai_app._redact_config(userinfo, "azure_luna_endpoint") == "***"
    assert (
        ai_app._redact_config("https://acct.openai.azure.com", "azure_openai_endpoint")
        == "https://acct.openai.azure.com"
    )


def test_effective_config_is_staff_gated_and_redacted() -> None:
    with (
        patch.object(ai_app, "_principal", return_value=SimpleNamespace(is_staff=False)),
        pytest.raises(HTTPException) as excinfo,
    ):
        asyncio.run(ai_app.effective_config())
    assert excinfo.value.status_code == 403

    with patch.object(ai_app, "_principal", return_value=SimpleNamespace(is_staff=True)):
        payload = asyncio.run(ai_app.effective_config())
    assert "settings" in payload and "registry" in payload
    # No secret material anywhere in the serialized settings.
    import json

    blob = json.dumps(payload["settings"]).lower()
    for needle in ("sk-", 'api-key":', "secretstr"):
        assert needle not in blob
    # Name markers mask STRING values only — feature_token_* booleans and
    # the token-budget integer are flags the endpoint exists to expose.
    key_fields = [
        name
        for name in payload["settings"]
        if any(m in name.upper() for m in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    ]
    assert key_fields, "expected at least one secret-shaped field in the dump"
    masked = 0
    for name in key_fields:
        value = payload["settings"][name]
        if isinstance(value, str) and value:
            assert value == "***", f"{name} leaked: {value!r}"
            masked += 1
    assert masked, "expected at least one masked string secret in the dump"
    # Registry metadata rides along for cross-plane visibility.
    env_names = {row["env_name"] for row in payload["registry"]}
    assert "FEATURE_THREAD_SHARING" in env_names
