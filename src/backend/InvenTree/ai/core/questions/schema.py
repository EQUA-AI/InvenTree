"""Question payload and pending-record builders (S22).

The QUESTION event payload is the client contract; the pending record is the
server-side source of truth the answer is validated against. They are built
together from the same inputs but differ deliberately: option ``ref`` values
(the server-derived accept payloads — machine ids, serials, lexicon terms)
live ONLY in the pending record, never on the wire.

SSE hygiene (invariant 5): the event payload must never carry the keys a
stale client's default parser branch renders as transcript text —
``content``, ``delta``, ``choices``, ``message``. ``AGUIEvent.to_dict``
flattens ``data`` to the top level, so this is enforced on the payload's own
keys and pinned by test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from ai.core.questions.pending import (
    PENDING_QUESTION_SCHEMA_VERSION,
    PENDING_QUESTION_TTL_SECONDS,
)

#: Claude AskUserQuestion benchmark: 2-4 options; the "Other" free-text row is
#: host-rendered — the server never emits one.
MAX_OPTIONS_TEXT = 4
#: Voice reads options aloud with ordinals inside the 700-char no-truncation
#: spoken ceiling; three short options is the practical maximum.
MAX_OPTIONS_VOICE = 3

#: Keys a stale client's default SSE branch would render as transcript text.
FORBIDDEN_EVENT_KEYS = frozenset({"content", "delta", "choices", "message"})


def _public_option(option: dict) -> dict:
    """Strip server-side refs; the wire carries display + identity only."""
    public = {
        "id": str(option["id"]),
        "label": str(option["label"]),
        "kind": str(option.get("kind", "")),
    }
    if option.get("description"):
        public["description"] = str(option["description"])
    if option.get("recommended"):
        public["recommended"] = True
    return public


def build_question_payload(
    *,
    interrupt_id: str,
    question_text: str,
    options: list[dict],
    source: str,
    expires_at: str,
) -> dict:
    """Build the QUESTION event data (also embedded in the canonical)."""
    payload = {
        "kind": "clarification_question",
        "interrupt_id": interrupt_id,
        "reason": "input_required",
        "question_text": question_text,
        "options": [_public_option(option) for option in options],
        "response_schema": {
            "type": "selection",
            "min_options": 1,
            "max_options": 1,
            "allow_free_text": True,
        },
        "source": source,
        "expires_at": expires_at,
        "policy_version": PENDING_QUESTION_SCHEMA_VERSION,
    }
    forbidden = FORBIDDEN_EVENT_KEYS & set(payload)
    if forbidden:  # pragma: no cover - structurally impossible; belt and braces
        raise ValueError(f"question payload carries forbidden keys: {forbidden}")
    return payload


def build_pending_record(
    *,
    thread_id: Any,
    turn_id: Any,
    source: str,
    question_text: str,
    options: list[dict],
    origin_content: str,
    workflow: str,
    modality: str,
    now: datetime | None = None,
) -> tuple[dict, dict]:
    """Build (pending_record, question_payload) as one consistent pair.

    The record keeps the full options WITH refs for validation and the accept
    branch; the payload is the ref-free wire shape.
    """
    moment = now or datetime.now(UTC)
    interrupt_id = str(uuid.uuid4())
    expires_at = (moment + timedelta(seconds=PENDING_QUESTION_TTL_SECONDS)).isoformat()
    record = {
        "schema_version": PENDING_QUESTION_SCHEMA_VERSION,
        "interrupt_id": interrupt_id,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "created_at": moment.isoformat(),
        "expires_at": expires_at,
        "source": source,
        "question_text": question_text,
        "modality": modality,
        "options": options,
        "origin": {"content": origin_content, "workflow": workflow},
    }
    payload = build_question_payload(
        interrupt_id=interrupt_id,
        question_text=question_text,
        options=options,
        source=source,
        expires_at=expires_at,
    )
    return record, payload


def render_question_text(question_text: str, options: list[dict], *, modality: str) -> str:
    """Render the visible (and, for voice, spoken) question text.

    Voice invariant: spoken == visible, option labels appear LITERALLY, and
    ordinals are spoken so "the second one" is answerable. Text modality gets
    a numbered markdown list. The caller owns the 700-char voice budget and
    drops options rather than ever truncating a label.
    """
    if modality == "voice":
        ordinals = ("one", "two", "three", "four")
        parts = [question_text.strip()]
        for index, option in enumerate(options):
            parts.append(f"Option {ordinals[index]}: {option['label']}.")
        return " ".join(parts)
    lines = [question_text.strip(), ""]
    for index, option in enumerate(options, start=1):
        label = option["label"]
        description = option.get("description")
        suffix = " (recommended)" if option.get("recommended") else ""
        if description:
            lines.append(f"{index}. **{label}**{suffix} — {description}")
        else:
            lines.append(f"{index}. **{label}**{suffix}")
    return "\n".join(lines)


__all__ = [
    "FORBIDDEN_EVENT_KEYS",
    "MAX_OPTIONS_TEXT",
    "MAX_OPTIONS_VOICE",
    "build_pending_record",
    "build_question_payload",
    "render_question_text",
]
