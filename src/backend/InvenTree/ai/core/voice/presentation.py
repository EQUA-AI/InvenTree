"""Deterministic listening chunks of the validated spoken form, never raw tokens."""

from __future__ import annotations

import re

POLICY_VERSION = "voice-presentation-v1"
_LIST_ITEM = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")
# Safety/qualifier-bearing sentences are never removed by short/pagination.
_CRITICAL = re.compile(
    r"\b(?:warn\w*|danger\w*|caution|risk\w*|safe\w*|must|never|not|do not|"
    r"lock\w*|loto|isolat\w*|energ\w*|pressure|voltage|uncertain\w*|unconfirm\w*|"
    r"approx\w*|unknown|incomplete|failed|verify|check|before|unless)\b|\d",
    re.I,
)
_CAUTION = re.compile(_CRITICAL.pattern.removesuffix("|\\d"), re.I)
_MISSING_FIELD = re.compile(r"\b(?:serial|location|IPN)\s*:?\s+not set\b", re.I)


def chunks(text: str, *, layout: str = "", safety_boundary: str = "") -> list[str]:
    """List total + first three; otherwise groups of three intact sentences.

    Only schema-validated spoken text enters here. Never cut a sentence, quantity
    or warning at a character boundary, and never use the full unvalidated chat
    markdown as a fallback. Safety/qualification sentences repeat on every page.
    """
    text = text.strip()
    items = _LIST_ITEM.split(text)
    if layout:
        from ai.core.turn.responses import _plain_spoken_text

        # List boundaries come only from a visible source whose ENTIRE
        # normalized text equals the schema-validated spoken form.
        if _plain_spoken_text(layout) == text:
            candidates = [_plain_spoken_text(item) for item in _LIST_ITEM.split(layout)]
            if len(candidates) > 2 and " ".join(item for item in candidates if item) == text:
                items = candidates
    is_list = len(items) > 2
    if is_list:
        intro, records = items[0].strip(), [item.strip() for item in items[1:]]
    else:
        intro, records = "", re.split(r"(?<=[.!?])\s+", text)
    critical = (
        [
            intro,
            *(record for record in records if _CAUTION.search(_MISSING_FIELD.sub("", record))),
        ]
        if is_list
        else [part for part in re.split(r"(?<=[.!?])\s+|\n", text) if _CRITICAL.search(part)]
    )
    pages = []
    for index in range(0, len(records), 3):
        page = " ".join(records[index : index + 3])
        # List quantities identify individual records: retain original record
        # text on its own page; repeat actual warning lines, not every identifier.
        warnings = [part for part in critical if part]
        if isinstance(safety_boundary, str) and safety_boundary and safety_boundary in text:
            warnings.append(safety_boundary)
        if index == 0 and is_list:
            page = f"{intro} {len(records)} items. {page}".strip()
        prefix = " ".join(dict.fromkeys(part for part in warnings if part not in page))
        page = " ".join(part for part in (prefix, page) if part)
        if index + 3 < len(records):
            page += " Say next three for more, or repeat."
        pages.append(page)
    return pages or [text]


def short_text(text: str) -> str:
    """Remove only exact sentence repetitions, never unique content.

    Arbitrary warnings cannot be recognized reliably with keywords. Until
    canonical output identifies deletable detail spans, brevity must not remove
    unique facts, warnings or qualifiers.
    """
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(dict.fromkeys(parts))
