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
    """An httpx-shaped transport double recording requests at the WIRE level.

    R5 WP-A replaced ``azure.ai.inference.EmbeddingsClient`` with raw HTTP over
    the identical wire, so the seam moved from SDK kwargs to the request body.
    Asserting here is strictly stronger: the frozen contract is what the
    provider sees, not what a client library was asked for.
    """

    def __init__(self, dimensions: int = 8, status_code: int = 200):
        self.dimensions = dimensions
        self.status_code = status_code
        #: Decoded request bodies, in order — the old ``embed(**kwargs)`` shape.
        self.calls: list[dict] = []
        #: Full request records for the byte-level wire pin.
        self.requests: list[dict] = []

    def post(self, path, *, params=None, headers=None, json=None):
        import json as _json

        self.calls.append(json)
        self.requests.append({
            "path": path,
            "params": params or {},
            "headers": headers or {},
            "json": json,
        })
        if self.status_code != 200:
            return SimpleNamespace(
                status_code=self.status_code, json=lambda: {}, text="provider said no"
            )
        payload = {
            "model": "embed-v4.0",
            "data": [
                {"index": i, "embedding": [0.5] * self.dimensions}
                for i in range(len(json["input"]))
            ],
        }
        del _json
        return SimpleNamespace(status_code=200, json=lambda: payload)

    def close(self):
        self.closed = True


def _cohere(dimensions: int = 8, inner: _FakeCohereInner | None = None, **kw):
    client = CohereEmbeddingClient(
        endpoint="https://example.services.ai.azure.com",
        model="embed-v-4-0",
        dimensions=dimensions,
        api_key="test",
        **kw,
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
        def post(self, path, *, params=None, headers=None, json=None):
            self.calls.append(json)
            payload = {"model": "embed-v4.0", "data": [{"index": 0, "embedding": "AAAA"}]}
            return SimpleNamespace(status_code=200, json=lambda: payload)

    client = _cohere(inner=_Base64Inner())
    with pytest.raises(AttachmentEmbeddingError) as excinfo:
        client.embed_documents(["one"])
    assert excinfo.value.code == "ATTACHMENT_EMBEDDING_MALFORMED"


def test_cohere_wraps_provider_errors_value_free():
    class _DownInner(_FakeCohereInner):
        def post(self, path, *, params=None, headers=None, json=None):
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
        def post(self, path, *, params=None, headers=None, json=None):
            self.calls.append(json)
            count = len(json["input"])
            payload = {
                "model": "embed-v4.0",
                "data": [
                    {"index": i, "embedding": [float(i)] * self.dimensions}
                    for i in reversed(range(count))
                ],
            }
            return SimpleNamespace(status_code=200, json=lambda: payload)

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
        def post(self, path, *, params=None, headers=None, json=None):
            self.calls.append(json)
            payload = {
                "model": "embed-v-4-0-2026-01",
                "data": [{"index": 0, "embedding": [0.5] * self.dimensions}],
            }
            return SimpleNamespace(status_code=200, json=lambda: payload)

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


# ---------------------------------------------------------------------------
# R5 WP-A: the frozen wire contract
# ---------------------------------------------------------------------------


def test_cohere_wire_request_matches_the_frozen_sdk_contract():
    """Pin the exact request azure-ai-inference 1.0.0b9 used to send.

    This is the whole port claim in one assertion. The SDK was retired
    2026-08-26 and replaced with raw HTTP over the identical wire; the live
    swap was verified bit-identical (maxdiff 0.0) against it, which is only
    meaningful for as long as the request stays byte-for-byte the same.
    """
    inner = _FakeCohereInner()
    client = _cohere(inner=inner)
    client.embed_documents(["alpha", "beta"])

    request = inner.requests[0]
    assert request["path"] == "/embeddings"
    assert request["params"] == {"api-version": "2024-05-01-preview"}
    assert request["headers"]["Authorization"] == "Bearer test"
    assert request["json"] == {
        "input": ["alpha", "beta"],
        "model": "embed-v-4-0",
        "dimensions": 8,
        "input_type": "document",
        "encoding_format": "float",
    }


def test_cohere_query_and_document_are_asymmetric_on_the_wire():
    """`input_type` is honoured by the service, not decorative.

    Measured live before the port: cos(query, document) = 0.871 on the same
    text. A transport that dropped this field would silently halve retrieval
    quality with no error anywhere.
    """
    inner = _FakeCohereInner()
    client = _cohere(inner=inner)
    client.embed_documents(["a"])
    client.embed_query("a")
    assert [call["input_type"] for call in inner.calls] == ["document", "query"]


def test_cohere_api_version_is_configurable():
    """The lever for the real risk: Azure retiring the api-version."""
    inner = _FakeCohereInner()
    client = _cohere(inner=inner, api_version="2099-01-01")
    client.embed_documents(["a"])
    assert inner.requests[0]["params"] == {"api-version": "2099-01-01"}


def test_cohere_non_200_is_value_free():
    """A provider error body echoes the endpoint; it must never reach a fault."""
    client = _cohere(inner=_FakeCohereInner(status_code=503))
    with pytest.raises(AttachmentEmbeddingError) as excinfo:
        client.embed_documents(["one"])
    assert excinfo.value.code == "ATTACHMENT_EMBEDDING_FAILED"
    assert "provider said no" not in str(excinfo.value)


def test_cohere_managed_identity_mints_a_token_per_request():
    """Keyless is the deployed posture; a backfill outruns one token lifetime."""
    minted: list[str] = []

    def _provider() -> str:
        minted.append(f"tok{len(minted)}")
        return minted[-1]

    client = CohereEmbeddingClient(
        endpoint="https://example.services.ai.azure.com",
        model="embed-v-4-0",
        dimensions=8,
    )
    inner = _FakeCohereInner()
    client._client = inner
    client._token_provider = _provider

    client.embed_documents(["a"])
    client.embed_documents(["b"])
    assert minted == ["tok0", "tok1"]
    assert inner.requests[0]["headers"]["Authorization"] == "Bearer tok0"
    assert inner.requests[1]["headers"]["Authorization"] == "Bearer tok1"
