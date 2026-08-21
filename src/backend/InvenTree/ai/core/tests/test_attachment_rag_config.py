"""R0 attachment-RAG configuration: dark defaults and fail-closed validators."""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import pytest
from ai.core.config import Settings
from pydantic import ValidationError


def _settings(**overrides) -> Settings:
    """Isolated settings keyed by env alias; the dev .env must not leak in."""
    return Settings(_env_file=None, **overrides)


def test_rag_flags_default_dark():
    s = _settings()
    assert s.feature_attachment_rag_ingest is False
    assert s.feature_attachment_rag_retrieval is False
    assert s.feature_media_rag_ingest is False
    assert s.feature_media_rag_retrieval is False
    assert s.azure_search_attachment_docs_index == "aimms-attachment-docs-v1"
    assert s.azure_search_media_index == "aimms-media-evidence-v1"
    assert s.cohere_embed_dimensions == 1536
    assert s.gemini_embed_dimensions == 3072
    assert s.rag_max_image_mb == 25


def test_attachment_flag_without_cohere_fails_closed():
    with pytest.raises(ValidationError, match="COHERE_EMBED_ENDPOINT"):
        _settings(FEATURE_ATTACHMENT_RAG_INGEST=True)


def test_attachment_retrieval_flag_also_fails_closed():
    with pytest.raises(ValidationError, match="COHERE_EMBED_ENDPOINT"):
        _settings(FEATURE_ATTACHMENT_RAG_RETRIEVAL=True)


def test_attachment_flag_requires_search_endpoint():
    with pytest.raises(ValidationError, match="AZURE_SEARCH_ENDPOINT"):
        _settings(
            FEATURE_ATTACHMENT_RAG_INGEST=True,
            COHERE_EMBED_ENDPOINT="https://example.models.ai.azure.com",
        )


def test_attachment_flag_with_complete_providers_boots():
    s = _settings(
        FEATURE_ATTACHMENT_RAG_INGEST=True,
        COHERE_EMBED_ENDPOINT="https://example.models.ai.azure.com",
        AZURE_SEARCH_ENDPOINT="https://example.search.windows.net",
    )
    assert s.feature_attachment_rag_ingest is True


def test_media_flag_without_gcp_fails_closed():
    with pytest.raises(ValidationError, match="GCP_PROJECT_ID"):
        _settings(FEATURE_MEDIA_RAG_INGEST=True)


def test_media_flag_requires_credentials_path():
    with pytest.raises(ValidationError, match="GCP_CREDENTIALS_PATH"):
        _settings(
            FEATURE_MEDIA_RAG_INGEST=True,
            GCP_PROJECT_ID="example-project",
            GCP_LOCATION="us-central1",
        )


def test_media_ingest_requires_caption_endpoint():
    with pytest.raises(ValidationError, match="AZURE_OPENAI_ENDPOINT"):
        _settings(
            FEATURE_MEDIA_RAG_INGEST=True,
            GCP_PROJECT_ID="example-project",
            GCP_LOCATION="us-central1",
            GCP_CREDENTIALS_PATH="/secrets/wif-external-account.json",
            AZURE_SEARCH_ENDPOINT="https://example.search.windows.net",
        )


def test_media_flag_with_complete_providers_boots():
    s = _settings(
        FEATURE_MEDIA_RAG_RETRIEVAL=True,
        GCP_PROJECT_ID="example-project",
        GCP_LOCATION="us-central1",
        GCP_CREDENTIALS_PATH="/secrets/wif-external-account.json",
        AZURE_SEARCH_ENDPOINT="https://example.search.windows.net",
    )
    assert s.feature_media_rag_retrieval is True
    assert s.gcp_auth_mode == "wif"


def test_rag_index_may_never_alias_the_governed_index():
    with pytest.raises(ValidationError, match="must be distinct"):
        _settings(
            AZURE_SEARCH_CONTROLLED_DOCUMENTS_INDEX="eaits-manuals-v4a",
            AZURE_SEARCH_ATTACHMENT_DOCS_INDEX="eaits-manuals-v4a",
        )


def test_rag_indexes_must_differ_from_each_other():
    with pytest.raises(ValidationError, match="must be distinct"):
        _settings(
            AZURE_SEARCH_ATTACHMENT_DOCS_INDEX="aimms-rag-shared",
            AZURE_SEARCH_MEDIA_INDEX="aimms-rag-shared",
        )


def test_video_overlap_must_be_smaller_than_segment():
    with pytest.raises(ValidationError, match="RAG_VIDEO_OVERLAP_S"):
        _settings(RAG_VIDEO_SEGMENT_S=60, RAG_VIDEO_OVERLAP_S=60)


def test_segment_length_respects_gemini_cap():
    with pytest.raises(ValidationError):
        _settings(RAG_VIDEO_SEGMENT_S=180)


def test_padded_governed_name_cannot_bypass_alias_guard():
    """F-01: whitespace must not defeat the trust-tier separation."""
    with pytest.raises(ValidationError, match="must be distinct"):
        _settings(
            AZURE_SEARCH_CONTROLLED_DOCUMENTS_INDEX=" eaits-manuals-v4a",
            AZURE_SEARCH_ATTACHMENT_DOCS_INDEX="eaits-manuals-v4a",
        )
    with pytest.raises(ValidationError, match="must be distinct"):
        _settings(
            AZURE_SEARCH_DOCUMENTS_INDEX="legacy-part-docs ",
            AZURE_SEARCH_MEDIA_INDEX=" legacy-part-docs",
        )


def test_governed_index_names_are_stripped():
    """The normalizer covers the governed/legacy names too (F-01)."""
    s = _settings(AZURE_SEARCH_CONTROLLED_DOCUMENTS_INDEX="  padded-name  ")
    assert s.azure_search_controlled_documents_index == "padded-name"


def test_stale_claim_default_and_floor():
    """The takeover horizon defaults above timeout+retry and refuses tiny values."""
    assert _settings().rag_stale_claim_s == 1800
    with pytest.raises(ValidationError):
        _settings(RAG_STALE_CLAIM_S=60)
