"""Post-LLM directive scrub for the compaction summary body (M2 PR 4; §8.5.2).

Plan of record §5.9 / §8.5.2 (GR-20): the strict response schema bounds the
SHAPE of summarizer output, not its strings. Two marker families bound the
strings:

* **syntax** markers (``tool_call``, ``function_call``, ``<tool``) can only
  be an attempt to forge a tool or function envelope, so a hit drops the
  item (or blanks the scalar) and counts ``dropped``;
* **natural-language** markers (a ``system:`` line, "invoke the tool",
  "ignore all previous instructions", "as the system", "you must now") are
  keep-and-flag: the item survives with ``directive_flags`` set and counts
  ``flagged``. It reaches a prompt only inside the context assembler's
  ``fence_untrusted_content`` markers (M1 PR D), so a benign "the SCADA
  system: alarm 42 latched" is never lost for looking like an instruction.

Both counts are per-pass deltas. The caller scrubs the MERGED body, which
carries the prior items (flags included) beside the fresh batch, so
``flagged`` counts only an active item whose ``nl_directive`` flag this
pass sets: a flagged item that survives into the next compaction is not
counted again, and neither is a superseded or forgotten item that never
renders. ``dropped`` is a delta by construction (a dropped item is gone).

Stdlib only (``re``); no Django, no ``agent_framework`` (GR-35); imports
only :mod:`ai.core.memory.summary_body` for the list vocabulary. Nothing
here logs — the caller records the counts, never the text.
"""

from __future__ import annotations

import re
from typing import Any

from ai.core.memory.summary_body import CITATION_LIST, ITEM_LISTS, is_active, item_text

SYNTAX_DIRECTIVE = "syntax_directive"
NL_DIRECTIVE = "nl_directive"

#: Lower-cased substrings that only ever spell a forged tool/function envelope.
SYNTAX_MARKERS: tuple[str, ...] = ("tool_call", "function_call", "<tool")

#: Natural-language directive shapes (§5.9 interim tightening, carried into
#: the shared module): a ``system:`` line start rather than the bare word,
#: ``invoke`` only when it names a tool/function/call, and the three classic
#: instruction-override phrasings.
NL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?im)^\s*system:"),
    re.compile(r"(?i)\binvoke\s+(the\s+)?(tool|function|[a-z_]+\()"),
    re.compile(r"(?i)\bignore\s+(all\s+|your\s+)?(previous|prior)\s+instructions"),
    re.compile(r"(?i)\bas the system\b"),
    re.compile(r"(?i)\byou must now\b"),
)

#: Scalar body strings the scrub inspects (``exclusions`` etc. are content-free).
SCALAR_FIELDS: tuple[str, ...] = ("label", "narrative")


def classify_directive(text: Any) -> frozenset[str]:
    """The directive codes ``text`` carries: any subset of the two codes."""
    value = str(text or "")
    codes: set[str] = set()
    lowered = value.lower()
    if any(marker in lowered for marker in SYNTAX_MARKERS):
        codes.add(SYNTAX_DIRECTIVE)
    if any(pattern.search(value) for pattern in NL_PATTERNS):
        codes.add(NL_DIRECTIVE)
    return frozenset(codes)


def _flag_item(item: Any, counts: dict[str, int]) -> Any | None:
    """One list item: ``None`` when dropped, else the (possibly flagged) item."""
    if not isinstance(item, (str, dict)):
        return item
    codes = classify_directive(item_text(item))
    if SYNTAX_DIRECTIVE in codes:
        counts["dropped"] += 1
        return None
    if NL_DIRECTIVE in codes:
        if isinstance(item, dict):
            existing = item.get("directive_flags")
            flags = {str(flag) for flag in existing} if isinstance(existing, list) else set()
            # A new hit only: an item already flagged by an earlier pass, or
            # one whose lifecycle keeps it out of every render, is not
            # counted again (the flag itself is still recorded).
            if NL_DIRECTIVE not in flags and is_active(item):
                counts["flagged"] += 1
            flagged = dict(item)
            flagged["directive_flags"] = sorted(flags | codes)
            return flagged
        # A legacy string item has nowhere to carry the flag; it is counted
        # and kept as-is (it upgrades to an object at the next merge, and
        # the live path upgrades every item before it reaches the scrub).
        counts["flagged"] += 1
    return item


def flag_items(body: dict) -> tuple[dict, dict[str, int]]:
    """Apply the scrub to a summary body; returns ``(body, counts)``.

    Pure: the input is never mutated. ``counts`` is
    ``{"dropped": n, "flagged": n}``. Per-fact items of every list in
    ``ITEM_LISTS`` are judged on their text; ``label`` and ``narrative`` are
    blanked on a syntax hit and kept (counted) on a natural-language hit;
    a citation key with a syntax hit is removed. Every other key passes
    through untouched. Idempotent on object items: a second pass over its
    own output counts ``flagged == 0`` (see the module docstring).
    """
    counts = {"dropped": 0, "flagged": 0}
    source = body if isinstance(body, dict) else {}
    cleaned: dict = {}
    for key, value in source.items():
        if key in ITEM_LISTS and isinstance(value, list):
            kept = []
            for item in value:
                result = _flag_item(item, counts)
                if result is not None:
                    kept.append(result)
            cleaned[key] = kept
        elif key == CITATION_LIST and isinstance(value, list):
            kept = []
            for entry in value:
                if isinstance(entry, str) and SYNTAX_DIRECTIVE in classify_directive(entry):
                    counts["dropped"] += 1
                    continue
                kept.append(entry)
            cleaned[key] = kept
        elif key in SCALAR_FIELDS and isinstance(value, str):
            codes = classify_directive(value)
            if SYNTAX_DIRECTIVE in codes:
                counts["dropped"] += 1
                cleaned[key] = ""
            else:
                if NL_DIRECTIVE in codes:
                    counts["flagged"] += 1
                cleaned[key] = value
        else:
            cleaned[key] = value
    return cleaned, counts


__all__ = [
    "NL_DIRECTIVE",
    "NL_PATTERNS",
    "SCALAR_FIELDS",
    "SYNTAX_DIRECTIVE",
    "SYNTAX_MARKERS",
    "classify_directive",
    "flag_items",
]
