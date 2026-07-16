"""Realtime transcript normalization (WS4-T7).

Pure module. Partial transcription events are display-only by construction:
only ``conversation.item.input_audio_transcription.completed`` yields a
``FinalTranscript``, and the turn idempotency key binds the provider item id
to the session so provider repeats replay instead of duplicating turns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FINAL_EVENT_TYPE = "conversation.item.input_audio_transcription.completed"
PARTIAL_EVENT_TYPE = "conversation.item.input_audio_transcription.delta"

_MAX_TRANSCRIPT_CHARS = 8_000


class TranscriptEventError(ValueError):
    """Raised for malformed or oversized transcription events."""


@dataclass(frozen=True)
class FinalTranscript:
    """One completed user utterance ready for the normalized turn service."""

    text: str
    item_id: str
    confidence: float | None
    language: str

    def idempotency_key(self, session_id: str) -> str:
        """One turn per provider item per session, replayable on repeats."""
        return f"voice:{session_id}:{self.item_id}"

    def modality_metadata(self) -> dict[str, Any]:
        """Bounded, audio-free metadata for the turn ledger."""
        metadata: dict[str, Any] = {
            "voice_live_item_id": self.item_id,
            "language": self.language,
        }
        if self.confidence is not None:
            metadata["transcription_confidence"] = self.confidence
        return metadata


def normalize_final_transcript(event: dict[str, Any]) -> FinalTranscript:
    """Validate and normalize a completed transcription event."""
    if event.get("type") != FINAL_EVENT_TYPE:
        raise TranscriptEventError("not a completed transcription event")
    text = str(event.get("transcript", "")).strip()
    if not text:
        raise TranscriptEventError("completed transcript is empty")
    if len(text) > _MAX_TRANSCRIPT_CHARS:
        raise TranscriptEventError("completed transcript exceeds bounds")
    item_id = str(event.get("item_id", "")).strip()
    if not item_id:
        raise TranscriptEventError("completed transcript is missing item_id")
    confidence_raw = event.get("confidence")
    confidence: float | None
    if confidence_raw is None:
        confidence = None
    else:
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError) as exc:
            raise TranscriptEventError("confidence is not numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise TranscriptEventError("confidence is out of range")
    language = str(event.get("language", "") or "en-US")
    return FinalTranscript(text=text, item_id=item_id, confidence=confidence, language=language)


def is_partial_transcript(event: dict[str, Any]) -> bool:
    """Partial deltas may render live but never become turns."""
    return event.get("type") == PARTIAL_EVENT_TYPE
