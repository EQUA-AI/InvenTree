"""Exact TTS payload construction (WS4-T8).

Pure module. The persist-before-speak rule is enforced structurally: the
payload builder refuses any text whose SHA-256 does not match the hash the
caller persisted first, so a paraphrase can never reach the provider.
"""

from __future__ import annotations

import hashlib
from typing import Any


class ExactSpeechViolation(RuntimeError):
    """Raised when requested speech does not match persisted text."""


def spoken_summary_hash(text: str) -> str:
    """Return the canonical SHA-256 hex digest for a spoken summary."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_exact_tts_payload(
    *,
    persisted_text: str,
    persisted_hash: str,
) -> dict[str, Any]:
    """Return the exact ``response.create`` body for persisted text.

    Raises ``ExactSpeechViolation`` when the text/hash pair is inconsistent,
    which means the caller failed the persist-before-speak contract.
    """
    if not persisted_text.strip():
        raise ExactSpeechViolation("refusing to speak empty text")
    if spoken_summary_hash(persisted_text) != persisted_hash:
        raise ExactSpeechViolation("spoken text does not match its persisted hash")
    return {
        "type": "response.create",
        "response": {
            "pre_generated_assistant_message": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": persisted_text}],
            }
        },
    }


def speakable_response_state(response_state: str) -> bool:
    """Only a complete canonical response may produce answer speech."""
    return response_state == "complete"
