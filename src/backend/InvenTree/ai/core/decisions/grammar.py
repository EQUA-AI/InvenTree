"""Pure, versioned grammar for replies to a pending decision."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING

from ai.core.voice.confirmation import ConfirmationReply, interpret_confirmation_reply

if TYPE_CHECKING:
    from collections.abc import Collection

DECISION_GRAMMAR_POLICY_VERSION = "decision-grammar-v1"


class DecisionUtteranceKind(StrEnum):
    """A whole-utterance interpretation for the decision coordinator."""

    AFFIRM = "affirm"
    DECLINE = "decline"
    AMEND = "amend"
    DEFER = "defer"
    REVIEW = "review"
    REVIEW_SECTION = "review_section"
    HISTORY = "history"
    DISARM = "disarm"
    CANCEL_ACTION = "cancel_action"
    UNRELATED = "unrelated"

    @property
    def non_consuming(self) -> bool:
        """Whether handling this command must retain the pending decision."""

        return self in {self.DEFER, self.REVIEW, self.REVIEW_SECTION, self.HISTORY}

    @property
    def refreshes_lifetime(self) -> bool:
        """Whether OD-12 permits this reply to refresh the sliding window."""

        return self in {self.REVIEW, self.HISTORY}


_TRAILING_PUNCTUATION = re.compile(r"[\s.!?,;:]+$")
_WHITESPACE = re.compile(r"\s+")

_CANCEL_ACTION_COMMANDS = frozenset({"cancel the action", "cancel action"})
_DISARM_COMMANDS = frozenset({"cancel that", "cancel this", "set that aside", "set this aside"})
_REVIEW_COMMANDS = frozenset({
    "why",
    "repeat",
    "repeat that",
    "say that again",
    "read that again",
    "read it back",
    "review that",
    "what am i confirming",
    "what are you waiting for",
})
_SECTION_REVIEW_COMMANDS = frozenset({"read more"})
_HISTORY_COMMANDS = frozenset({
    "what changed",
    "what happened",
    "what happened to my last action",
    "what was the result",
    "read the receipt",
    "show the receipt",
})

_CONFIRMATION_MAP = {
    ConfirmationReply.AFFIRM: DecisionUtteranceKind.AFFIRM,
    ConfirmationReply.DECLINE: DecisionUtteranceKind.DECLINE,
    ConfirmationReply.AMEND: DecisionUtteranceKind.AMEND,
    ConfirmationReply.DEFER: DecisionUtteranceKind.DEFER,
    ConfirmationReply.UNRELATED: DecisionUtteranceKind.UNRELATED,
}


def _normalize_command(content: str) -> str:
    text = _WHITESPACE.sub(" ", (content or "").strip().casefold())
    text = _TRAILING_PUNCTUATION.sub("", text)
    if text.startswith("please "):
        text = text[7:]
    if text.endswith(" please"):
        text = text[:-7]
    return text.strip()


def _normalized_allowed(allowed_responses: Collection[str]) -> frozenset[str]:
    if isinstance(allowed_responses, str):
        raise TypeError("allowed_responses must be a collection of response strings")
    if not all(isinstance(response, str) for response in allowed_responses):
        raise TypeError("allowed_responses must contain only strings")
    return frozenset(_normalize_command(response) for response in allowed_responses)


def _allows_affirm(allowed: Collection[str], required_phrase: str | None) -> bool:
    return any(
        interpret_confirmation_reply(response, required_phrase=required_phrase)
        is ConfirmationReply.AFFIRM
        for response in allowed
    )


def classify_decision_utterance(
    content: str,
    *,
    allowed_responses: Collection[str] = (),
    required_phrase: str | None = None,
) -> DecisionUtteranceKind:
    """Classify one whole reply without granting authority itself.

    Coordinator commands are deliberately exact and checked before the legacy
    confirmation grammar because that grammar correctly treats every leading
    ``cancel`` as a decline.  All confirmation semantics remain delegated to
    grammar v3, including strict phrases and mixed-assent safety.
    """

    command = _normalize_command(content)
    allowed = _normalized_allowed(allowed_responses)
    if command in _CANCEL_ACTION_COMMANDS:
        if allowed & _CANCEL_ACTION_COMMANDS:
            return DecisionUtteranceKind.CANCEL_ACTION
        # A disallowed source cancellation remains a safe refusal: it disarms
        # the interaction but cannot reject the durable source.
        return DecisionUtteranceKind.DECLINE
    if command in _DISARM_COMMANDS:
        return DecisionUtteranceKind.DISARM
    if command in _SECTION_REVIEW_COMMANDS:
        return DecisionUtteranceKind.REVIEW_SECTION
    if command in _REVIEW_COMMANDS:
        return DecisionUtteranceKind.REVIEW
    if command in _HISTORY_COMMANDS:
        return DecisionUtteranceKind.HISTORY
    # A reversible action may offer a labelled assent AND ordinary yes.
    # The labelled phrase is exact; mixed assent still goes through grammar v3.
    if required_phrase is None and command in allowed and command.startswith("confirm "):
        return DecisionUtteranceKind.AFFIRM
    result = _CONFIRMATION_MAP[
        interpret_confirmation_reply(content, required_phrase=required_phrase)
    ]
    if result is DecisionUtteranceKind.AFFIRM and not _allows_affirm(
        allowed_responses, required_phrase
    ):
        return DecisionUtteranceKind.UNRELATED
    return result


__all__ = [
    "DECISION_GRAMMAR_POLICY_VERSION",
    "DecisionUtteranceKind",
    "classify_decision_utterance",
]
