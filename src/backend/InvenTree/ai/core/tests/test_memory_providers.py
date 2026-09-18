"""Provider contract regressions; all network and identity seams are fakes."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from ai.core.integrations import memory_providers as providers


def _settings():
    return SimpleNamespace(
        memory_shield_endpoint="https://fixture.cognitiveservices.azure.com",
        memory_embedding_deployment="memory-fixture",
        memory_embedding_dims=1536,
    )


def _shield(monkeypatch, data):
    client = MagicMock()
    client.__enter__.return_value = client
    response = MagicMock()
    response.iter_bytes.return_value = [data]
    client.stream.return_value.__enter__.return_value = response
    monkeypatch.setattr(providers.httpx, "Client", lambda **_kwargs: client)
    monkeypatch.setattr(providers, "cognitive_token_provider", lambda: lambda: "fixture")
    return client


def test_shield_annotations_preserve_order(monkeypatch):
    """Flags are annotations; the provider cannot silently discard a document."""
    client = _shield(
        monkeypatch,
        b'{"documentsAnalysis":[{"attackDetected":true},{"attackDetected":false}]}',
    )
    result = providers.shield_documents(["one", "two"], settings=_settings())
    assert result.states == ("flagged", "clear")
    assert result.attempted
    assert client.stream.call_args.kwargs["json"] == {"documents": ["one", "two"]}


@pytest.mark.parametrize("body", [b"{}", b"bad", b'{"documentsAnalysis":[{}]}', b"x" * 65537])
def test_shield_invalid_responses_are_unavailable(monkeypatch, body):
    """Malformed/oversize responses cannot grant a clear verdict."""
    _shield(monkeypatch, body)
    result = providers.shield_documents(["one"], settings=_settings())
    assert result.states == ("unavailable",)
    assert result.error_code == "shield_unavailable"


def test_input_limits_precede_credentials(monkeypatch):
    """Oversize inputs and invalid endpoints never attempt auth or HTTP."""
    auth = MagicMock(side_effect=AssertionError("must not authenticate"))
    monkeypatch.setattr(providers, "cognitive_token_provider", auth)
    assert not providers.shield_documents(["x" * 10001], settings=_settings()).attempted
    settings = _settings()
    settings.memory_shield_endpoint = "https://fixture.cognitiveservices.azure.com@evil.test"
    assert not providers.shield_documents(["one"], settings=settings).attempted
    auth.assert_not_called()


@pytest.mark.parametrize("vector", [[1.0] * 1536, [0.0] * 1536, [float("nan")] * 1536, [1.0]])
def test_embedding_contract(monkeypatch, vector):
    """Only correctly shaped finite, nonzero vectors can leave the seam."""
    client = MagicMock()
    client.with_options.return_value.__enter__.return_value = client
    client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=vector)],
        usage=SimpleNamespace(prompt_tokens=8),
    )
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(providers, "build_openai_client", factory)
    settings = _settings()
    result = providers.embed_memory("ordinary maintenance fact", settings=settings)
    factory.assert_called_once_with(settings=settings, require_keyless=True)
    assert client.with_options.call_args.kwargs["max_retries"] == 0
    assert client.embeddings.create.call_args.kwargs["dimensions"] == 1536
    assert result.input_tokens == 8
    assert (result.vector is not None) == (len(vector) == 1536 and vector[0] == pytest.approx(1.0))


def test_embedding_redacts_before_call(monkeypatch):
    """Contact-shaped content is removed before crossing the provider seam."""
    client = MagicMock()
    client.with_options.return_value.__enter__.return_value = client
    monkeypatch.setattr(providers, "build_openai_client", lambda **_kwargs: client)
    providers.embed_memory("Contact private@example.test", settings=_settings())
    sent = client.embeddings.create.call_args.kwargs["input"][0]
    assert "private@example.test" not in sent
