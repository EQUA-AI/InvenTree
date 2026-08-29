"""S44: the flag registry is the single declaration point for both planes.

These tests are the "authority" part of one-config-authority: a flag added
to ``ai.core.config.Settings`` without a registry entry — or a registry
entry whose default disagrees with the Settings field — fails CI. The
Django plane's coverage is asserted structurally (every django-plane entry
names a config key and a supported kind); its live bridging is exercised by
the Django-runner suite (``aichat/tests/test_flag_bridge.py``).
"""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.config import Settings
from aimms_flags import REGISTRY, ai_flags, django_flags


def _accepted_env_names(field) -> set[str]:
    alias = getattr(field, "validation_alias", None) or getattr(field, "alias", None)
    if alias is None:
        return set()
    if isinstance(alias, str):
        return {alias}
    return {str(choice) for choice in getattr(alias, "choices", [])}


def test_every_ai_entry_matches_a_settings_field() -> None:
    fields = Settings.model_fields
    for entry in ai_flags():
        assert entry.ai_field, f"{entry.env_name}: ai-plane entry without ai_field"
        field = fields.get(entry.ai_field)
        assert field is not None, f"{entry.env_name}: no Settings field {entry.ai_field}"
        accepted = _accepted_env_names(field)
        assert entry.env_name in accepted, (
            f"{entry.env_name}: Settings field {entry.ai_field} accepts {accepted}"
        )
        assert field.default == entry.default, (
            f"{entry.env_name}: registry default {entry.default!r} != "
            f"Settings default {field.default!r}"
        )


def test_every_flag_shaped_settings_field_is_registered() -> None:
    """Closure: a new FEATURE_*/AIMMS_* Settings flag must join the registry."""
    registered_fields = {entry.ai_field for entry in ai_flags()}
    for name, field in Settings.model_fields.items():
        accepted = _accepted_env_names(field)
        flag_shaped = any(
            env.startswith("FEATURE_")
            or (env.startswith("AIMMS_") and env[len("AIMMS_") :].startswith("FEATURE_"))
            for env in accepted
        )
        if flag_shaped:
            assert name in registered_fields, (
                f"Settings.{name} ({sorted(accepted)}) has no aimms_flags entry"
            )


def test_prefixed_env_name_is_accepted_for_every_ai_flag() -> None:
    """The AIMMS_-prefixed form of every flag env name must also work."""
    for entry in ai_flags():
        field = Settings.model_fields[entry.ai_field]
        accepted = _accepted_env_names(field)
        prefixed = (
            entry.env_name if entry.env_name.startswith("AIMMS_") else f"AIMMS_{entry.env_name}"
        )
        assert prefixed in accepted, (
            f"{entry.env_name}: prefixed form {prefixed} not accepted ({accepted})"
        )


def test_django_entries_are_structurally_complete() -> None:
    for entry in django_flags():
        assert entry.config_key, f"{entry.env_name}: django entry without config_key"
        assert entry.kind in ("bool", "str", "csv", "int"), (
            f"{entry.env_name}: unsupported django kind {entry.kind}"
        )


def test_both_plane_defaults_agree() -> None:
    for entry in REGISTRY:
        if entry.planes != "both":
            continue
        field = Settings.model_fields[entry.ai_field]
        assert field.default == entry.default, (
            f"{entry.env_name}: both-plane defaults diverge "
            f"(registry {entry.default!r} vs ai {field.default!r})"
        )


def test_env_names_are_unique() -> None:
    names = [entry.env_name for entry in REGISTRY]
    assert len(names) == len(set(names))


#: Companion env each validator-coupled flag needs to round-trip alone. Any
#: new flag whose model validator demands providers MUST add its companions
#: here, or this parametrized check fails with the validator's own message.
_VOICE_COMPANIONS: dict[str, object] = {
    "AZURE_VOICELIVE_ENDPOINT": "aimms-foundry.services.ai.azure.com",
}

_COMPANION_ENV: dict[str, dict[str, object]] = {
    "FEATURE_VOICE_LIVE": dict(_VOICE_COMPANIONS),
    "FEATURE_VOICE_LIVE_WEBRTC": {
        **_VOICE_COMPANIONS,
        "FEATURE_VOICE_LIVE": True,
    },
    "FEATURE_VOICE_LIVE_RELAY": {
        **_VOICE_COMPANIONS,
        "FEATURE_VOICE_LIVE": True,
    },
    "FEATURE_VOICE_NATIVE_STS": {
        **_VOICE_COMPANIONS,
        "FEATURE_VOICE_LIVE": True,
        "AZURE_VOICELIVE_MODEL": "gpt-realtime",
        "AZURE_VOICELIVE_TRANSCRIPTION_MODEL": "whisper-1",
    },
    "FEATURE_VOICE_LIVE_DIAGNOSIS": {
        "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
    },
    "FEATURE_ATTACHMENT_RAG_INGEST": {
        "COHERE_EMBED_ENDPOINT": "https://cohere.example",
        "AZURE_SEARCH_ENDPOINT": "https://search.example",
    },
    "FEATURE_ATTACHMENT_RAG_RETRIEVAL": {
        "COHERE_EMBED_ENDPOINT": "https://cohere.example",
        "AZURE_SEARCH_ENDPOINT": "https://search.example",
    },
    "FEATURE_MEDIA_RAG_INGEST": {
        "GCP_PROJECT_ID": "example-project",
        "GCP_LOCATION": "us-central1",
        "GCP_CREDENTIALS_PATH": "/tmp/wif.json",
        "AZURE_SEARCH_ENDPOINT": "https://search.example",
        # The image path hard-depends on gpt-4o captions (R3 validator).
        "AZURE_OPENAI_ENDPOINT": "https://openai.example",
    },
    "FEATURE_MEDIA_RAG_RETRIEVAL": {
        "GCP_PROJECT_ID": "example-project",
        "GCP_LOCATION": "us-central1",
        "GCP_CREDENTIALS_PATH": "/tmp/wif.json",
        "AZURE_SEARCH_ENDPOINT": "https://search.example",
    },
}


@pytest.mark.parametrize(
    "entry",
    [e for e in ai_flags() if e.kind == "bool"],
    ids=lambda e: e.env_name,
)
def test_every_bool_flag_env_round_trips(entry) -> None:
    """EVERY bool flag instantiates live — not a positional sample.

    The previous [:5] slice silently excluded validator-coupled flags by
    registry order alone (review finding R0-7); companions make each flag
    constructible in isolation.
    """
    flipped = not entry.default
    env = {entry.env_name: flipped}
    env.update(_COMPANION_ENV.get(entry.env_name, {}))
    settings = Settings(_env_file=None, **env)
    assert getattr(settings, entry.ai_field) == flipped
