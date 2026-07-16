"""WS2-T6: exact Voice Live URL construction plus opt-in reachability.

URL exactness is deterministic and always runs. The reachability probe is
target-host evidence: set ``AIMMS_AZURE_INTEGRATION=1`` and run from the
approved hosting environment. The full authenticated WebSocket handshake is
WS4's control-connection integration test; this module proves DNS/TLS/HTTP
path and exact URL strings only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from ai.core.voice.endpoints import (
    GA_CONTROL_API_VERSION,
    WEBRTC_CALLS_API_VERSION,
    VoiceLiveEndpointError,
    build_calls_url,
    build_control_url,
)

MANIFEST_PATH = Path(__file__).parent / "azure_validation_manifest.example.json"
INTEGRATION_ENABLED = os.environ.get("AIMMS_AZURE_INTEGRATION") == "1"

HOST = "aimms-foundry.services.ai.azure.com"


def test_exact_ga_control_url():
    assert build_control_url(HOST, "gpt-4.1-mini") == (
        "wss://aimms-foundry.services.ai.azure.com/voice-live/realtime"
        "?api-version=2026-04-10&model=gpt-4.1-mini"
    )


def test_exact_preview_calls_url():
    assert build_calls_url(HOST, "gpt-4.1-mini") == (
        "wss://aimms-foundry.services.ai.azure.com/voice-live/realtime/calls"
        "?api-version=2026-01-01-preview&model=gpt-4.1-mini"
    )


def test_pinned_versions_are_defaults():
    assert GA_CONTROL_API_VERSION == "2026-04-10"
    assert WEBRTC_CALLS_API_VERSION == "2026-01-01-preview"
    assert "api-version=2026-04-10" in build_control_url(HOST, "m")
    assert "api-version=2026-01-01-preview" in build_calls_url(HOST, "m")


def test_scheme_prefixed_host_is_normalized():
    assert build_control_url(f"https://{HOST}", "m") == build_control_url(HOST, "m")


def test_model_is_query_encoded():
    url = build_control_url(HOST, "model with space&x=1")
    assert "model=model%20with%20space%26x%3D1" in url


@pytest.mark.parametrize(
    "bad_host",
    [
        "",
        "evil.example.com",
        "aimms-foundry.services.ai.azure.com.evil.example.com",
        f"user:pass@{HOST}",
        f"{HOST}:8443",
        f"{HOST}/extra/path",
        f"https://{HOST}/api/projects/Epcon-AIMMS",
    ],
)
def test_unsafe_hosts_are_rejected(bad_host):
    with pytest.raises(VoiceLiveEndpointError):
        build_control_url(bad_host, "gpt-4.1-mini")


def test_empty_model_is_rejected():
    with pytest.raises(VoiceLiveEndpointError):
        build_calls_url(HOST, "  ")


def test_legacy_cognitiveservices_host_is_accepted():
    url = build_control_url("legacy-res.cognitiveservices.azure.com", "m")
    assert url.startswith("wss://legacy-res.cognitiveservices.azure.com/")


@pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="target-host probe; set AIMMS_AZURE_INTEGRATION=1 on the approved host",
)
def test_target_host_https_reachability():
    """DNS/TLS/HTTP path to the manifest resource host from the target network."""
    import httpx

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    host = manifest["voice_live_resource_host"]
    response = httpx.get(f"https://{host}/", timeout=15.0, follow_redirects=False)
    # Any well-formed HTTP answer proves the network path; auth comes later.
    assert response.status_code in {200, 401, 403, 404, 405}
    location = response.headers.get("location", "")
    if location:
        assert ".azure.com" in location
