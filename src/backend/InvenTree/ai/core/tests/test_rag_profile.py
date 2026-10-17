"""R5 corpus profile strings: the homogeneity spine (WP-2)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

from ai.core.config import Settings
from ai.core.integrations.rag_profile import (
    BASELINE_PROFILE,
    media_embedding_profile,
    text_embedding_profile,
)

_MEDIA_ON = {
    "FEATURE_MEDIA_RAG_INGEST": True,
    "GCP_PROJECT_ID": "example-project",
    "GCP_LOCATION": "us-central1",
    "GCP_CREDENTIALS_PATH": "/secrets/wif-external-account.json",
    "AZURE_SEARCH_ENDPOINT": "https://example.search.windows.net",
    "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
}


def _settings(**overrides) -> Settings:
    # R5 note: a bare construction now means DEGRADED defaults (default-on
    # flags knocked dark for missing providers) — still the v1 baseline
    # profile, because the profile hashes knobs, not flags.
    """Isolated settings keyed by env alias; the dev .env must not leak in."""
    return Settings(_env_file=None, **overrides)


def test_defaults_are_the_baseline_profile():
    """The contract: R4 defaults re-project to byte-identical documents.

    If this ever drifts, a deployment that changed nothing would be told its
    whole corpus is stale and would re-embed for no reason.
    """
    s = _settings()
    assert media_embedding_profile(s) == BASELINE_PROFILE == "v1"
    assert text_embedding_profile(s) == "v1"


def test_baseline_matches_the_column_db_default():
    """Pre-R5 rows carry the column default; they must not read as stale."""
    assert BASELINE_PROFILE == "v1"


def test_audio_extraction_changes_the_profile():
    s = _settings(GEMINI_AUDIO_TRACK_EXTRACTION=True, **_MEDIA_ON)
    assert media_embedding_profile(s) == "v2-audio"


def test_task_conditioning_changes_the_profile():
    s = _settings(GEMINI_EMBED_TASK_CONDITIONING="task_type", **_MEDIA_ON)
    assert media_embedding_profile(s) == "v2-tt"


def test_prefix_conditioning_is_distinct_from_task_type():
    """The two hypotheses produce different vectors, so different cohorts."""
    task_type = _settings(GEMINI_EMBED_TASK_CONDITIONING="task_type", **_MEDIA_ON)
    prefix = _settings(GEMINI_EMBED_TASK_CONDITIONING="prefix", **_MEDIA_ON)
    assert media_embedding_profile(task_type) != media_embedding_profile(prefix)


def test_caption_frames_change_the_profile():
    s = _settings(RAG_VIDEO_CAPTION_FRAMES=8, **_MEDIA_ON)
    assert media_embedding_profile(s) == "v2-f8"


def test_markers_join_in_a_fixed_order():
    """The string is compared for equality, never parsed — order is contract."""
    s = _settings(
        GEMINI_EMBED_TASK_CONDITIONING="task_type",
        GEMINI_AUDIO_TRACK_EXTRACTION=True,
        RAG_VIDEO_CAPTION_FRAMES=8,
        **_MEDIA_ON,
    )
    assert media_embedding_profile(s) == "v2-tt-audio-f8"


def test_auto_truncate_does_not_change_the_profile():
    """It converts silent truncation into a loud error.

    A vector that already succeeded is unaffected, so it must never force a
    whole-corpus re-embed.
    """
    plain = _settings(**_MEDIA_ON)
    loud = _settings(GEMINI_AUTO_TRUNCATE=False, **_MEDIA_ON)
    assert media_embedding_profile(plain) == media_embedding_profile(loud)


def test_profile_fits_the_column_width():
    """max_length=32 on AttachmentIngest.embedding_profile."""
    s = _settings(
        GEMINI_EMBED_TASK_CONDITIONING="task_type",
        GEMINI_AUDIO_TRACK_EXTRACTION=True,
        RAG_VIDEO_CAPTION_FRAMES=10,
        **_MEDIA_ON,
    )
    assert len(media_embedding_profile(s)) <= 32


def test_text_space_is_unchanged_by_media_knobs():
    """R5 changes nothing about how document chunks are embedded."""
    s = _settings(GEMINI_AUDIO_TRACK_EXTRACTION=True, RAG_VIDEO_CAPTION_FRAMES=8, **_MEDIA_ON)
    assert text_embedding_profile(s) == "v1"


@pytest.mark.parametrize("model", ["embed-v-4-0", "embed-v4.0"])
def test_legacy_v4_text_profiles_remain_compatible(model):
    settings = _settings(COHERE_EMBED_MODEL=model, COHERE_EMBED_DIMENSIONS=1536)
    assert text_embedding_profile(settings) == BASELINE_PROFILE


def test_v5_pro_has_a_distinct_bounded_text_profile():
    settings = _settings(COHERE_EMBED_MODEL="embed-v5.0-pro")
    profile = text_embedding_profile(settings)
    assert profile != BASELINE_PROFILE
    assert profile == text_embedding_profile(settings)
    assert len(profile) <= 32


def test_text_vector_dimensions_change_the_profile():
    narrow = _settings(COHERE_EMBED_MODEL="embed-v5.0-pro", COHERE_EMBED_DIMENSIONS=1536)
    wide = _settings(COHERE_EMBED_MODEL="embed-v5.0-pro", COHERE_EMBED_DIMENSIONS=2048)
    assert text_embedding_profile(narrow) != text_embedding_profile(wide)
    assert text_embedding_profile(_settings(COHERE_EMBED_DIMENSIONS=1024)) != BASELINE_PROFILE


@pytest.mark.parametrize("model", ["embed-v-5-0-pro", "Cohere-Embed-V5-Pro"])
def test_azure_and_cohere_v5_model_aliases_share_a_profile(model):
    azure = _settings(COHERE_EMBED_MODEL=model)
    cohere = _settings(COHERE_EMBED_MODEL="embed-v5.0-pro")
    assert text_embedding_profile(azure) == text_embedding_profile(cohere)
