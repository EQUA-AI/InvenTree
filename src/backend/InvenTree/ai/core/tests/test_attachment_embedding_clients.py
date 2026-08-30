"""R0 embedding-client pins: batching, input types, and dimension fail-closed."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import pytest
from ai.core.integrations.embeddings_cohere import (
    COHERE_BATCH_LIMIT,
    AttachmentEmbeddingError,
    CohereEmbeddingClient,
)
from ai.core.integrations.embeddings_gemini import (
    GeminiEmbeddingClient,
    MediaEmbeddingError,
)


class _FakeCohereInner:
    """Records embed() calls and answers with fixed-width float vectors."""

    def __init__(self, dimensions: int = 8):
        self.dimensions = dimensions
        self.calls: list[dict] = []

    def embed(self, **kwargs):
        self.calls.append(kwargs)
        data = [
            SimpleNamespace(embedding=[0.5] * self.dimensions, index=i)
            for i in range(len(kwargs["input"]))
        ]
        return SimpleNamespace(data=data)


def _cohere(dimensions: int = 8, inner: _FakeCohereInner | None = None):
    client = CohereEmbeddingClient(
        endpoint="https://example.models.ai.azure.com",
        model="embed-v-4-0",
        dimensions=dimensions,
        api_key="test",
    )
    client._client = inner or _FakeCohereInner(dimensions=dimensions)
    return client


def test_cohere_batches_at_provider_limit():
    inner = _FakeCohereInner()
    client = _cohere(inner=inner)
    texts = [f"chunk {i}" for i in range(COHERE_BATCH_LIMIT * 2 + 5)]
    vectors = client.embed_documents(texts)
    assert len(vectors) == len(texts)
    assert [len(call["input"]) for call in inner.calls] == [96, 96, 5]
    assert {call["input_type"] for call in inner.calls} == {"document"}


def test_cohere_query_uses_query_input_type():
    inner = _FakeCohereInner()
    client = _cohere(inner=inner)
    vector = client.embed_query("where is the seal?")
    assert len(vector) == 8
    assert inner.calls[0]["input_type"] == "query"


def test_cohere_passes_model_and_dimensions():
    inner = _FakeCohereInner()
    client = _cohere(inner=inner)
    client.embed_documents(["one"])
    assert inner.calls[0]["model"] == "embed-v-4-0"
    assert inner.calls[0]["dimensions"] == 8


def test_cohere_refuses_dimension_drift():
    client = _cohere(dimensions=16, inner=_FakeCohereInner(dimensions=8))
    with pytest.raises(AttachmentEmbeddingError) as excinfo:
        client.embed_documents(["one"])
    assert excinfo.value.code == "ATTACHMENT_EMBEDDING_DIMENSION_DRIFT"


def test_cohere_refuses_non_float_payload():
    class _Base64Inner(_FakeCohereInner):
        def embed(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(embedding="AAAA", index=0)])

    client = _cohere(inner=_Base64Inner())
    with pytest.raises(AttachmentEmbeddingError) as excinfo:
        client.embed_documents(["one"])
    assert excinfo.value.code == "ATTACHMENT_EMBEDDING_MALFORMED"


def test_cohere_wraps_provider_errors_value_free():
    class _DownInner(_FakeCohereInner):
        def embed(self, **kwargs):
            raise RuntimeError("secret=abc123 leaked provider detail")

    client = _cohere(inner=_DownInner())
    with pytest.raises(AttachmentEmbeddingError) as excinfo:
        client.embed_documents(["one"])
    assert excinfo.value.code == "ATTACHMENT_EMBEDDING_FAILED"
    assert "secret" not in str(excinfo.value)


class _FakeGeminiModels:
    def __init__(self, dimensions: int = 8):
        self.dimensions = dimensions
        self.calls: list[dict] = []

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        # Reality, not convenience: gemini-embedding-2 mean-pools every part of
        # a request into ONE vector. The SDK folds a list of strings into a
        # single content, so a batched call returns one fused vector -- never
        # one per input. A fake that returned len(contents) would hide a
        # reintroduced batch behind a green test.
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.5] * self.dimensions)])


def _gemini(dimensions: int = 8, models: _FakeGeminiModels | None = None, **knobs):
    client = GeminiEmbeddingClient(
        project_id="example-project",
        location="us-central1",
        model="gemini-embedding-2-preview",
        dimensions=dimensions,
        **knobs,
    )
    client._client = SimpleNamespace(models=models or _FakeGeminiModels(dimensions=dimensions))
    return client


def test_gemini_embeds_texts_and_pins_model():
    models = _FakeGeminiModels()
    client = _gemini(models=models)
    vectors = client.embed_texts(["caption one", "caption two"])
    assert len(vectors) == 2
    # One request per text: batching would fuse them into a single vector.
    assert len(models.calls) == 2
    assert [call["contents"] for call in models.calls] == ["caption one", "caption two"]
    assert models.calls[0]["model"] == "gemini-embedding-2-preview"
    assert models.calls[0]["config"].output_dimensionality == 8


def test_gemini_omits_unset_knobs_from_the_payload():
    """R4 defaults must produce an R4-identical request.

    An explicitly-null field is not the same as an absent one, and the
    provider default for auto_truncate (silent truncation) is what shipped.
    """
    models = _FakeGeminiModels()
    client = _gemini(models=models)
    client.embed_query("nameplate")
    config = models.calls[0]["config"]
    assert config.output_dimensionality == 8
    assert getattr(config, "task_type", None) is None
    assert getattr(config, "audio_track_extraction", None) is None
    assert getattr(config, "auto_truncate", None) is None


def test_gemini_task_type_conditioning_is_opt_in():
    models = _FakeGeminiModels()
    client = _gemini(models=models, task_conditioning="task_type")
    client.embed_query("nameplate")
    assert models.calls[0]["config"].task_type == "RETRIEVAL_QUERY"
    assert models.calls[0]["contents"] == "nameplate"


def test_gemini_prefix_conditioning_rewrites_the_text_instead():
    """The two hypotheses are mutually exclusive on the wire."""
    models = _FakeGeminiModels()
    client = _gemini(models=models, task_conditioning="prefix")
    client.embed_query("nameplate")
    assert models.calls[0]["contents"] == "task: search result | query: nameplate"
    assert getattr(models.calls[0]["config"], "task_type", None) is None


def test_gemini_audio_knobs_reach_only_media_calls():
    """Text calls must not carry video-only knobs."""
    models = _FakeGeminiModels()
    client = _gemini(models=models, audio_track_extraction=True, auto_truncate=False)
    client.embed_query("nameplate")
    assert getattr(models.calls[0]["config"], "audio_track_extraction", None) is None

    client.embed_video_segment(b"clip", mime_type="video/mp4")
    media_config = models.calls[1]["config"]
    assert media_config.audio_track_extraction is True
    assert media_config.auto_truncate is False


def test_gemini_query_returns_single_vector():
    client = _gemini()
    assert len(client.embed_query("photo of the nameplate")) == 8


def test_gemini_refuses_dimension_drift():
    client = _gemini(dimensions=16, models=_FakeGeminiModels(dimensions=8))
    with pytest.raises(MediaEmbeddingError) as excinfo:
        client.embed_texts(["caption"])
    assert excinfo.value.code == "MEDIA_EMBEDDING_DIMENSION_DRIFT"


def test_gemini_embeds_image_bytes():
    models = _FakeGeminiModels()
    client = _gemini(models=models)
    vector = client.embed_image(b"\x89PNG fake", mime_type="image/png")
    assert len(vector) == 8
    assert models.calls[0]["contents"] is not None


def test_gemini_embeds_video_segment_bytes():
    """R4: one clip goes up as a bare Part (not a batch) at the pinned width."""
    models = _FakeGeminiModels()
    client = _gemini(models=models)
    vector = client.embed_video_segment(
        b"\x00\x00\x00\x18ftypmp42 fake clip", mime_type="video/mp4"
    )
    assert len(vector) == 8
    contents = models.calls[0]["contents"]
    assert contents is not None
    assert not isinstance(contents, list)
    assert models.calls[0]["config"].output_dimensionality == 8


def test_gemini_video_segment_refuses_cardinality_drift():
    """A multi-embedding response for one clip is malformed, never truncated."""

    class _TwoVectorModels(_FakeGeminiModels):
        def embed_content(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.5] * self.dimensions) for _ in range(2)]
            )

    client = _gemini(models=_TwoVectorModels())
    with pytest.raises(MediaEmbeddingError) as excinfo:
        client.embed_video_segment(b"clip bytes", mime_type="video/mp4")
    assert excinfo.value.code == "MEDIA_EMBEDDING_MALFORMED"


def test_gemini_wraps_provider_errors_value_free():
    class _DownModels(_FakeGeminiModels):
        def embed_content(self, **kwargs):
            raise RuntimeError("Bearer eyJ leaked token detail")

    client = _gemini(models=_DownModels())
    with pytest.raises(MediaEmbeddingError) as excinfo:
        client.embed_texts(["caption"])
    assert excinfo.value.code == "MEDIA_EMBEDDING_FAILED"
    assert "Bearer" not in str(excinfo.value)


def test_cohere_reorders_by_item_index():
    """F-20: out-of-order provider responses must re-pair correctly."""

    class _ShuffledInner(_FakeCohereInner):
        def embed(self, **kwargs):
            self.calls.append(kwargs)
            count = len(kwargs["input"])
            data = [
                SimpleNamespace(embedding=[float(i)] * self.dimensions, index=i)
                for i in reversed(range(count))
            ]
            return SimpleNamespace(data=data)

    client = _cohere(inner=_ShuffledInner())
    vectors = client.embed_documents(["a", "b", "c"])
    # Input i must get the vector the provider labeled index=i, regardless of
    # wire order.
    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


def test_cohere_records_the_provider_resolved_model():
    """R0-3: the model-pin registry learns what the endpoint actually serves."""
    from ai.core.integrations.model_pins import (
        _reset_resolved_models,
        resolved_model_versions,
    )

    class _NamedInner(_FakeCohereInner):
        def embed(self, **kwargs):
            response = super().embed(**kwargs)
            response.model = "embed-v-4-0-2026-01"
            return response

    _reset_resolved_models()
    try:
        client = _cohere(inner=_NamedInner())
        client.embed_documents(["one"])
        assert resolved_model_versions().get("embed-v-4-0") == "embed-v-4-0-2026-01"
    finally:
        _reset_resolved_models()


def test_cohere_close_releases_client():
    class _ClosableInner(_FakeCohereInner):
        closed = False

        def close(self):
            self.closed = True

    inner = _ClosableInner()
    client = _cohere(inner=inner)
    client.close()
    assert inner.closed is True
    assert client._client is None


def test_cohere_from_settings_refuses_missing_endpoint(monkeypatch):
    from types import SimpleNamespace as NS

    monkeypatch.setattr(
        "ai.core.config.get_settings",
        lambda: NS(
            cohere_embed_endpoint="",
            cohere_embed_model="m",
            cohere_embed_dimensions=8,
            cohere_embed_key="",
        ),
    )
    with pytest.raises(AttachmentEmbeddingError) as excinfo:
        CohereEmbeddingClient.from_settings()
    assert excinfo.value.code == "ATTACHMENT_EMBEDDING_CONFIG_INVALID"


def test_gemini_auth_mode_mismatch_refuses(tmp_path):
    """F-16: an SA key can never satisfy a WIF-mode deployment."""
    key_file = tmp_path / "sa.json"
    key_file.write_text('{"type": "service_account"}')
    client = GeminiEmbeddingClient(
        project_id="p",
        location="us-central1",
        model="gemini-embedding-2-preview",
        dimensions=8,
        credentials_path=str(key_file),
        auth_mode="wif",
    )
    with pytest.raises(MediaEmbeddingError) as excinfo:
        client._load_credentials()
    assert excinfo.value.code == "MEDIA_EMBEDDING_CONFIG_INVALID"


def test_gemini_never_mutates_process_environment(tmp_path, monkeypatch):
    """F-16: credentials are explicit; GOOGLE_APPLICATION_CREDENTIALS stays."""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    wif_file = tmp_path / "wif.json"
    wif_file.write_text(
        '{"type": "external_account", "audience": "x", "subject_token_type": "y",'
        ' "token_url": "https://sts.googleapis.com/v1/token",'
        ' "credential_source": {"file": "/tmp/none"}}'
    )
    client = GeminiEmbeddingClient(
        project_id="p",
        location="us-central1",
        model="gemini-embedding-2-preview",
        dimensions=8,
        credentials_path=str(wif_file),
        auth_mode="wif",
    )
    credentials = client._load_credentials()
    assert credentials is not None
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ


def test_gemini_unreadable_credentials_fail_closed(tmp_path):
    client = GeminiEmbeddingClient(
        project_id="p",
        location="us-central1",
        model="gemini-embedding-2-preview",
        dimensions=8,
        credentials_path=str(tmp_path / "missing.json"),
        auth_mode="sa_key",
    )
    with pytest.raises(MediaEmbeddingError) as excinfo:
        client._load_credentials()
    assert excinfo.value.code == "MEDIA_EMBEDDING_CONFIG_INVALID"
