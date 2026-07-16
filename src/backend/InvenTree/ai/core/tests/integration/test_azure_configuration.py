"""WS2-T1: validate the secret-free Azure estate manifest and config alignment.

Deterministic — runs in the default suite with no network access.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from ai.core.config import Settings
from ai.core.voice import endpoints

MANIFEST_PATH = Path(__file__).parent / "azure_validation_manifest.example.json"

_SECRET_KEY_PATTERN = re.compile(r"(?i)(api[-_]?key|secret|password|token$|bearer)")
_JWT_PREFIX = "eyJ"

_INCOMPATIBLE_PAIRS = {
    # azure-speech transcription is documented for non-multimodal session
    # models only; realtime-family sessions need a gpt-4o-transcribe model.
    ("gpt-realtime", "azure-speech"),
    ("gpt-realtime-mini", "azure-speech"),
    ("gpt-realtime-1.5", "azure-speech"),
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    else:
        yield path, value


def test_manifest_has_required_shape(manifest):
    assert manifest["manifest_version"] == 1
    for key in (
        "foundry_project_endpoint",
        "voice_live_resource_host",
        "region",
        "identity",
        "voice_live",
        "reasoning",
        "network",
    ):
        assert key in manifest, f"manifest is missing required key {key!r}"
    assert isinstance(manifest["region"], str) and manifest["region"].strip()


def test_manifest_is_secret_free(manifest):
    for path, value in _walk(manifest):
        leaf = path.rsplit(".", 1)[-1]
        assert not _SECRET_KEY_PATTERN.search(leaf) or leaf == "token_scope", (
            f"manifest key {path!r} looks like it names a credential"
        )
        if isinstance(value, str):
            assert not value.startswith(_JWT_PREFIX), f"manifest value at {path!r} looks like a JWT"


def test_identity_contract(manifest):
    identity = manifest["identity"]
    assert identity["mode"] == "managed_identity"
    assert identity["token_scope"] == endpoints.TOKEN_SCOPE
    assert identity["required_roles"] == [
        "Cognitive Services User",
        "Foundry User",
    ]


def test_pinned_api_versions(manifest):
    voice_live = manifest["voice_live"]
    assert voice_live["control_api_version"] == endpoints.GA_CONTROL_API_VERSION
    assert voice_live["webrtc_calls_api_version"] == endpoints.WEBRTC_CALLS_API_VERSION


def test_session_transcriber_pair_is_compatible(manifest):
    voice_live = manifest["voice_live"]
    pair = (
        voice_live["session_model_candidate"],
        voice_live["transcription_model_candidate"],
    )
    assert pair not in _INCOMPATIBLE_PAIRS, (
        "azure-speech transcription cannot be paired with a realtime-family session model"
    )


def test_reasoning_agent_is_pinned(manifest):
    reasoning = manifest["reasoning"]
    assert reasoning["invocation_mode"] in {"agent_reference", "direct_deployment"}
    assert reasoning["agent_name"]
    version = reasoning["agent_version"]
    assert isinstance(version, str) and version.strip()
    assert version.lower() not in {"latest", "current", ""}, (
        "agent version must be pinned, never floating"
    )
    endpoint = manifest["foundry_project_endpoint"]
    assert endpoint.startswith("https://")
    host = endpoint.split("://", 1)[1].split("/", 1)[0]
    assert host.endswith((".services.ai.azure.com", ".cognitiveservices.azure.com"))


def test_manifest_aligns_with_settings_defaults(manifest):
    fields = Settings.model_fields
    aliases = {field.alias for field in fields.values() if field.alias}
    for required_alias in (
        "AZURE_FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_VOICE_AGENT_NAME",
        "AZURE_VOICE_AGENT_VERSION",
        "AZURE_LUNA_REASONING_EFFORT",
    ):
        assert required_alias in aliases, (
            f"Settings no longer exposes {required_alias}; manifest and config have drifted"
        )
    reasoning = manifest["reasoning"]
    assert fields["azure_voice_agent_name"].default == reasoning["agent_name"]
    assert fields["azure_voice_agent_version"].default == reasoning["agent_version"]
