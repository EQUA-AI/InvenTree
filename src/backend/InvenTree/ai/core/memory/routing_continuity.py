"""Routing continuity for follow-up fragments (M1, plan Appendix E row E35).

The intent classifier routes a bare follow-up ("Compare the first two you
listed.") on its own words; without the previous rail's context it lands on
the lookup assistant, and the history the builder replays never reaches
wf2/wf3/wf1. This module is the deterministic, content-free rule that keeps
such a turn on the rail the conversation was already on. It is gated by
``FEATURE_MEMORY_RAIL_REPLAY`` beside the replay itself — together they are
"memory on rails"; neither moves the follow-up parity gate alone.

The rule is conservative on purpose:

- only wf1/wf2/wf3 are sticky (wf8 is the default anyway; wf4 and wf6 are the
  write and upload rails and must be re-routed every turn);
- the message must be short and carry an anaphoric or continuation cue
  ("those", "them", "it", "the first one", "and for …", "summarise what you
  found");
- a bare acknowledgement ("Ok.", "thanks") never inherits a rail;
- a message that names a new intent (diagnose, find suppliers, how many …,
  send, email, create, order) always goes back through the router.

Pure Python, no Django, no agent framework (GR-35 boundary).
"""

from __future__ import annotations

import re

#: Rails that carry a conversation forward once chosen.
RAIL_WORKFLOWS = frozenset({"wf1", "wf2", "wf3"})
#: Longer messages carry their own intent; the router decides them.
MAX_FOLLOWUP_WORDS = 14

_ACKNOWLEDGEMENTS = frozenset({
    "ok",
    "okay",
    "k",
    "thanks",
    "thank you",
    "thx",
    "yes",
    "no",
    "sure",
    "great",
    "got it",
    "fine",
    "noted",
    "cheers",
    "yep",
    "nope",
    "alright",
    "right",
    "understood",
    "perfect",
})
_ANAPHORA = (
    "those",
    "them",
    "that",
    "it",
    "its",
    "this",
    "these",
    "one",
    "ones",
    "first",
    "second",
    "last",
    "either",
    "both",
    "same",
    "other",
    "above",
    "mentioned",
    "listed",
    "found",
    "next",
    "previous",
    "earlier",
)
_CONTINUATION_OPENERS = (
    "and ",
    "what about",
    "how about",
    "back to",
    "now the same",
    "same for",
    "summarise",
    "summarize",
    "compare",
    "which of",
    "what next",
    "anything else",
    "also ",
)
#: A new intent named outright: never inherit, let the router decide.
_NEW_INTENT = (
    "diagnose",
    "find supplier",
    "find alternatives",
    "research ",
    "how many",
    "send ",
    "email",
    "create ",
    "order ",
    "raise ",
    "open a ",
    "schedule",
    "generate",
    "export",
    "delete",
    "update ",
    "upload",
)
_WORD = re.compile(r"[a-z0-9']+")


def _normalise(message: str) -> str:
    return " ".join(_WORD.findall(str(message or "").lower()))


_ACK_PHRASES = tuple(sorted((a for a in _ACKNOWLEDGEMENTS if " " in a), key=len, reverse=True))


def is_acknowledgement(message: str) -> bool:
    """A bare acknowledgement must not inherit the previous turn's rail."""
    text = _normalise(message)
    if not text:
        return True
    for phrase in _ACK_PHRASES:
        text = text.replace(phrase, phrase.replace(" ", "_"))
    return all(word.replace("_", " ") in _ACKNOWLEDGEMENTS for word in text.split())


def is_followup_fragment(message: str) -> bool:
    """Short, anaphoric or continuation-shaped, and not a new intent."""
    text = _normalise(message)
    if not text or is_acknowledgement(message):
        return False
    words = text.split()
    if len(words) > MAX_FOLLOWUP_WORDS:
        return False
    padded = f" {text} "
    if any(marker in padded for marker in _NEW_INTENT):
        return False
    if any(f" {cue} " in padded for cue in _ANAPHORA):
        return True
    return any(
        text.startswith(opener.strip()) or f" {opener}" in padded
        for opener in _CONTINUATION_OPENERS
    )


def continuation_workflow(message: str, prior_rail_workflow_id: str) -> str:
    """The rail to pin for this message, or '' when the router should decide."""
    prior = str(prior_rail_workflow_id or "")
    if prior not in RAIL_WORKFLOWS:
        return ""
    return prior if is_followup_fragment(message) else ""


__all__ = [
    "MAX_FOLLOWUP_WORDS",
    "RAIL_WORKFLOWS",
    "continuation_workflow",
    "is_acknowledgement",
    "is_followup_fragment",
]
