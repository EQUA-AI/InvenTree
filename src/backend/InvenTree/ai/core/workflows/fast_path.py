"""Deterministic fast-path answers for Tier-1 voice lookups.

The FastPathRouter already executes InvenTree reads for pattern-matched
stock/part/BOM/location queries. When ``feature_voice_fast_path`` is on, a
permitted voice turn is answered directly from that result -- skipping the LLM
tool loop -- instead of the result being discarded.

The read is permission-gated here because the fast path bypasses the per-tool
RBAC filter. Fail closed: a user missing any required view permission does not
get the fast-path answer and falls through to the RBAC-enforced workflow.
"""

from __future__ import annotations

from typing import Any

#: Views required to surface each fast-path result type.
FAST_PATH_REQUIRED_VIEWS: dict[str, frozenset[tuple[str, str]]] = {
    "stock_check": frozenset({("part", "view"), ("stock", "view")}),
    "part_details": frozenset({("part", "view")}),
    "bom": frozenset({("part", "view")}),
    "location": frozenset({("part", "view"), ("stock", "view")}),
}


def fast_path_permitted(result_type: str, profile: frozenset[tuple[str, str]]) -> bool:
    """Whether a permission profile may receive this fast-path result type."""
    required = FAST_PATH_REQUIRED_VIEWS.get(result_type)
    if required is None:
        return False
    return required.issubset(profile)


def _part_label(part: dict[str, Any]) -> str:
    if not isinstance(part, dict):
        return "the part"
    return str(part.get("name") or part.get("IPN") or part.get("full_name") or "the part")


def format_fast_path_answer(result: dict[str, Any]) -> str | None:
    """Concise, spoken-friendly answer from a fast-path result dict.

    Returns ``None`` when the result cannot be rendered, so the caller falls
    back to the normal workflow.
    """
    if not isinstance(result, dict):
        return None

    rtype = result.get("type")
    part = result.get("part") or {}
    label = _part_label(part)

    if rtype == "stock_check":
        total = result.get("total_quantity", 0)
        return f"{label} has {total} in stock."

    if rtype == "part_details":
        details = result.get("part") or {}
        name = _part_label(details)
        description = str(details.get("description") or "").strip()
        return f"{name}: {description}." if description else f"{name}."

    if rtype == "bom":
        count = len(result.get("bom_items") or [])
        return f"{label} has {count} BOM line{'s' if count != 1 else ''}."

    if rtype == "location":
        locations = result.get("locations") or []
        if not locations:
            return f"{label} has no recorded stock location."
        first = locations[0]
        name = first.get("location", "Unknown")
        qty = first.get("quantity", 0)
        if len(locations) == 1:
            return f"{label} is in {name}, with {qty} on hand."
        return f"{label} is in {len(locations)} locations, including {name}."

    return None
