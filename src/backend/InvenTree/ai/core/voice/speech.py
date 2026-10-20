"""Exact TTS payload construction (WS4-T8).

Pure module. The persist-before-speak rule is enforced structurally: the
payload builder refuses any text whose SHA-256 does not match the hash the
caller persisted first, so a paraphrase can never reach the provider.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any


class ExactSpeechViolation(RuntimeError):
    """Raised when requested speech does not match persisted text."""


def spoken_summary_hash(text: str) -> str:
    """Return the canonical SHA-256 hex digest for a spoken summary."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: Only these presentation rates ever reach the provider (the finite
#: allow-list behind the existing "presentation slower" command).
ALLOWED_SPEECH_RATES = (0.8, 1.0)


def build_exact_tts_payload(
    *,
    persisted_text: str,
    persisted_hash: str,
    utterance_id: str | None = None,
    speech_kind: str | None = None,
    onset_kind: str | None = None,
    dispatch_generation: str | None = None,
    rate: float | None = None,
) -> dict[str, Any]:
    """Return the exact ``response.create`` body for persisted text.

    Raises ``ExactSpeechViolation`` when the text/hash pair is inconsistent,
    which means the caller failed the persist-before-speak contract.

    The optional metadata hints are CONTENT-FREE and never authority: the
    persisted utterance id/hash (the gate) stay as-is, consumers must tolerate
    providers not echoing any of them (missing echo = unknown coverage), and
    ``aimms_onset_kind`` is present ONLY for actual optional waiting
    (``"filler"``) or a completed-answer dispatch (``"answer"``) -- never for
    required status, help, clarification or readback speech, so no client can
    mistake a prompt for a useful answer or an optional cue.
    """
    if not persisted_text.strip():
        raise ExactSpeechViolation("refusing to speak empty text")
    if spoken_summary_hash(persisted_text) != persisted_hash:
        raise ExactSpeechViolation("spoken text does not match its persisted hash")
    payload = {
        "type": "response.create",
        "response": {
            "pre_generated_assistant_message": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": persisted_text}],
            }
        },
    }
    metadata: dict[str, Any] = {}
    if utterance_id is not None:
        try:
            identifier = str(uuid.UUID(utterance_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ExactSpeechViolation("invalid opaque utterance binding") from exc
        # Telemetry consistency only. This metadata never establishes delivery
        # authority, and consumers must tolerate providers not echoing it.
        metadata["aimms_utterance_id"] = identifier
        metadata["aimms_spoken_hash"] = persisted_hash
    if speech_kind is not None:
        if speech_kind not in ("answer", "status", "prompt"):
            raise ValueError("unknown speech kind")
        metadata["aimms_speech_kind"] = speech_kind
    if onset_kind is not None:
        if onset_kind not in ("filler", "answer"):
            raise ValueError("unknown onset kind")
        metadata["aimms_onset_kind"] = onset_kind
    if dispatch_generation is not None:
        if not (
            isinstance(dispatch_generation, str)
            and dispatch_generation.isdigit()
            and len(dispatch_generation) <= 16
        ):
            raise ValueError("invalid dispatch generation")
        metadata["aimms_dispatch_generation"] = dispatch_generation
    if metadata:
        payload["response"]["metadata"] = metadata
    if rate is not None:
        if rate not in ALLOWED_SPEECH_RATES:
            raise ValueError("unsupported presentation rate")
        payload["response"]["_aimms_rate"] = rate
    return payload


def speakable_response_state(response_state: str) -> bool:
    """Only a complete canonical response may produce answer speech."""
    return response_state == "complete"


def utterance_binding(payload: dict[str, Any]) -> str | None:
    """Return the opaque utterance id bound in an exact-TTS payload, if any.

    Telemetry correlation only: providers may not echo this metadata back,
    so it never establishes delivery authority.
    """
    try:
        value = payload.get("response", {}).get("metadata", {}).get("aimms_utterance_id")
    except AttributeError:
        return None
    if type(value) is str:
        try:
            return str(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            return None
    return None
