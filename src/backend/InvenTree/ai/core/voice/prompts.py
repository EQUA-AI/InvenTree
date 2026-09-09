"""Allow-listed spoken prompts the client may REQUEST but never author (voice-UX A6).

The client asks for a prompt by kind; the server composes the exact text from
fixed, versioned templates, persists it as a ``VoiceUtterance`` and speaks it
through the exact-TTS path. The only client-supplied slot is the transcript
being reviewed, and it is sanitized and bounded here. Pure module: no Django,
no network.
"""

from __future__ import annotations

import re

PROMPT_POLICY_VERSION = "voice-prompts-v1"
TRANSCRIPT_MAX_CHARS = 240

PROMPT_KINDS: tuple[str, ...] = ("transcript_review", "status")

#: Canonical English templates/phrases (keys of the localized tables).
TRANSCRIPT_REVIEW_TEMPLATE = (
    "I heard: {transcript}. Is that right? Say confirm, discard, or say it again."
)
STATUS_LISTENING = "I'm listening."
STATUS_WAITING_FOR_DECISION = "I'm waiting for your decision. Say confirm, change that, or cancel."

STATUS_PROMPTS: dict[str, str] = {
    "listening": STATUS_LISTENING,
    "waiting_for_decision": STATUS_WAITING_FOR_DECISION,
}

LOCALIZED_TEMPLATES: dict[str, dict[str, str]] = {
    "es": {
        TRANSCRIPT_REVIEW_TEMPLATE: (
            "Escuché: {transcript}. ¿Es correcto? Di confirmar, descartar, o repítelo."
        ),
        STATUS_LISTENING: "Estoy escuchando.",
        STATUS_WAITING_FOR_DECISION: ("Espero tu decisión. Di confirmar, cambiar eso, o cancelar."),
    },
}


def _localized(text: str, locale: str | None) -> str:
    if not locale:
        return text
    table = LOCALIZED_TEMPLATES.get(str(locale).lower().split("-")[0])
    if not table:
        return text
    return table.get(text, text)


def sanitize_transcript(transcript: str | None) -> str:
    """Collapse whitespace, drop control characters, bound the length."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(transcript or ""))
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return text[:TRANSCRIPT_MAX_CHARS].rstrip()


def compose_transcript_review(transcript: str | None, locale: str | None = None) -> str:
    """The spoken review prompt for one transcript; raises ``ValueError`` when empty."""
    text = sanitize_transcript(transcript)
    if not text:
        raise ValueError("empty transcript")
    return _localized(TRANSCRIPT_REVIEW_TEMPLATE, locale).format(transcript=text)


def status_prompt(name: str | None, locale: str | None = None) -> str | None:
    """The allow-listed status prompt for ``name``, or ``None`` when unknown."""
    phrase = STATUS_PROMPTS.get(str(name or "").strip().lower())
    if phrase is None:
        return None
    return _localized(phrase, locale)


__all__ = [
    "PROMPT_KINDS",
    "PROMPT_POLICY_VERSION",
    "STATUS_PROMPTS",
    "TRANSCRIPT_MAX_CHARS",
    "TRANSCRIPT_REVIEW_TEMPLATE",
    "compose_transcript_review",
    "sanitize_transcript",
    "status_prompt",
]
