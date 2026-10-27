"""Bounded server-owned stock questions shared by text and voice.

These native reads retain current permissions. Unsupported filters on a
recognized target stay unverified; no model prose certifies stock evidence.
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


def stock_category_switch(text: str) -> bool:
    """A category inventory question does not change durable entity focus."""
    from ai.core.tools.capabilities import matched_category_terms

    return bool(
        re.fullmatch(
            r"\s*switch to .+?(?:,?\s+how much stock is there\??|\s+stock(?: now)?[.!?]?)\s*",
            text,
            re.I,
        )
        and stock_question(text)
        and not re.match(r"\s*switch to\s+(?:machine|asset|task|card|work\s*order)\b", text, re.I)
        and matched_category_terms(text)
    )


def _unavailable_filter() -> dict:
    return {
        "text": "I could not verify current stock with all the requested filters. Please specify the exact part and location; additional exclusions or stock predicates need a supported native read.",
        "evidence": {"resolved": False, "filters_verified": False},
    }


def _location(text: str) -> tuple[str, int | None, dict | None]:
    """Resolve an explicit location ID or exact human name/path, never a name's digits."""
    from django.db.models import Q
    from stock.models import StockLocation

    explicit = re.search(r"\b(?:at|in)\s+location\s+id\s*[:#]?\s*(\d+)\b", text, re.I)
    if explicit:
        return text[: explicit.start()] + text[explicit.end() :], int(explicit[1]), None
    named = re.search(
        r"\b(?:at|in)\s+([\w /-]+?)(?=\s+for\s+(?:exact\s+)?part|[?.!]|$)", text, re.I
    )
    if not named:
        return text, None, None
    name = named[1].strip()
    # A compact human label is matched only to actual names, never to a PK.
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name)
    rows = StockLocation.objects.filter(
        Q(name__iexact=name)
        | Q(name__iexact=spaced)
        | Q(pathstring__iexact=name)
        | Q(pathstring__iexact=spaced)
    )
    if rows.count() != 1:
        return (
            text,
            None,
            {
                "text": "Which exact stock location do you mean? Please specify its full path or location ID.",
                "evidence": {"resolved": False, "requires_clarification": True},
            },
        )
    return (
        text[: named.start()] + text[named.end() :],
        rows.values_list("pk", flat=True).get(),
        None,
    )


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

    if len(terms) > 1:
        return {
            "text": "Which single stock category should I use? Specify its category ID or full path.",
            "evidence": {"resolved": False, "requires_clarification": True},
        }
    if not terms:
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
    if data.get("location_id") is not None:
        identity += (
            f" at {data['location_name']} (location {data['location_id']}, including sublocations)"
        )
    if locations:
        # Use the same consistency and partial-coverage checks as category reads.
        proxy = {**data, "category_id": data["part_id"], "category_name": identity}
        text = category_location_answer(proxy, category_id=data["part_id"], modality=modality)
        if data.get("allocated_stock") is not None:
            text += f" Allocated {data['allocated_stock']}; available {data['available_stock']}."
        else:
            text += " Allocated and available quantities for this location are not verified."
        return text
    text = f"Current stock for {identity}: on hand {data['total_in_stock']}."
    if data.get("allocated_stock") is not None:
        text += f" Allocated {data['allocated_stock']}; available {data['available_stock']}."
    else:
        text += " Allocated and available quantities for this location are not verified."
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
    from ai.core.read_numbers import normalize_read_numbers

    parsed = normalize_read_numbers(query)
    if parsed.ambiguous:
        return {
            "text": "Please confirm the exact part ID or stock quantity; that numeric field is ambiguous.",
            "evidence": {"resolved": False, "requires_clarification": True},
        }
    query = parsed.text
    query = re.sub(r"\bIPN(?=\d)", "IPN ", query, flags=re.I)
    if not _REVISION.search(query):
        query = re.sub(r"\b(REV-[A-Za-z0-9._-]+)\b", r"revision \1", query, flags=re.I)
    history = (context or {}).get("conversation_history")
    history = history if isinstance(history, (list, tuple)) else []
    subject = query
    explicit_terms = matched_category_terms(query)
    if _ANAPHORA.search(query) and not explicit_terms and not _PART.search(query):
        for row in reversed(history):
            if isinstance(row, dict) and row.get("role") == "user":
                subject = normalize_read_numbers(str(row.get("content") or "")).text + " " + query
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
    # Permission precedes location name resolution, including clarification.
    user = _current_user()
    if user is None or not all(check_user_role(user, role, "view") for role in ("part", "stock")):
        return {
            "text": "Current stock could not be verified with your permissions.",
            "evidence": {"resolved": False},
        }
    predicates, location_id, location_error = (
        _location(predicates) if _PART.search(subject) else (predicates, None, None)
    )
    if location_error:
        return location_error
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
        return _unavailable_filter() if (_PART.search(subject) or terms) else None
    parts = set(_PART.findall(subject))
    revision = _REVISION.search(subject)
    explicit_ipns = re.findall(
        r"\bIPN\s*[:#]?\s*([A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*)", subject, re.I
    )
    ipns = list(
        dict.fromkeys([
            *explicit_ipns,
            *[
                m[0]
                for m in _IPN.finditer(subject)
                if any(c.isdigit() for c in m[0])
                and not (revision and revision.start() <= m.start() < revision.end())
            ],
        ])
    )
    comparisons = list(_COMPARE.finditer(subject))
    if len(parts) > 1 or len(ipns) > 1 or len(comparisons) > 1:
        return _unavailable_filter()
    if (
        not parts
        and not (revision and ipns)
        and not comparisons
        and not stock_category_switch(query)
    ):
        return None  # Existing unfiltered category route and clarification rail.
    if not parts and not (revision and ipns) and not terms:
        return None
    if comparisons and re.search(r"\b(?:allocated|available)\b", subject, re.I):
        return _unavailable_filter()  # The comparator is current on-hand quantity.
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
            return _unavailable_filter()
        part_id = int(next(iter(parts))) if parts else None
        if ipns and part_id is None:
            resolved = resolve_exact_ipn(ipns[0], revision[1] if revision else None)
            if isinstance(resolved, dict):
                return {
                    "text": resolved.get("note") or "The exact part could not be verified.",
                    "evidence": resolved,
                }
            part_id = resolved
        data = current_stock_summary(part_id, location_id)
        if ipns and data.get("resolved") and data.get("part_ipn") != ipns[0]:
            return {
                "text": "The supplied part ID and IPN do not identify the same exact part. Please check them.",
                "evidence": {"resolved": False},
            }
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
