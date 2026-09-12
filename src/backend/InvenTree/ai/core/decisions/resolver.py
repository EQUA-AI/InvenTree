"""Deterministic proposal intents before the bounded text-tool planner."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkOrderIntent:
    """Transcript parameters only; the adapter resolves scope and target afresh."""

    reference: str
    reason: str
    action: str = "work_order.hold"


_HOLD = re.compile(
    r"^(?:please\s+)?(?:put|place|set)\s+(?:work\s*order|wo)\s+"
    r"(?P<ref>[a-z]*[- ]?\d+)\s+(?:on\s+hold|on\s+pause)"
    r"(?:\s+(?:because(?:\s+of)?|for|reason[: ]*)\s*(?P<reason>.+?))?[.!?]*$",
    re.I,
)
_HOLD_SHORT = re.compile(
    r"^(?:please\s+)?hold\s+(?:work\s*order|wo)\s+(?P<ref>[a-z]*[- ]?\d+)"
    r"(?:\s+(?:because(?:\s+of)?|for|reason[: ]*)\s*(?P<reason>.+?))?[.!?]*$",
    re.I,
)


def parse_work_order_intent(content: str) -> WorkOrderIntent | None:
    """Recognize a complete hold request, never infer a reason or target."""
    match = _HOLD.fullmatch(content.strip()) or _HOLD_SHORT.fullmatch(content.strip())
    if not match:
        return None
    return WorkOrderIntent(match["ref"].strip(), (match["reason"] or "").strip().rstrip(".!"))


def apply_correction(content: str, original: WorkOrderIntent) -> WorkOrderIntent | None:
    """Only exact target/reason corrections may reuse the other reviewed field."""
    text = content.strip().rstrip(".!?")
    target = re.fullmatch(
        r"(?:no[, ]+)?(?:i meant|make that|change (?:that|it) to)\s+"
        r"(?:(?:work\s*order|wo)\s+)?([a-z]*[- ]?\d+)",
        text,
        re.I,
    )
    if target:
        return WorkOrderIntent(target[1], original.reason)
    reason = re.fullmatch(r"(?:no[, ]+)?change (?:the )?reason to (.+)", text, re.I)
    if reason:
        return WorkOrderIntent(original.reference, reason[1])
    return parse_work_order_intent(content)


class CompositeVoiceActionResolver:
    """Try the deterministic proposal parser before the existing tool resolver."""

    async def resolve(self, content, *, actor, trusted_context):
        intent = parse_work_order_intent(content)
        if intent is not None:
            return intent
        from ai.core.voice.tool_actions import VoiceToolActionResolver

        return await VoiceToolActionResolver().resolve(
            content, actor=actor, trusted_context=trusted_context
        )
