"""Deterministic proposal intents before the bounded text-tool planner."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkOrderIntent:
    """Transcript parameters only; the adapter resolves scope and target afresh."""

    reference: str
    reason: str
    action: str = "work_order.hold"


_REFERENCE = r"(?P<ref>[a-z0-9][a-z0-9, -]{0,120}?)"
_DIGITS = dict(
    zip(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
        range(10),
        strict=True,
    )
)
_SMALL = {
    **_DIGITS,
    **dict(
        zip(
            [
                "ten",
                "eleven",
                "twelve",
                "thirteen",
                "fourteen",
                "fifteen",
                "sixteen",
                "seventeen",
                "eighteen",
                "nineteen",
            ],
            range(10, 20),
            strict=True,
        )
    ),
}
_TENS = dict(
    zip(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"],
        range(20, 100, 10),
        strict=True,
    )
)


def _under_hundred(words: list[str]) -> int | None:
    if len(words) == 1:
        return _SMALL.get(words[0], _TENS.get(words[0]))
    if len(words) == 2 and words[0] in _TENS and words[1] in _DIGITS and _DIGITS[words[1]]:
        return _TENS[words[0]] + _DIGITS[words[1]]
    return None


def _under_thousand(words: list[str]) -> int | None:
    if len(words) >= 2 and words[0] in _DIGITS and _DIGITS[words[0]] and words[1] == "hundred":
        rest = words[2:]
        if rest[:1] == ["and"]:
            rest = rest[1:]
            if not rest:
                return None
        tail = _under_hundred(rest) if rest else 0
        return None if tail is None else _DIGITS[words[0]] * 100 + tail
    return _under_hundred(words)


def _reference(value: str) -> str | None:
    """Parse only the identifier slot, with no fuzzy or quantity normalization."""
    value = value.strip()
    if re.fullmatch(r"[a-z]*[- ]?\d+", value, re.I):
        return value
    if re.fullmatch(r"[1-9]\d{0,2}(?:,\d{3}){1,2}", value):
        return value.replace(",", "")
    words = value.lower().replace("-", " ").split()
    if not words or len(words) > 16:
        return None
    if all(word in _DIGITS for word in words):
        return "".join(str(_DIGITS[word]) for word in words)
    if words.count("thousand") == 1:
        split = words.index("thousand")
        head = _under_thousand(words[:split])
        rest = words[split + 1 :]
        if rest[:1] == ["and"]:
            rest = rest[1:]
            if not rest:
                return None
        tail = _under_thousand(rest) if rest else 0
        number = head * 1000 + tail if head and tail is not None else None
    else:
        number = _under_thousand(words)
    return str(number) if number is not None else None


_HOLD = re.compile(
    r"^(?:please\s+)?(?:put|place|set)\s+(?:work\s*order|wo)\s+"
    + _REFERENCE
    + r"\s+(?:on\s+hold|on\s+pause)"
    r"(?:\s+(?:because(?:\s+of)?\s+|for\s+|reason[: ]+)(?P<reason>.+?))?[.!?]*$",
    re.I,
)
_HOLD_SHORT = re.compile(
    r"^(?:please\s+)?hold\s+(?:work\s*order|wo)\s+"
    + _REFERENCE
    + r"(?:\s+(?:because(?:\s+of)?\s+|for\s+|reason[: ]+)(?P<reason>.+?))?[.!?]*$",
    re.I,
)


def parse_work_order_intent(content: str) -> WorkOrderIntent | None:
    """Recognize a complete hold request, never infer a reason or target."""
    match = _HOLD.fullmatch(content.strip()) or _HOLD_SHORT.fullmatch(content.strip())
    if not match:
        return None
    reference = _reference(match["ref"])
    if reference is None:
        return None
    return WorkOrderIntent(reference, (match["reason"] or "").strip().rstrip(".!"))


def apply_correction(content: str, original: WorkOrderIntent) -> WorkOrderIntent | None:
    """Only exact target/reason corrections may reuse the other reviewed field."""
    text = content.strip().rstrip(".!?")
    target = re.fullmatch(
        r"(?:no[, ]+)?(?:i meant|make that|change (?:that|it) to)\s+"
        r"(?:(?:work\s*order|wo)\s+)?" + _REFERENCE,
        text,
        re.I,
    )
    if target:
        reference = _reference(target["ref"])
        return WorkOrderIntent(reference, original.reason) if reference is not None else None
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
