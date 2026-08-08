"""Versioned, pure answer parser for pending questions (S22/S23).

Modeled on the voice confirmation grammar (``confirmation.py``): compiled
module-level patterns versioned by a policy string, a frozen result type, and
zero I/O so the pytest island covers every branch. The outcomes map to the
MCP elicitation triad: selected=accept, declined=decline; expiry/abandon
(cancel) are handled by the store and binder, not here.

Unmatched is a first-class outcome, not an error: a free-text or unrelated
reply falls through to normal routing — ignoring a question never traps the
user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

QUESTION_ANSWER_POLICY_VERSION = "question-answer-v2"

_ORDINAL_WORDS = {
    "1": 0,
    "one": 0,
    "first": 0,
    "2": 1,
    "two": 1,
    "second": 1,
    "3": 2,
    "three": 2,
    "third": 2,
    "4": 3,
    "four": 3,
    "fourth": 3,
}

#: "2", "two", "option 3", "number two" — anchored at the start so a number
#: buried in a sentence does not select.
_LEADING_ORDINAL_RE = re.compile(
    r"^\s*(?:option\s+|number\s+)?(1|2|3|4|one|two|three|four)\b[\s.!,]*$",
    re.IGNORECASE,
)
#: "the second one", "second option", "the first"
_POSITIONAL_RE = re.compile(
    r"\b(first|second|third|fourth)\s*(?:one|option|choice)?\b",
    re.IGNORECASE,
)
_LAST_ONE_RE = re.compile(r"\bthe\s+last\s+(?:one|option|choice)\b", re.IGNORECASE)

_DECLINE_RE = re.compile(
    r"^\s*(?:no\s+thanks?|none\s+of\s+(?:those|these|them)|neither(?:\s+of\s+(?:those|these|them))?"
    r"|skip(?:\s+it)?|cancel|never\s?mind|forget\s+it)[\s.!,]*$",
    re.IGNORECASE,
)
#: Decline words inside a longer request ("cancel the order for pump seals")
#: must NOT decline; the anchor above plus this token bound enforces it.
_DECLINE_MAX_TOKENS = 6

#: v2 normalized label matching. Live battery 2026-08-08: technicians answer
#: "Influent Pump Station 1" / "influent pump station one" against the label
#: "Influent Pump Station No. 1" — exact/containment can never match those,
#: and every near-miss re-asked the identical card. Normalization folds case,
#: strips punctuation, maps small number words to digits and drops filler
#: tokens, then a UNIQUE token-subset match either way selects. A long reply
#: is a new question, not an answer — the token bound keeps a fresh sentence
#: that merely mentions an option ("The influent pump station is tripping
#: again...") from being hijacked as a selection.
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4"}
_LABEL_FILLER_TOKENS = frozenset({"the", "a", "an", "no", "number", "num", "option", "please"})
_LABEL_MATCH_MAX_TOKENS = 8


def _normalized_tokens(text: str) -> tuple[str, ...]:
    tokens = re.findall(r"[a-z0-9]+", text.casefold())
    return tuple(
        _NUMBER_WORDS.get(token, token) for token in tokens if token not in _LABEL_FILLER_TOKENS
    )


@dataclass(frozen=True)
class AnswerInterpretation:
    """The parser's verdict on one reply against one persisted option list."""

    outcome: Literal["selected", "declined", "unmatched"]
    option_index: int | None
    option_id: str | None
    matched_by: Literal["ordinal", "label", "free"] | None
    policy_version: str


def _unmatched() -> AnswerInterpretation:
    return AnswerInterpretation(
        outcome="unmatched",
        option_index=None,
        option_id=None,
        matched_by=None,
        policy_version=QUESTION_ANSWER_POLICY_VERSION,
    )


def _selected(index: int, options: Sequence[dict], matched_by: str) -> AnswerInterpretation:
    return AnswerInterpretation(
        outcome="selected",
        option_index=index,
        option_id=str(options[index]["id"]),
        matched_by=matched_by,  # type: ignore[arg-type]
        policy_version=QUESTION_ANSWER_POLICY_VERSION,
    )


def _ordinal_index(reply: str, option_count: int) -> int | None:
    match = _LEADING_ORDINAL_RE.match(reply)
    if match:
        index = _ORDINAL_WORDS[match.group(1).lower()]
        return index if index < option_count else None
    match = _POSITIONAL_RE.search(reply)
    if match:
        index = _ORDINAL_WORDS[match.group(1).lower()]
        return index if index < option_count else None
    if _LAST_ONE_RE.search(reply):
        return option_count - 1
    return None


def interpret_question_answer(
    reply: str, options: Sequence[dict], *, modality: str = "text"
) -> AnswerInterpretation:
    """Interpret one reply against the PERSISTED options, exactly once.

    Precedence: ordinal → decline → unique label match → unmatched. An
    out-of-range ordinal is unmatched (never an error); an ambiguous label
    containment (two options hit) is unmatched — guessing selects nothing.
    ``modality`` is reserved for future ASR-artifact tuning; the grammar is
    identical on both rails in v1.
    """
    del modality  # reserved; grammar identical on both rails in v1
    text = (reply or "").strip()
    if not text or not options:
        return _unmatched()

    index = _ordinal_index(text, len(options))
    if index is not None:
        return _selected(index, options, "ordinal")

    if len(text.split()) <= _DECLINE_MAX_TOKENS and _DECLINE_RE.match(text):
        return AnswerInterpretation(
            outcome="declined",
            option_index=None,
            option_id=None,
            matched_by=None,
            policy_version=QUESTION_ANSWER_POLICY_VERSION,
        )

    folded = text.casefold()
    exact = [i for i, option in enumerate(options) if str(option["label"]).casefold() == folded]
    if len(exact) == 1:
        return _selected(exact[0], options, "label")

    contains = [
        i
        for i, option in enumerate(options)
        if str(option["label"]).casefold() in folded or folded in str(option["label"]).casefold()
    ]
    if len(contains) == 1:
        return _selected(contains[0], options, "label")

    # v2: normalized token matching, short replies only. A unique option whose
    # normalized tokens contain (or are contained by) the reply's selects;
    # any ambiguity — "pump" hitting two pumps — stays unmatched: guessing
    # selects nothing.
    if len(text.split()) <= _LABEL_MATCH_MAX_TOKENS:
        answer_tokens = set(_normalized_tokens(text))
        if answer_tokens:
            normalized = [
                i
                for i, option in enumerate(options)
                if (label_tokens := set(_normalized_tokens(str(option["label"]))))
                and (answer_tokens <= label_tokens or label_tokens <= answer_tokens)
            ]
            if len(normalized) == 1:
                return _selected(normalized[0], options, "label")

    return _unmatched()


__all__ = [
    "QUESTION_ANSWER_POLICY_VERSION",
    "AnswerInterpretation",
    "interpret_question_answer",
]
