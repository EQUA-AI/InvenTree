"""Read-only visualization wording shared by routing and tool selection.

This module classifies presentation, never permissions or business effects.
Only the narrowly named display-label instruction is masked; a separate
instruction to mark a work order, create a part or send a report survives.
"""

from __future__ import annotations

import re


def chart_requested(text: str) -> bool:
    """Whether a graphical presentation was explicitly requested."""
    return bool(
        re.search(
            r"\b(?:charts?|graphs?|plots?|trends?|visuali[sz]ations?|visual comparison)\b",
            text,
            re.I,
        )
    )


def effect_text(text: str) -> str:
    """Keep business actions, without treating chart labels as writes."""
    if chart_requested(text):
        text = re.sub(
            r"\b(?:create|make|build|generate|draw)\s+(?:me\s+)?(?:an?\s+)?(?:synthetic\s+)?(?:(?:line|bar|pie)\s+)?(?:chart|graph|plot)\b",
            "display chart",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"\bwork[ \u2010\u2011-]+orders?\b(?!\s+\d+\s+(?:more|additional)\b)",
            "maintenance jobs",
            text,
            flags=re.I,
        )
        text = re.sub(r"\btest\s+count\b", "test value", text, flags=re.I)
        text = re.sub(
            r"\bmark\s+(?:it|the\s+(?:chart|graph|plot))\s+(?:as\s+)?synthetic(?:\s+test)?\s+data\b",
            "label the chart synthetic data",
            text,
            flags=re.I,
        )
    return text


def status_comparison_requested(text: str) -> bool:
    """A bounded recorded work-order status graphic, not fleet analytics."""
    return bool(
        chart_requested(text)
        and re.search(r"\bwork[ -]?orders?\b", text, re.I)
        and re.search(r"\b(?:open|completed|status|lifecycle)\b", text, re.I)
        and not re.search(r"\b(?:monthly|weekly|trend|over time)\b", text, re.I)
    )
