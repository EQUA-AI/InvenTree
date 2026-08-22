"""Server-authored media-evidence manifest for chat answers (R4).

Evidence chips under an answer open the photo or video segment the turn's
retrieval tools actually returned — and "actually returned" is defined by
the SERVER: the manifest is built exclusively from the bounded, tool-only-
writable capture ledger. A model cannot place a chip by mentioning an id,
and model-authored in-app links deliberately render as dead text — this
channel is the only clickable path to evidence.

Labels are built from server-stamped ids and timecodes ONLY. The captured
``source_file_name``/``document`` fields are fenced attacker-authored text
and never reach a chip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Chips are heavier than entity chips (each opens a media viewer).
MAX_MEDIA_EVIDENCE = 6

_EVIDENCE_ACCESS_CLASS = "evidence_recording"


def _to_int(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed


def _to_float(value: Any) -> float | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _format_timecode(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _format_label(
    *,
    media_type: str,
    work_order_id: int | None,
    model_type: str,
    model_id: int | None,
    attachment_id: int,
    timecode_start_s: float | None,
) -> str:
    """Neutral server-built label — ids and timecodes only, never filenames."""
    if work_order_id:
        anchor = f"WO #{work_order_id}"
    elif model_type == "assetmachine" and model_id:
        anchor = f"Machine #{model_id}"
    else:
        anchor = f"Evidence #{attachment_id}"
    if media_type == "video_segment" and timecode_start_s is not None:
        return f"{anchor} · {_format_timecode(timecode_start_s)}"
    return f"{anchor} · photo"


def build_media_evidence(citations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project ledger citations into the bounded evidence manifest.

    Only ``evidence_recording`` rows qualify (governed/doc citations share
    the same ledger pool and are dropped). Deduplicated on
    (attachment, segment) in ledger call order, capped at
    ``MAX_MEDIA_EVIDENCE``. Deliberately DB-free, like the entity manifest:
    a deleted attachment degrades in the viewer's 404 path, keeping this
    builder pure and deterministic.
    """
    entries: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for citation in citations or ():
        if not isinstance(citation, dict):
            continue
        if str(citation.get("access_class") or "") != _EVIDENCE_ACCESS_CLASS:
            continue
        attachment_id = _to_int(citation.get("attachment_id"))
        if attachment_id is None or attachment_id <= 0:
            continue
        segment_index = _to_int(citation.get("segment_index")) or 0
        if (attachment_id, segment_index) in seen:
            continue
        if len(entries) >= MAX_MEDIA_EVIDENCE:
            break
        seen.add((attachment_id, segment_index))
        media_type = str(citation.get("media_type") or "image")
        model_type = str(citation.get("model_type") or "")
        model_id = _to_int(citation.get("model_id"))
        work_order_id = _to_int(citation.get("work_order_id"))
        timecode_start_s = _to_float(citation.get("timecode_start_s"))
        timecode_end_s = _to_float(citation.get("timecode_end_s"))
        entries.append({
            "attachment_id": attachment_id,
            "model_type": model_type,
            "model_id": model_id,
            "work_order_id": work_order_id,
            "media_type": media_type,
            "timecode_start_s": timecode_start_s,
            "timecode_end_s": timecode_end_s,
            "segment_index": segment_index,
            "label": _format_label(
                media_type=media_type,
                work_order_id=work_order_id,
                model_type=model_type,
                model_id=model_id,
                attachment_id=attachment_id,
                timecode_start_s=timecode_start_s,
            ),
        })
    return entries


__all__ = ["MAX_MEDIA_EVIDENCE", "build_media_evidence"]
