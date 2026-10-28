"""Deterministic listening chunks of the validated spoken form, never raw tokens."""

from __future__ import annotations

import re

POLICY_VERSION = "voice-presentation-v4"
_BRIEF_PROSE_MAX_WORDS = 300
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
    """Page flat lists, intact sections or sentences in original source order.

    Only schema-validated spoken text enters here. Never cut a sentence, quantity
    or warning at a character boundary, and never use the full unvalidated chat
    markdown as a fallback. Safety/qualification sentences repeat on every page.
    A later unindented heading/prose block ends a flat list: splitting every
    bullet across sections attaches following headings to preceding items and
    invents an aggregate item count. Repeat complete safety/qualification records
    in their original order, without hoisting later tools/warnings before the
    job/cause introduction.
    """
    text = text.strip()
    items = _LIST_ITEM.split(text)
    sections = None
    layout_has_list = False
    if layout:
        from ai.core.turn.responses import _plain_spoken_text

        # List boundaries come only from a visible source whose ENTIRE
        # normalized text equals the schema-validated spoken form.
        if _plain_spoken_text(layout) == text:
            layout_has_list = bool(_LIST_ITEM.search(layout))
            seen_item = False
            structured = False
            for line in layout.splitlines():
                if _LIST_ITEM.match(line):
                    seen_item = True
                elif seen_item and line.strip() and not line[0].isspace():
                    structured = True
            if structured:
                candidates = [_plain_spoken_text(part) for part in re.split(r"\n\s*\n", layout)]
                if len(candidates) > 1 and " ".join(part for part in candidates if part) == text:
                    sections = [part for part in candidates if part]
            if sections is None:
                # A flat record list can end with a continuation instruction.
                # One paragraph is not a multi-section layout: retain the
                # numbered record boundaries and its trailing source text.
                candidates = [_plain_spoken_text(item) for item in _LIST_ITEM.split(layout)]
                if len(candidates) > 2 and " ".join(item for item in candidates if item) == text:
                    items = candidates
    is_list = sections is None and len(items) > 2
    # A complete bounded prose brief is one listening unit. Keep all words and
    # its safety boundary together; lists and intact sections keep their pages.
    if (
        not is_list
        and sections is None
        and not layout_has_list
        and len(items) == 1
        and len(text.split()) <= _BRIEF_PROSE_MAX_WORDS
    ):
        return [text]
    if is_list:
        intro, records = items[0].strip(), [item.strip() for item in items[1:]]
    else:
        intro, records = "", sections or re.split(r"(?<=[.!?])\s+", text)
    critical_indices = (
        {
            index
            for index, record in enumerate(records)
            if _CAUTION.search(_MISSING_FIELD.sub("", record))
        }
        if is_list
        else {index for index, record in enumerate(records) if _CRITICAL.search(record)}
    )
    pages = []
    for index in range(0, len(records), 3):
        selected = sorted(critical_indices | set(range(index, min(index + 3, len(records)))))
        page = " ".join(records[position] for position in selected)
        # List quantities identify individual records: retain original record
        # text on its own page; repeat actual warning lines, not every identifier.
        if index == 0 and is_list:
            page = f"{intro} {len(records)} items. {page}".strip()
        elif intro:
            page = f"{intro} {page}".strip()
        if (
            isinstance(safety_boundary, str)
            and safety_boundary
            and safety_boundary in text
            and safety_boundary not in page
        ):
            page = f"{page} {safety_boundary}"
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
