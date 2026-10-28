"""Local-provider contracts and fail-closed configuration."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ai.core.config import Settings
from ai.core.integrations import azure_openai_client, model_pins
from pydantic import ValidationError


def local_settings(**overrides):
    values = {
        "env": "development",
        "ai_provider": "local",
        "local_openai_base_url": "http://host.docker.internal:11434/v1",
        "local_openai_model": "llama3.2:latest",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_local_provider_is_explicit_and_uses_actual_model_names():
    assert Settings(_env_file=None).ai_provider == "azure"
    settings = local_settings()
    assert settings.azure_openai_deployment == "llama3.2:latest"
    assert settings.azure_openai_fast_deployment == "llama3.2:latest"
    assert settings.azure_luna_deployment == "llama3.2:latest"
    assert azure_openai_client.chat_provider_configured(settings)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:11434/v1",
        "http://169.254.169.254:11434/v1",
        "http://localhost.example.com:11434/v1",
        "http://user@localhost:11434/v1",
        "http://localhost:11434/v1?target=external",
        "http://localhost:11434/other",
        "https://localhost:11434/v1",
        "http://localhost:bad/v1",
    ],
)
def test_local_endpoint_rejects_external_or_ambiguous_urls(url):
    with pytest.raises(ValidationError):
        local_settings(local_openai_base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1/",
        "http://[::1]:11434/v1",
        "http://host.docker.internal:11434/v1",
        "http://forgeai-ollama:11434/v1",
    ],
)
def test_local_endpoint_accepts_only_allowlisted_hosts(url):
    assert local_settings(local_openai_base_url=url).local_openai_base_url.endswith("/v1")


def test_local_provider_rejects_production_and_missing_model():
    with pytest.raises(ValidationError):
        local_settings(env="production")
    with pytest.raises(ValidationError):
        local_settings(local_openai_model="")


def test_framework_and_raw_clients_use_the_local_model(monkeypatch):
    from ai.core.agents.factory import AgentSpec, build_agent

    settings = local_settings()
    monkeypatch.setattr(azure_openai_client, "get_settings", lambda: settings)
    chat = azure_openai_client.build_chat_client("irrelevant-alias", max_iterations=2)
    raw = azure_openai_client.build_openai_client(settings=settings)
    try:
        assert chat.model_id == "llama3.2:latest"
        assert chat.function_invocation_configuration.max_iterations == 2
        assert str(chat.client.base_url) == "http://host.docker.internal:11434/v1/"
        assert str(raw.base_url) == "http://host.docker.internal:11434/v1/"
        assert azure_openai_client.client_auth_mode(settings) == "local"
        agent = build_agent(
            AgentSpec(deployment="llama3.2:latest", instructions="Test", name="test")
        )
        assert agent is not None
    finally:
        raw.close()


@pytest.mark.parametrize(
    ("provider", "workflow", "requested_timeout", "expected_timeout"),
    [
        ("local", "wf8", 30.0, 90.0),
        ("local", "wf8", None, None),
        ("local", "wf2", 30.0, 30.0),
        ("azure", "wf8", 30.0, 30.0),
    ],
)
def test_only_local_wf8_uses_the_configured_transport_timeout(
    monkeypatch, provider, workflow, requested_timeout, expected_timeout
):
    from ai.core.agents import factory

    monkeypatch.setattr(
        factory,
        "get_settings",
        lambda: SimpleNamespace(ai_provider=provider, local_openai_timeout_s=90.0),
    )
    build_client = Mock(return_value=object())
    monkeypatch.setattr(factory, "build_chat_client", build_client)
    monkeypatch.setattr(factory, "ChatAgent", Mock())
    factory.build_agent(
        factory.AgentSpec(
            deployment="model",
            instructions="instructions",
            name="test",
            workflow=workflow,
            request_timeout_s=requested_timeout,
            request_max_retries=1,
        )
    )
    assert build_client.call_args.kwargs["request_timeout_s"] == expected_timeout
    assert build_client.call_args.kwargs["request_max_retries"] == 1


def test_local_wf8_configures_the_real_framework_sdk_timeout(monkeypatch):
    from ai.core.agents import factory

    settings = local_settings(local_openai_timeout_s=85.0)
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    monkeypatch.setattr(azure_openai_client, "get_settings", lambda: settings)
    agent = factory.build_agent(
        factory.AgentSpec(
            deployment="model",
            instructions="instructions",
            name="test",
            workflow="wf8",
            request_timeout_s=30.0,
            request_max_retries=1,
        )
    )
    assert agent.chat_client.client.timeout.read == pytest.approx(85.0)
    assert agent.chat_client.client.timeout.connect == pytest.approx(5.0)
    assert agent.chat_client.client.max_retries == 1


def test_keyless_memory_never_uses_the_local_provider(monkeypatch):
    settings = local_settings(azure_openai_endpoint="https://example.openai.azure.com")
    factory = Mock(return_value=object())
    monkeypatch.setattr("openai.AzureOpenAI", factory)
    monkeypatch.setattr(azure_openai_client, "_keyless_token_provider", lambda: lambda: "token")
    azure_openai_client.build_openai_client(settings=settings, require_keyless=True)
    assert factory.call_args.kwargs["azure_endpoint"] == "https://example.openai.azure.com"
    assert "azure_ad_token_provider" in factory.call_args.kwargs
    assert "api_key" not in factory.call_args.kwargs
    assert azure_openai_client.client_auth_mode(settings, require_keyless=True) == "keyless"


def test_local_boot_probe_is_required_and_pins_the_actual_model():
    settings = local_settings(model_version_boot_probe_enabled=False)
    probe = Mock()
    report = model_pins.run_boot_probes(settings=settings, chat_prober=probe)
    probe.assert_called_once_with(settings, "llama3.2:latest", "llama3.2:latest")
    assert report["chat"] == "verified"
    assert report["chat_provider"] == "local"


def test_local_context_alias_is_pinned_exactly(monkeypatch):
    model = "qwen3-aimms-local:8b-16k"
    settings = local_settings(local_openai_model=model)
    probe = Mock()
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)

    model_pins.run_boot_probes(settings=settings, chat_prober=probe)

    probe.assert_called_once_with(settings, model, model)
    assert model_pins._expected_identity(model) == model


@pytest.mark.parametrize("resolved", ["", "different-model"])
def test_local_boot_probe_rejects_missing_or_mismatched_identity(monkeypatch, resolved):
    settings = local_settings()
    client = Mock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = SimpleNamespace(
        model=resolved,
        choices=[SimpleNamespace(message=SimpleNamespace(content="pong", refusal=None))],
    )
    monkeypatch.setattr(azure_openai_client, "build_openai_client", lambda **_kwargs: client)
    with pytest.raises(model_pins.ModelPinError):
        model_pins._probe_chat_deployment(
            settings, settings.local_openai_model, settings.local_openai_model
        )
    client.close.assert_called_once()
    assert client.with_options.call_args.kwargs == {"timeout": 30.0, "max_retries": 0}


def test_local_boot_probe_rejects_an_empty_answer(monkeypatch):
    settings = local_settings()
    client = Mock()
    client.with_options.return_value = client
    client.chat.completions.create.return_value = SimpleNamespace(
        model=settings.local_openai_model, choices=[]
    )
    monkeypatch.setattr(azure_openai_client, "build_openai_client", lambda **_kwargs: client)
    with pytest.raises(model_pins.ModelPinError, match="no answer"):
        model_pins._probe_chat_deployment(
            settings, settings.local_openai_model, settings.local_openai_model
        )
    client.close.assert_called_once()


def test_local_intake_uses_provider_and_validates_json(monkeypatch):
    import json
    from unittest.mock import MagicMock

    from django.test import override_settings
    from repair.intake_binding import IntakeUnavailableError, draft_fields, provider_status

    settings = local_settings()
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    client = MagicMock()
    client.with_options.return_value.__enter__.return_value = client
    fields = {
        "title": "Inspect",
        "work_scope": "Review the reported symptom",
        "symptom": "Noise",
        "production_impact": "Unknown",
    }
    response = SimpleNamespace(
        id="local-run",
        model="llama3.2:latest",
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=json.dumps(fields), refusal=None),
            )
        ],
    )
    client.chat.completions.create.return_value = response
    monkeypatch.setattr(azure_openai_client, "build_openai_client", lambda: client)
    with override_settings(AIMMS_WORK_INTAKE_AI_ENABLED=True):
        assert provider_status()["available"]
        result, provenance = draft_fields({"symptom": "Noise"}, actor_id=1)
        assert result == fields
        assert provenance["model"] == "llama3.2:latest"
        assert client.with_options.call_args.kwargs == {"timeout": 90.0, "max_retries": 0}
        response.choices[0].message.content = '{"title":"unexpected"}'
        with pytest.raises(IntakeUnavailableError):
            draft_fields({}, actor_id=1)


def test_local_closeout_uses_pinned_model_and_validates_output(monkeypatch):
    import json

    from ai.core.capabilities import closeout_binding

    settings = local_settings()
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    client = Mock()
    client.with_options.return_value = client
    response = SimpleNamespace(
        id="local-run",
        model="llama3.2:latest",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    refusal=None,
                    content=json.dumps({
                        "schema_version": 1,
                        "fields": {},
                        "part_candidates": [],
                        "reading_candidates": [],
                        "warnings": [],
                    }),
                ),
            )
        ],
    )
    client.chat.completions.create.return_value = response
    monkeypatch.setattr(azure_openai_client, "build_openai_client", lambda **_kwargs: client)
    document = closeout_binding.extract("No work reported.", {})
    assert document.model_provenance["model"] == "llama3.2:latest"
    assert client.chat.completions.create.call_args.kwargs["model"] == "llama3.2:latest"
    assert client.with_options.call_args.kwargs == {"timeout": 90.0, "max_retries": 0}
    response.choices[0].message.content = "not JSON"
    with pytest.raises(ValueError):
        closeout_binding.extract("No work reported.", {})
