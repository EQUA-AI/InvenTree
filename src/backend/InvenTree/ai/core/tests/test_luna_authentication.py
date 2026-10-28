"""Provider authentication selection for the bounded Luna diagnostic rail."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from ai.core.reasoning.luna_diagnostics import (
    LunaDiagnosticsAdapter,
    ProviderConfigurationError,
    ReasoningProviderConfig,
    ToolLoopBudget,
)


def _adapter(mode="direct_deployment"):
    return LunaDiagnosticsAdapter(
        provider_config=ReasoningProviderConfig(
            invocation_mode=mode,
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
            agent_name="diagnostic-agent",
            agent_version="3",
            direct_endpoint="https://luna.example.openai.azure.com",
            direct_deployment="pinned-luna-deployment",
            direct_api_version="2025-04-01-preview",
        ),
        budget=ToolLoopBudget(),
    )


def _settings(monkeypatch, **overrides):
    values = {"aimms_openai_keyless": False, "azure_openai_api_key": "synthetic-luna-key"}
    monkeypatch.setattr(
        "ai.core.config.get_settings", lambda: SimpleNamespace(**(values | overrides))
    )


def _identity(monkeypatch):
    identity = ModuleType("azure.identity")
    identity.DefaultAzureCredential = Mock(return_value="synthetic-credential")
    identity.get_bearer_token_provider = Mock(return_value="synthetic-token-provider")
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    return identity


def test_direct_key_mode_keeps_luna_endpoint_version_and_pin(monkeypatch):
    _settings(monkeypatch)
    constructor = Mock()
    monkeypatch.setattr("openai.AzureOpenAI", constructor)
    identity = _identity(monkeypatch)
    adapter = _adapter()

    assert adapter._get_client() is constructor.return_value
    constructor.assert_called_once_with(
        azure_endpoint="https://luna.example.openai.azure.com",
        api_version="2025-04-01-preview",
        api_key="synthetic-luna-key",
    )
    identity.DefaultAzureCredential.assert_not_called()
    identity.get_bearer_token_provider.assert_not_called()
    assert adapter.provider_config.direct_deployment == "pinned-luna-deployment"
    assert "synthetic-luna-key" not in repr(adapter.provider_config)


def test_direct_key_mode_never_reads_azure_identity(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr("openai.AzureOpenAI", Mock())

    class ForbiddenIdentity(ModuleType):
        def __getattr__(self, name):
            raise AssertionError(f"Key mode attempted identity access: {name}")

    monkeypatch.setitem(sys.modules, "azure.identity", ForbiddenIdentity("azure.identity"))
    _adapter()._get_client()


def test_direct_keyless_mode_does_not_fall_back_to_a_present_key(monkeypatch):
    _settings(monkeypatch, aimms_openai_keyless=True)
    constructor = Mock()
    monkeypatch.setattr("openai.AzureOpenAI", constructor)
    identity = _identity(monkeypatch)

    _adapter()._get_client()

    identity.DefaultAzureCredential.assert_called_once_with()
    identity.get_bearer_token_provider.assert_called_once_with(
        "synthetic-credential", "https://ai.azure.com/.default"
    )
    constructor.assert_called_once_with(
        azure_endpoint="https://luna.example.openai.azure.com",
        api_version="2025-04-01-preview",
        azure_ad_token_provider="synthetic-token-provider",
    )


def test_missing_key_fails_before_sdk_or_identity_dispatch(monkeypatch):
    _settings(monkeypatch, azure_openai_api_key="")
    constructor = Mock()
    monkeypatch.setattr("openai.AzureOpenAI", constructor)
    identity = _identity(monkeypatch)

    with pytest.raises(ProviderConfigurationError, match="API-key authentication"):
        _adapter()._get_client()

    constructor.assert_not_called()
    identity.DefaultAzureCredential.assert_not_called()


def test_agent_reference_keeps_explicit_project_identity(monkeypatch):
    _settings(monkeypatch)
    identity = _identity(monkeypatch)
    projects = ModuleType("azure.ai.projects")
    projects.AIProjectClient = Mock()
    monkeypatch.setitem(sys.modules, "azure.ai.projects", projects)

    client = _adapter("agent_reference")._get_client()

    identity.DefaultAzureCredential.assert_called_once_with()
    projects.AIProjectClient.assert_called_once_with(
        endpoint="https://example.services.ai.azure.com/api/projects/demo",
        credential="synthetic-credential",
    )
    assert client is projects.AIProjectClient.return_value.get_openai_client.return_value


def test_direct_client_is_cached_without_recreating_credentials(monkeypatch):
    _settings(monkeypatch)
    constructor = Mock()
    monkeypatch.setattr("openai.AzureOpenAI", constructor)
    adapter = _adapter()

    assert adapter._get_client() is adapter._get_client()
    constructor.assert_called_once()
