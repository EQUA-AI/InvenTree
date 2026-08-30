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


def test_video_duration_cap_default_and_bounds():
    """R4: the duration cap is configurable only within the worker-safe bound."""
    assert _settings().rag_video_max_duration_s == 900
    with pytest.raises(ValidationError):
        _settings(RAG_VIDEO_MAX_DURATION_S=119)
    with pytest.raises(ValidationError):
        _settings(RAG_VIDEO_MAX_DURATION_S=901)


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


# ---------------------------------------------------------------------------
# R5: corpus-affecting knobs (WP-2)
# ---------------------------------------------------------------------------

#: A fully-provisioned media plane, so the R5 rules are what fails — not the
#: pre-existing provider-completeness legs.
_MEDIA_ON = {
    "FEATURE_MEDIA_RAG_INGEST": True,
    "GCP_PROJECT_ID": "example-project",
    "GCP_LOCATION": "us-central1",
    "GCP_CREDENTIALS_PATH": "/secrets/wif-external-account.json",
    "AZURE_SEARCH_ENDPOINT": "https://example.search.windows.net",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
}


def test_r5_corpus_knobs_default_to_r4_behaviour():
    """Defaults must reproduce R4 exactly, or the rollout re-embeds for nothing."""
    s = _settings()
    assert s.gemini_embed_task_conditioning == "off"
    assert s.gemini_audio_track_extraction is False
    assert s.gemini_auto_truncate is None  # tri-state: unset omits the field
    assert s.rag_video_caption_frames == 1


def test_r5_corpus_knobs_round_trip():
    s = _settings(
        GEMINI_EMBED_TASK_CONDITIONING="task_type",
        GEMINI_AUDIO_TRACK_EXTRACTION=True,
        GEMINI_AUTO_TRUNCATE=False,
        RAG_VIDEO_CAPTION_FRAMES=8,
        **_MEDIA_ON,
    )
    assert s.gemini_embed_task_conditioning == "task_type"
    assert s.gemini_audio_track_extraction is True
    assert s.gemini_auto_truncate is False
    assert s.rag_video_caption_frames == 8


def test_audio_extraction_requires_media_plane():
    with pytest.raises(ValidationError, match="require media RAG"):
        _settings(GEMINI_AUDIO_TRACK_EXTRACTION=True)


def test_auto_truncate_alone_requires_media_plane():
    """The tri-state must not become a back door around the media gate."""
    with pytest.raises(ValidationError, match="require media RAG"):
        _settings(GEMINI_AUTO_TRUNCATE=False)


def test_audio_extraction_refused_on_a_predict_routed_pin():
    """F1: the SDK silently DROPS audio on the PREDICT path.

    A deployment would believe narration was fused while nothing was sent.
    Refuse at boot instead of shipping a setting that is a no-op.
    """
    with pytest.raises(ValidationError, match="silently drop"):
        _settings(
            GEMINI_AUDIO_TRACK_EXTRACTION=True,
            GEMINI_EMBED_MODEL="gemini-embedding-001",
            **_MEDIA_ON,
        )


def test_audio_extraction_refused_beyond_a_sixty_second_segment():
    """60 s costs 6060 of 8192 tokens with audio; 120 s would truncate every window."""
    with pytest.raises(ValidationError, match="RAG_VIDEO_SEGMENT_S <= 60"):
        _settings(GEMINI_AUDIO_TRACK_EXTRACTION=True, RAG_VIDEO_SEGMENT_S=120, **_MEDIA_ON)


def test_audio_extraction_allowed_at_sixty_seconds():
    s = _settings(GEMINI_AUDIO_TRACK_EXTRACTION=True, RAG_VIDEO_SEGMENT_S=60, **_MEDIA_ON)
    assert s.gemini_audio_track_extraction is True


def test_task_conditioning_requires_media_plane():
    with pytest.raises(ValidationError, match="GEMINI_EMBED_TASK_CONDITIONING"):
        _settings(GEMINI_EMBED_TASK_CONDITIONING="task_type")


def test_task_conditioning_rejects_an_unknown_mode():
    with pytest.raises(ValidationError):
        _settings(GEMINI_EMBED_TASK_CONDITIONING="retrieval", **_MEDIA_ON)


def test_caption_frames_require_the_ingest_flag():
    """Retrieval alone never captions, so only the ingest flag binds."""
    with pytest.raises(ValidationError, match="RAG_VIDEO_CAPTION_FRAMES"):
        _settings(RAG_VIDEO_CAPTION_FRAMES=4)


def test_caption_frames_are_bounded():
    with pytest.raises(ValidationError):
        _settings(RAG_VIDEO_CAPTION_FRAMES=11, **_MEDIA_ON)


# ---------------------------------------------------------------------------
# R5 WP-A: the Cohere transport port
# ---------------------------------------------------------------------------

_COHERE_ON = {
    "FEATURE_ATTACHMENT_RAG_INGEST": True,
    "COHERE_EMBED_ENDPOINT": "https://example.services.ai.azure.com",
    "AZURE_SEARCH_ENDPOINT": "https://example.search.windows.net",
}


def test_cohere_api_version_defaults_to_the_frozen_sdk_value():
    """The retired SDK hardcoded this; the default must reproduce it exactly."""
    assert _settings().cohere_embed_api_version == "2024-05-01-preview"


def test_cohere_api_version_is_overridable():
    s = _settings(COHERE_EMBED_API_VERSION="2099-01-01", **_COHERE_ON)
    assert s.cohere_embed_api_version == "2099-01-01"


def test_cohere_endpoint_with_an_api_path_is_refused():
    """The client builds the route, so a path here yields /embeddings/embeddings."""
    with pytest.raises(ValidationError, match="without an API path"):
        _settings(**{
            **_COHERE_ON,
            "COHERE_EMBED_ENDPOINT": "https://x.services.ai.azure.com/embeddings",
        })


def test_cohere_endpoint_with_an_openai_path_is_refused():
    with pytest.raises(ValidationError, match="without an API path"):
        _settings(**{
            **_COHERE_ON,
            "COHERE_EMBED_ENDPOINT": "https://x.services.ai.azure.com/openai/v1",
        })


def test_cohere_resource_root_is_accepted():
    """Both live host shapes must pass; this is not a host allowlist."""
    for host in ("example.services.ai.azure.com", "example.models.ai.azure.com"):
        s = _settings(**{**_COHERE_ON, "COHERE_EMBED_ENDPOINT": f"https://{host}"})
        assert s.cohere_embed_endpoint == f"https://{host}"
