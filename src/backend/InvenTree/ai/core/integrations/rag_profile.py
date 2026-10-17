"""Corpus profile strings: which settings produced a row's vectors (R5).

``run_ingest`` short-circuits on an INDEXED row carrying the same content sha,
so changing *how* content is embedded or captioned does not, on its own, cause
a re-ingest. Without a marker the corpus would silently split into old-profile
and new-profile halves, and nothing would be able to tell them apart or drive
a convergent repair.

A profile is that marker: a short deterministic string derived only from the
settings that change what lands in the index. It is stamped on the registry
row and on every projected document, which buys three things:

* a one-query homogeneity proof (``GROUP BY embedding_profile`` returns one
  row; the Search-side equivalent is ``$count`` where the profile differs);
* an idempotent, convergent re-ingest selector for the backfill's
  ``--force-stale-profile``, which needs no file read and is safe to re-run;
* per-flag A/B cohorts sitting in the index during a rollout.

The contract that matters: at R4 defaults both functions return ``"v1"``, so a
deployment that changes nothing re-projects to byte-identical documents.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ai.core.config import Settings

#: The profile every pre-R5 row carries (matches the column's ``db_default``).
BASELINE_PROFILE = "v1"

#: Marker fragments, in the fixed order they are joined. Order is part of the
#: contract: the string is compared for equality, never parsed.
_TASK_CONDITIONING_MARKERS = {"task_type": "tt", "prefix": "px"}


def media_embedding_profile(settings: Settings) -> str:
    """Profile for the Gemini media space (images and video segments).

    Encodes only the knobs that change the stored vector or the searchable
    caption/OCR text. ``auto_truncate`` is deliberately excluded: it turns a
    silent truncation into a loud error but does not alter a vector that
    succeeded, so it must not force a re-embed.
    """
    parts: list[str] = []

    conditioning = getattr(settings, "gemini_embed_task_conditioning", "off")
    marker = _TASK_CONDITIONING_MARKERS.get(conditioning)
    if marker:
        parts.append(marker)

    if getattr(settings, "gemini_audio_track_extraction", False):
        parts.append("audio")

    frames = int(getattr(settings, "rag_video_caption_frames", 1) or 1)
    if frames > 1:
        parts.append(f"f{frames}")

    if not parts:
        return BASELINE_PROFILE
    return "-".join(["v2", *parts])


def text_embedding_profile(settings: Settings) -> str:
    """Identify the model and vector width without invalidating legacy v4 rows."""
    aliases = {
        "embed-v-4-0": "embed-v4.0",
        "embed-v-5-0-pro": "embed-v5.0-pro",
        "cohere-embed-v5-pro": "embed-v5.0-pro",
    }
    model = aliases.get(settings.cohere_embed_model.casefold(), settings.cohere_embed_model)
    dimensions = settings.cohere_embed_dimensions
    if model == "embed-v4.0" and dimensions == 1536:
        return BASELINE_PROFILE
    payload = json.dumps(
        {"model": model, "dimensions": dimensions},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    return f"v2-text-{digest}"
