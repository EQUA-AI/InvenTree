"""Deterministic proposal intents before the bounded text-tool planner."""

import re
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class WorkOrderIntent:
    """Transcript parameters only; the adapter resolves scope and target afresh."""

    reference: str
    reason: str
    action: str = "work_order.hold"
    parameters: dict = field(default_factory=dict)


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
_CANCEL = re.compile(
    r"^(?:please\s+)?cancel\s+(?:work\s*order|wo)\s+"
    + _REFERENCE
    + r"(?:\s+(?:because(?:\s+of)?\s+|for\s+|reason[: ]+)(?P<reason>.+?))?[.!?]*$",
    re.I,
)


def cancel_confirmation_reference(content: str) -> str | None:
    """Read only the optional identifier after the exact OD-3 strict phrase."""
    match = re.fullmatch(
        r"confirm cancel order\s+(?:(?:work\s*order|wo)\s+)?" + _REFERENCE,
        content.strip().rstrip(".!?"),
        re.I,
    )
    return _reference(match["ref"]) if match else None


def parse_work_order_intent(content: str) -> WorkOrderIntent | None:
    """Recognize explicit hold/cancel requests, never infer a reason or target."""
    cancel = _CANCEL.fullmatch(content.strip())
    match = cancel or _HOLD.fullmatch(content.strip()) or _HOLD_SHORT.fullmatch(content.strip())
    if not match:
        return _extended_work_order_intent(content)
    reference = _reference(match["ref"])
    if reference is None:
        return None
    return WorkOrderIntent(
        reference,
        (match["reason"] or "").strip().rstrip(".!"),
        "work_order.cancel" if cancel else "work_order.hold",
    )


def apply_correction(content: str, original: WorkOrderIntent) -> WorkOrderIntent | None:
    """Only exact target/reason corrections may reuse the other reviewed field."""
    text = content.strip().rstrip(".!?")
    if original.action.startswith("stock."):
        from ai.core.decisions.inventory import parse_stock_intent

        return parse_stock_intent(content)
    if original.action == "work_order.cancel":
        reference = cancel_confirmation_reference(text)
        if reference is not None:
            return replace(original, reference=reference)
    target = re.fullmatch(
        r"(?:no[, ]+)?(?:i meant|make that|change (?:that|it) to)\s+"
        r"(?:(?:work\s*order|wo)\s+)?" + _REFERENCE,
        text,
        re.I,
    )
    if target:
        reference = _reference(target["ref"])
        return replace(original, reference=reference) if reference is not None else None
    reason = re.fullmatch(r"(?:no[, ]+)?change (?:the )?reason to (.+)", text, re.I)
    if reason:
        return replace(original, reason=reason[1])
    return parse_work_order_intent(content)


def _extended_work_order_intent(content: str) -> WorkOrderIntent | None:
    """Small explicit syntax; unsupported or ambiguous fields are never inferred."""
    text = content.strip().rstrip(".!?")
    target = r"(?:work\s*order|wo)\s+" + _REFERENCE
    patterns = (
        (
            "procedure.complete",
            r"complete step (?P<step>[a-z0-9-]+) of application (?P<application>\d+) for "
            + target
            + r"(?: with value (?P<value>[^ ]+))?(?: passed (?P<passed>true|false))?",
        ),
        ("work_order.resume", r"resume\s+" + target + r"(?:\s+because\s+(?P<reason>.+))?"),
        ("work_order.delete", r"delete\s+" + target + r"(?:\s+because\s+(?P<reason>.+))?"),
        ("work_order.assign", r"assign\s+" + target + r"\s+to\s+(?P<assignee>.+)"),
        ("work_order.resize", r"resize\s+" + target + r"\s+to\s+(?P<minutes>\d+)\s+minutes"),
        (
            "work_order.schedule",
            r"schedule\s+" + target + r"\s+from\s+(?P<start>\S+)\s+to\s+(?P<end>\S+)",
        ),
        (
            "work_order.update",
            r"update\s+"
            + target
            + r"\s+(?P<field>title|description|priority)\s+to\s+(?P<value>.+)",
        ),
        (
            "work_order.transition",
            r"(?:transition|move)\s+"
            + target
            + r"\s+to\s+(?P<status>draft|planned|ready|in progress|on hold|verifying|completed)",
        ),
        (
            "work_order.create_child",
            r"create\s+(?:a\s+)?child\s+for\s+" + target + r"\s+titled\s+(?P<title>.+)",
        ),
        ("work_order.generate_procurement", r"generate\s+procurement\s+for\s+" + target),
        (
            "dependency.create",
            r"add\s+dependency\s+to\s+"
            + target
            + r"\s+from\s+(?:work\s*order|wo)\s+(?P<predecessor>[a-z0-9-]+)\s+type\s+(?P<type>FS|SS|FF|SF)\s+lag\s+(?P<lag>-?\d+)\s+minutes",
        ),
        ("dependency.delete", r"remove\s+dependency\s+(?P<dependency>\d+)\s+from\s+" + target),
    )
    for action, pattern in patterns:
        match = re.fullmatch(r"(?:please\s+)?" + pattern, text, re.I)
        if not match:
            continue
        reference = _reference(match["ref"])
        if reference is None:
            return None
        values = match.groupdict()
        params = {}
        if "step" in values:
            params.update(application_id=int(values["application"]), step_key=values["step"])
            if values.get("value") is not None:
                raw = values["value"]
                params["value"] = {"true": True, "false": False}.get(raw.lower(), raw)
            if values.get("passed") is not None:
                params["passed"] = values["passed"].lower() == "true"
        if "assignee" in values:
            params["assignee_name"] = values["assignee"]
        if "minutes" in values:
            params["estimated_minutes"] = int(values["minutes"])
        if "start" in values:
            params.update(scheduled_start=values["start"], scheduled_end=values["end"])
        if "field" in values:
            params["fields"] = {values["field"].lower(): values["value"]}
        if "status" in values:
            params["to_status"] = values["status"].lower().replace(" ", "_")
        if "title" in values:
            params["title"] = values["title"]
        if "predecessor" in values:
            params.update(
                predecessor_reference=values["predecessor"],
                dependency_type=values["type"].upper(),
                lag_minutes=int(values["lag"]),
            )
        if "dependency" in values:
            params["dependency_id"] = int(values["dependency"])
        return WorkOrderIntent(reference, values.get("reason") or "", action, params)
    return None


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
