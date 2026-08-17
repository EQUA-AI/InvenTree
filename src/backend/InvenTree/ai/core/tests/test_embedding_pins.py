"""S17 model/embedding pin tests: boot probes, stamps, and drift refusals."""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.config import Settings
from ai.core.integrations.model_pins import (
    ModelPinError,
    _reset_resolved_models,
    record_resolved_model,
    resolved_model_versions,
    run_boot_probes,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_resolved_models()
    yield
    _reset_resolved_models()


def _settings(**overrides) -> Settings:
    """Build isolated settings keyed by env alias; the env file must not leak in."""
    base = {
        "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
        "AZURE_SEARCH_ENDPOINT": "https://example.search.windows.net",
        "AZURE_SEARCH_CONTROLLED_DOCUMENTS_INDEX": "eaits-manuals-test",
        "CONTROLLED_DOCUMENT_EMBEDDING_DIMENSIONS": 8,
        "EMBEDDING_BOOT_PROBE_ENABLED": True,
        "MODEL_VERSION_BOOT_PROBE_ENABLED": False,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class _Embedder:
    def __init__(self, dimensions=8, model="text-embedding-3-large"):
        self.dimensions = dimensions
        self.model = model

    def embed_batch(self, inputs):
        record_resolved_model("text-embedding-3-large", self.model)
        return [[0.5] * self.dimensions for _ in inputs]


class _FailingEmbedder:
    def embed_batch(self, inputs):
        raise RuntimeError("network down")


def test_probe_verifies_matching_dimensions_and_index():
    report = run_boot_probes(
        settings=_settings(),
        embedding_client_factory=_Embedder,
        index_dimensions_reader=lambda _s: 8,
    )
    assert report == {
        "embedding": "verified",
        "index": "verified",
        "chat": "disabled",
        "attachment_embedding": "dark",
    }


def test_probe_refuses_dimension_drift():
    """An embedding plane producing the wrong width must abort startup."""
    with pytest.raises(ModelPinError) as excinfo:
        run_boot_probes(
            settings=_settings(),
            embedding_client_factory=lambda: _Embedder(dimensions=4),
            index_dimensions_reader=lambda _s: 8,
        )
    assert excinfo.value.code == "EMBEDDING_DIMENSION_DRIFT"


def test_probe_refuses_live_index_drift():
    """Config that disagrees with the live index vector width must abort."""
    with pytest.raises(ModelPinError) as excinfo:
        run_boot_probes(
            settings=_settings(),
            embedding_client_factory=_Embedder,
            index_dimensions_reader=lambda _s: 1536,
        )
    assert excinfo.value.code == "INDEX_DIMENSION_DRIFT"


def test_probe_degrades_when_index_schema_unreadable():
    """A data-plane-only credential reports unreadable, never a false verify."""
    report = run_boot_probes(
        settings=_settings(),
        embedding_client_factory=_Embedder,
        index_dimensions_reader=lambda _s: None,
    )
    assert report["embedding"] == "verified"
    assert report["index"] == "unreadable"


def test_probe_fails_closed_when_deployment_unreachable():
    """A boot that cannot embed must not limp into serving retrieval."""
    with pytest.raises(ModelPinError) as excinfo:
        run_boot_probes(
            settings=_settings(),
            embedding_client_factory=_FailingEmbedder,
            index_dimensions_reader=lambda _s: 8,
        )
    assert excinfo.value.code == "EMBEDDING_PROBE_UNREACHABLE"


def test_probe_skips_loudly_when_index_unconfigured(caplog):
    """The known dev posture (no controlled index) must boot, with a loud skip."""
    import logging

    with caplog.at_level(logging.WARNING, logger="ai.core.integrations.model_pins"):
        report = run_boot_probes(
            settings=_settings(AZURE_SEARCH_CONTROLLED_DOCUMENTS_INDEX=""),
            embedding_client_factory=_FailingEmbedder,
        )
    assert report["embedding"] == "skipped"
    assert any("unconfigured" in record.message for record in caplog.records)


def test_probe_disabled_flag_short_circuits():
    report = run_boot_probes(
        settings=_settings(EMBEDDING_BOOT_PROBE_ENABLED=False),
        embedding_client_factory=_FailingEmbedder,
    )
    assert report["embedding"] == "disabled"


def test_expected_embedding_model_pin_mismatch_is_fatal():
    with pytest.raises(ModelPinError) as excinfo:
        run_boot_probes(
            settings=_settings(AZURE_OPENAI_EXPECTED_EMBEDDING_MODEL="text-embedding-3-large"),
            embedding_client_factory=lambda: _Embedder(model="text-embedding-ada-002"),
            index_dimensions_reader=lambda _s: 8,
        )
    assert excinfo.value.code == "EMBEDDING_MODEL_PIN_MISMATCH"


def test_chat_probe_asserts_pin_per_deployment():
    probed = []

    def prober(settings, deployment, expected):
        probed.append((deployment, expected))
        record_resolved_model(deployment, "gpt-5.6-luna-2026-05-01")

    report = run_boot_probes(
        settings=_settings(
            MODEL_VERSION_BOOT_PROBE_ENABLED=True,
            AZURE_OPENAI_DEPLOYMENT="gpt-5.6-luna",
            AZURE_OPENAI_FAST_DEPLOYMENT="gpt-4.1",
            AZURE_OPENAI_EXPECTED_MODEL="gpt-5.6-luna-2026-05-01",
        ),
        embedding_client_factory=_Embedder,
        index_dimensions_reader=lambda _s: 8,
        chat_prober=prober,
    )
    assert report["chat"] == "verified"
    assert probed == [
        ("gpt-5.6-luna", "gpt-5.6-luna-2026-05-01"),
        ("gpt-4.1", ""),
    ]


def test_resolved_model_registry_round_trips():
    record_resolved_model("dep-a", "model-1")
    record_resolved_model("dep-a", "model-1")
    record_resolved_model("dep-b", "model-2")
    assert resolved_model_versions() == {"dep-a": "model-1", "dep-b": "model-2"}


def test_search_documents_and_manifest_carry_the_stamp():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from ai.core.integrations.controlled_document_ingestion import (
        build_ingestion_manifest,
        build_search_documents,
        chunk_markdown_sections,
        parse_markdown_sections,
    )

    sections = parse_markdown_sections("# Manual\n\nTorque the coupling to 45 Nm.\n")
    chunks = chunk_markdown_sections(sections)
    document = SimpleNamespace(
        pk=5,
        document_id="doc-1",
        revision="1.0",
        source_sha256="a" * 64,
        scope_key="site",
        access_class="maintenance_authorized",
        asset_id="",
        child_asset_id="",
        facility="",
        process_area="",
        work_order_id="",
        repair_packet_id="",
        document_class="technical_manual",
        source_filename="doc.md",
        source_location="/tmp/doc.md",
        revision_date=None,
    )
    rows = build_search_documents(
        document=document,
        chunks=chunks,
        indexed_at=datetime.now(UTC),
        embedding_model="text-embedding-3-large",
        embedding_dimensions=3072,
    )
    assert rows and all(
        row["embedding_model"] == "text-embedding-3-large" and row["embedding_dimensions"] == 3072
        for row in rows
    )
    manifest = build_ingestion_manifest(
        source_sha256="a" * 64,
        sections=sections,
        chunks=chunks,
        embedding_model="text-embedding-3-large",
        embedding_dimensions=3072,
    )
    assert manifest["embedding_model"] == "text-embedding-3-large"
    assert manifest["embedding_dimensions"] == 3072


def test_terminal_metadata_carries_model_versions():
    from ai.core.turn_service import _terminal_output_metadata

    assert _terminal_output_metadata({"a": 1}) == {"a": 1}
    record_resolved_model("dep", "model-x")
    stamped = _terminal_output_metadata({"a": 1})
    assert stamped["a"] == 1
    assert stamped["model_versions"] == {"dep": "model-x"}


# ---------------------------------------------------------------------------
# Attachment plane (Cohere Embed v4) pins — hardening pass, spec §9
# ---------------------------------------------------------------------------


def _attachment_settings(**overrides) -> Settings:
    """Governed base plus a lit attachment plane pinned at 8 dims."""
    base = {
        "FEATURE_ATTACHMENT_RAG_INGEST": True,
        "COHERE_EMBED_ENDPOINT": "https://cohere.example",
        "COHERE_EMBED_DIMENSIONS": 8,
    }
    base.update(overrides)
    return _settings(**base)


class _CohereEmbedder:
    def __init__(self, dimensions=8):
        self.dimensions = dimensions

    def embed_batch(self, inputs):
        record_resolved_model("embed-v-4-0", "embed-v-4-0-2026-01")
        return [[0.5] * self.dimensions for _ in inputs]


def test_attachment_probe_dark_when_flags_off():
    report = run_boot_probes(
        settings=_settings(),
        embedding_client_factory=_Embedder,
        index_dimensions_reader=lambda _s: 8,
    )
    assert report["attachment_embedding"] == "dark"


def test_attachment_probe_verifies_the_cohere_pin():
    report = run_boot_probes(
        settings=_attachment_settings(),
        embedding_client_factory=_Embedder,
        index_dimensions_reader=lambda _s: 8,
        attachment_embedding_client_factory=_CohereEmbedder,
    )
    assert report["attachment_embedding"] == "verified"
    assert resolved_model_versions()["embed-v-4-0"] == "embed-v-4-0-2026-01"


def test_attachment_probe_refuses_dimension_drift():
    with pytest.raises(ModelPinError) as excinfo:
        run_boot_probes(
            settings=_attachment_settings(),
            embedding_client_factory=_Embedder,
            index_dimensions_reader=lambda _s: 8,
            attachment_embedding_client_factory=lambda: _CohereEmbedder(dimensions=4),
        )
    assert excinfo.value.code == "EMBEDDING_DIMENSION_DRIFT"


def test_attachment_probe_unreachable_endpoint_is_fatal():
    with pytest.raises(ModelPinError) as excinfo:
        run_boot_probes(
            settings=_attachment_settings(),
            embedding_client_factory=_Embedder,
            index_dimensions_reader=lambda _s: 8,
            attachment_embedding_client_factory=_FailingEmbedder,
        )
    assert excinfo.value.code == "EMBEDDING_PROBE_UNREACHABLE"


def test_gemini_dimension_pin_is_drift_fatal():
    """The media plane has no resolved-model identity; width is the pin."""
    from types import SimpleNamespace

    from ai.core.integrations.embeddings_gemini import (
        GeminiEmbeddingClient,
        MediaEmbeddingError,
    )

    client = GeminiEmbeddingClient(
        project_id="p",
        location="us-central1",
        model="gemini-embedding-2-preview",
        dimensions=3072,
    )
    client._client = SimpleNamespace(
        models=SimpleNamespace(
            embed_content=lambda **_kwargs: SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.5] * 1536)]
            )
        )
    )
    with pytest.raises(MediaEmbeddingError) as excinfo:
        client.embed_texts(["probe"])
    assert excinfo.value.code == "MEDIA_EMBEDDING_DIMENSION_DRIFT"
