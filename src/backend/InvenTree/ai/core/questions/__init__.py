"""Structured question rail (S22/S23) — turn-terminal clarification questions.

Architecture A: a question is a COMPLETED turn outcome. The asking turn
persists a QUESTION event (replay-safe) plus a single-slot pending record;
the user's answer arrives as the next turn's ordinary content and is bound
pre-routing by the turn service, validated only against the persisted
record. Nothing on this rail ever executes anything.
"""

from ai.core.questions.answers import (
    QUESTION_ANSWER_POLICY_VERSION,
    AnswerInterpretation,
    interpret_question_answer,
)
from ai.core.questions.pending import (
    PENDING_QUESTION_SCHEMA_VERSION,
    PENDING_QUESTION_TTL_SECONDS,
    CachedPendingQuestionStore,
    InMemoryPendingQuestionStore,
    PendingQuestionStore,
)
from ai.core.questions.schema import (
    MAX_OPTIONS_TEXT,
    MAX_OPTIONS_VOICE,
    build_pending_record,
    build_question_payload,
    render_question_text,
)

__all__ = [
    "MAX_OPTIONS_TEXT",
    "MAX_OPTIONS_VOICE",
    "PENDING_QUESTION_SCHEMA_VERSION",
    "PENDING_QUESTION_TTL_SECONDS",
    "QUESTION_ANSWER_POLICY_VERSION",
    "AnswerInterpretation",
    "CachedPendingQuestionStore",
    "InMemoryPendingQuestionStore",
    "PendingQuestionStore",
    "build_pending_record",
    "build_question_payload",
    "interpret_question_answer",
    "render_question_text",
]
