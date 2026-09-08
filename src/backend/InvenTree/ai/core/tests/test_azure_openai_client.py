"""M2 PR 7 (GR-23): the raw OpenAI client factory and its keyless seam.

Island tests, no database. ``azure.identity`` is replaced by a fake module
in ``sys.modules`` so no credential is ever created, and ``openai.AzureOpenAI``
is patched at the SDK attribute the factory looks up at call time.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest
from ai.core.integrations import azure_openai_client as builder


class _FakeAzureOpenAI:
    """Records the constructor kwargs; never talks to the network."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _settings(**overrides):
    base = {
        "azure_openai_endpoint": "https://example.openai.azure.com",
        "azure_openai_api_key": "test-key",
        "azure_openai_api_version": "2024-10-21",
        "aimms_openai_keyless": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _fresh_token_provider_cache():
    """The credential is process-cached; never let a fake outlive its test."""
    builder.reset_token_provider_cache()
    yield
    builder.reset_token_provider_cache()


@pytest.fixture
def fake_identity(monkeypatch):
    """A stand-in ``azure.identity`` that records what the factory asks for."""
    calls: dict = {"credentials": 0, "providers": []}

    class FakeCredential:
        def __init__(self):
            calls["credentials"] += 1

    def get_bearer_token_provider(credential, *scopes):
        calls["providers"].append((credential, scopes))
        return lambda: "fake-token"

    module = types.ModuleType("azure.identity")
    module.DefaultAzureCredential = FakeCredential
    module.get_bearer_token_provider = get_bearer_token_provider
    monkeypatch.setitem(sys.modules, "azure.identity", module)
    return calls, FakeCredential


def test_key_mode_passes_the_api_key_and_no_token_provider(monkeypatch):
    monkeypatch.setattr("openai.AzureOpenAI", _FakeAzureOpenAI)
    client = builder.build_openai_client(settings=_settings())
    assert isinstance(client, _FakeAzureOpenAI)
    assert client.kwargs == {
        "azure_endpoint": "https://example.openai.azure.com",
        "api_version": "2024-10-21",
        "api_key": "test-key",
    }
    assert "azure_ad_token_provider" not in client.kwargs


def test_key_mode_never_imports_azure_identity(monkeypatch):
    monkeypatch.setattr("openai.AzureOpenAI", _FakeAzureOpenAI)

    class Exploding(types.ModuleType):
        def __getattr__(self, name):
            raise AssertionError(f"azure.identity touched in key mode: {name}")

    monkeypatch.setitem(sys.modules, "azure.identity", Exploding("azure.identity"))
    builder.build_openai_client(settings=_settings())


def test_keyless_mode_passes_a_token_provider_and_no_api_key(monkeypatch, fake_identity):
    calls, FakeCredential = fake_identity
    monkeypatch.setattr("openai.AzureOpenAI", _FakeAzureOpenAI)
    client = builder.build_openai_client(settings=_settings(aimms_openai_keyless=True))
    assert "api_key" not in client.kwargs
    assert client.kwargs["azure_endpoint"] == "https://example.openai.azure.com"
    assert client.kwargs["api_version"] == "2024-10-21"
    assert client.kwargs["azure_ad_token_provider"]() == "fake-token"
    assert calls["credentials"] == 1
    credential, scopes = calls["providers"][0]
    assert isinstance(credential, FakeCredential)
    assert scopes == (builder.COGNITIVE_SERVICES_SCOPE,)
    assert builder.COGNITIVE_SERVICES_SCOPE == "https://cognitiveservices.azure.com/.default"


def test_keyless_credential_is_built_once_per_process(monkeypatch, fake_identity):
    """Plan §8.7 MI block: a process-cached credential, shared by every client.

    ``_summarize_with_bisect`` builds a client per attempt and the Django-Q
    worker runs many compactions; each must reuse the ONE credential so the
    chain probe and the token cache inside it are paid for once.
    """
    calls, _ = fake_identity
    monkeypatch.setattr("openai.AzureOpenAI", _FakeAzureOpenAI)
    keyless = _settings(aimms_openai_keyless=True)
    first = builder.build_openai_client(settings=keyless)
    second = builder.build_openai_client(settings=keyless)
    assert calls["credentials"] == 1
    assert len(calls["providers"]) == 1
    assert first.kwargs["azure_ad_token_provider"] is second.kwargs["azure_ad_token_provider"]
    assert first is not second  # the client itself is still per call


def test_reset_token_provider_cache_builds_a_fresh_credential(monkeypatch, fake_identity):
    calls, _ = fake_identity
    monkeypatch.setattr("openai.AzureOpenAI", _FakeAzureOpenAI)
    keyless = _settings(aimms_openai_keyless=True)
    builder.build_openai_client(settings=keyless)
    builder.reset_token_provider_cache()
    builder.build_openai_client(settings=keyless)
    assert calls["credentials"] == 2


def test_key_mode_does_not_populate_the_credential_cache(monkeypatch):
    monkeypatch.setattr("openai.AzureOpenAI", _FakeAzureOpenAI)
    builder.build_openai_client(settings=_settings())
    assert builder._TOKEN_PROVIDER is None


def test_factory_falls_back_to_get_settings(monkeypatch, fake_identity):
    monkeypatch.setattr("openai.AzureOpenAI", _FakeAzureOpenAI)
    monkeypatch.setattr(builder, "get_settings", lambda: _settings(aimms_openai_keyless=True))
    client = builder.build_openai_client()
    assert "azure_ad_token_provider" in client.kwargs
    assert "api_key" not in client.kwargs


def test_client_auth_mode_values(monkeypatch):
    assert builder.client_auth_mode(_settings()) == "key"
    assert builder.client_auth_mode(_settings(aimms_openai_keyless=True)) == "keyless"
    # Absent attribute (an older settings object) reads as key mode.
    assert builder.client_auth_mode(SimpleNamespace()) == "key"
    monkeypatch.setattr(builder, "get_settings", lambda: _settings(aimms_openai_keyless=True))
    assert builder.client_auth_mode() == "keyless"


def test_constructor_is_looked_up_at_call_time(monkeypatch):
    """A patch applied AFTER import is honoured — the seam existing tests rely on."""
    import openai

    seen: list[type] = []

    class First(_FakeAzureOpenAI):
        pass

    class Second(_FakeAzureOpenAI):
        pass

    for fake in (First, Second):
        monkeypatch.setattr(openai, "AzureOpenAI", fake)
        seen.append(type(builder.build_openai_client(settings=_settings())))
    assert seen == [First, Second]


def test_setting_defaults_dark():
    from ai.core.config import Settings

    settings = Settings(_env_file=None)
    assert settings.aimms_openai_keyless is False
    assert Settings(_env_file=None, AIMMS_OPENAI_KEYLESS="1").aimms_openai_keyless is True
