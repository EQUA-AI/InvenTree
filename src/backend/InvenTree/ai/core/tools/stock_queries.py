"""Bounded server-owned stock questions shared by text and voice.

These native reads retain current permissions. Unrecognized/ambiguous grammar
stays on the existing planning path; no model prose certifies stock evidence.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

_STOCK = re.compile(r"\b(?:stock|on[- ]hand|allocated|available|inventory)\b", re.I)
_LOCATION = re.compile(r"\b(?:where|locations?|located|stored|kept)\b", re.I)
_PART = re.compile(r"\bpart\s*(?:id\s*)?[:#]?\s*(\d{1,9})\b(?![\w-]|\.\w)", re.I)
_REVISION = re.compile(r"\brevision\s+([A-Za-z0-9._-]+)", re.I)
_IPN = re.compile(r"\b[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)+\b")
_ANAPHORA = re.compile(r"\b(?:those|these|them|they|their|it|its|ones)\b", re.I)
_COMPARE = re.compile(
    r"\b(at least|at most|no more than|no less than|less than|fewer than|more than|greater than|under|below|over|above|exactly)\s*(\d+(?:\.\d+)?)\b",
    re.I,
)
_OPERATORS = {
    "under": "lt",
    "below": "lt",
    "less than": "lt",
    "fewer than": "lt",
    "at most": "lte",
    "no more than": "lte",
    "over": "gt",
    "above": "gt",
    "more than": "gt",
    "greater than": "gt",
    "at least": "gte",
    "no less than": "gte",
    "exactly": "eq",
}


def stock_question(text: str) -> bool:
    """Recognize a read shape, retaining the existing business-effect fence."""
    from ai.core.tools.capabilities import _write_intent

    return (
        bool(_STOCK.search(text) or _LOCATION.search(text))
        and _write_intent(text.casefold()) is None
    )


def _category(terms: tuple[str, ...]) -> int | dict | None:
    from ai.core.tools.capabilities import _lexicon_variants
    from part.models import PartCategory

    if len(terms) != 1:
        return None
    matches = PartCategory.objects.filter(name__iexact=terms[0])
    if not matches.exists():
        ids = [
            pk
            for pk, name in PartCategory.objects.values_list("pk", "name")
            if terms[0] in _lexicon_variants(name)
        ]
        matches = PartCategory.objects.filter(pk__in=ids)
    if matches.count() == 1:
        return matches.values_list("pk", flat=True).get()
    if not matches.exists():
        return {
            "text": "That stock category is no longer available. Which category should I use?",
            "evidence": {"requires_clarification": True},
        }
    choices = "; ".join(
        f"{row.pathstring} (category {row.pk})" for row in matches.order_by("pk")[:5]
    )
    return {
        "text": f"Which stock category do you mean? {choices}. Specify its category ID or path.",
        "evidence": {"requires_clarification": True},
    }


def _part_answer(data: dict, *, modality: str, locations: bool) -> str:
    from ai.core.workflows.wf8_lookup import category_location_answer

    if data.get("resolved") is not True:
        return "Current stock for that exact part could not be verified."
    identity = f"part {data['part_id']} ({data.get('part_ipn') or data.get('part_name')}; revision {data.get('part_revision') or 'not recorded'})"
    if locations:
        # Use the same consistency and partial-coverage checks as category reads.
        proxy = {**data, "category_id": data["part_id"], "category_name": identity}
        return category_location_answer(proxy, category_id=data["part_id"], modality=modality)
    text = f"Current stock for {identity}: on hand {data['total_in_stock']}."
    if data.get("allocated_stock") is not None:
        text += f" Allocated {data['allocated_stock']}; available {data['available_stock']}."
    return text


def resolve_stock_query(query: str, context: dict[str, Any] | None = None) -> dict | None:
    """Answer a narrowly recognized native stock read, or keep existing planning."""
    from ai.core.tools.capabilities import matched_category_terms
    from ai.core.tools.inventree.read.database import _current_user
    from ai.core.tools.inventree.read.parts import resolve_exact_ipn
    from ai.core.tools.inventree.read.stock import category_stock_summary, current_stock_summary
    from users.permissions import check_user_role

    if not stock_question(query):
        return None
    history = (context or {}).get("conversation_history")
    history = history if isinstance(history, (list, tuple)) else []
    subject = query
    explicit_terms = matched_category_terms(query)
    if _ANAPHORA.search(query) and not explicit_terms and not _PART.search(query):
        for row in reversed(history):
            if isinstance(row, dict) and row.get("role") == "user":
                subject = str(row.get("content") or "") + " " + query
                break
    if not _STOCK.search(subject) and not (_PART.search(subject) and _LOCATION.search(query)):
        return None
    # Native current-stock reads already enforce these historical exclusions.
    predicates = re.sub(
        r"\bexclude\s+historical,?\s+depleted\s+and\s+non-current\s+stock\s+rows\b",
        "",
        subject,
        flags=re.I,
    )
    predicates = re.sub(
        r"\b(?:locations and quantities|current (?:in-)?stock)\s+only\b", "", predicates, flags=re.I
    )
    # Remove the stock idiom before rejecting extra prepositional predicates.
    # "in stock" is supported; "at Room 101" needs a scoped native read.
    predicates = re.sub(r"\bin[- ]stock\b", "", predicates, flags=re.I)
    predicates = _COMPARE.sub("", predicates)
    terms = matched_category_terms(query, history)
    for term in terms:
        predicates = re.sub(
            rf"\bin\s+(?:the\s+)?{re.escape(term)}(?:\s+category)?\b",
            "",
            predicates,
            flags=re.I,
        )
    # Additional location/category predicates must not be silently removed.
    if re.search(
        r"\b(?:except|exclude|excluding|highest|lowest|top|only|at|in|within|from|active|inactive|batch|serial|reserved|expired|expiry|damaged|quarantined|after|before|since|between|supplier|customer)\b",
        predicates,
        re.I,
    ):
        return None
    parts = set(_PART.findall(subject))
    revision = _REVISION.search(subject)
    ipns = [m[0] for m in _IPN.finditer(subject) if any(c.isdigit() for c in m[0])]
    comparisons = list(_COMPARE.finditer(subject))
    if len(parts) > 1 or len(ipns) > 1 or len(comparisons) > 1:
        return None
    if not parts and not (revision and ipns) and not comparisons:
        return None  # Existing unfiltered category route and clarification rail.
    if not parts and not (revision and ipns) and not terms:
        return None
    if comparisons and re.search(r"\b(?:allocated|available)\b", subject, re.I):
        return None  # The comparator is defined on current on-hand quantity.
    user = _current_user()
    if user is None or not all(check_user_role(user, role, "view") for role in ("part", "stock")):
        return {
            "text": "Current stock could not be verified with your permissions.",
            "evidence": {"resolved": False},
        }
    modality = str((context or {}).get("modality") or "text")
    location_requested = bool(_LOCATION.search(query))
    if parts or (revision and ipns):
        if comparisons:
            return None
        part_id = int(next(iter(parts))) if parts else None
        if ipns:
            resolved = resolve_exact_ipn(ipns[0], revision[1] if revision else None)
            if isinstance(resolved, dict):
                return None  # Existing captured native revision-question rail.
            if part_id is not None and part_id != resolved:
                return {
                    "text": "The supplied part ID and IPN/revision do not identify the same exact part. Please check them.",
                    "evidence": {"resolved": False},
                }
            part_id = resolved
        data = current_stock_summary(part_id)
        if revision and data.get("resolved") and data.get("part_revision") != revision[1]:
            return {
                "text": "That part ID does not match the requested revision. Please check the exact part and revision.",
                "evidence": {"resolved": False},
            }
        return {
            "text": _part_answer(data, modality=modality, locations=location_requested),
            "evidence": data,
        }
    category = _category(terms)
    if isinstance(category, dict):
        return category
    if category is None:
        return None
    comparison = comparisons[0] if comparisons else None
    data = category_stock_summary(
        category,
        None,
        group_by="location" if location_requested else None,
        quantity_comparison=_OPERATORS[comparison[1].casefold()] if comparison else None,
        quantity_value=Decimal(comparison[2]) if comparison else None,
    )
    if location_requested:
        from ai.core.workflows.wf8_lookup import category_location_answer

        return {
            "text": category_location_answer(data, category_id=category, modality=modality),
            "evidence": data,
        }
    if data.get("resolved") is not True:
        return {"text": "Current category stock could not be verified.", "evidence": data}
    if comparison:
        rows = data["parts"][: 3 if modality == "voice" else 20]
        label = f"{comparison[1]} {comparison[2]}"
        text = f"{data['part_count']} exact parts in {data['category_name']} have current stock {label}, including zero-stock parts when they match."
        entries = []
        for row in rows:
            identity = row.get("part_ipn") or row["name"]
            revision = row.get("part_revision") or "not recorded"
            entry = f"part {row['part_id']} ({identity}; revision {revision}): {row['total_stock']}"
            if modality == "voice" and len(text) + sum(len(e) + 2 for e in [*entries, entry]) > 600:
                break
            entries.append(entry)
        text += " " + "; ".join(entries) + ("." if entries else "")
        remaining = data["part_count"] - len(entries)
        if remaining:
            text += f" {remaining} more matching parts are not listed here."
        return {"text": text, "evidence": data}
    return {
        "text": f"Current stock for {data['category_name']} totals {data['total_in_stock']}.",
        "evidence": data,
    }
