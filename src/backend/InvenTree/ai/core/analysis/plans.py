"""Deterministic plan-lite for the analytics intents (S7, §7.3-lite).

The §7.3 query plan is enum- and size-bounded; v1 fills it with a
DETERMINISTIC keyword mapper rather than a model — the same posture as the
intent rules. The mapper only ever emits vocabulary the ``tasks``
analytics module validates again server-side, with one deliberate
exception: a question that asks to group by a PERSON emits the
unsupported ``performer`` grouping on purpose, so the server's allow-list
refuses it and the user gets the honest ``grouping_unavailable``
limitation instead of a silently different table (§8.3: identities are
never grouped).

Every choice the mapper makes is echoed in the answer (grouping,
date field, timezone, bucket, window), so a default is visible, never
silent. A model-emitted plan can replace this mapper later without the
executor changing: the output shape is the contract.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

#: First match wins; ``performer`` is a deliberate refusal probe (see
#: module docstring). Default: ``machine``.
_GROUPING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:per|by|each|which|who)\s+"
            r"(?:technician|person|people|operator|performer|assignee)\b"
            r"|\b(?:technician|who)\b",
            re.IGNORECASE,
        ),
        "performer",
    ),
    (re.compile(r"\bpriorit", re.IGNORECASE), "priority"),
    (
        re.compile(r"\b(?:type|corrective|preventive|inspection|calibration)\b", re.IGNORECASE),
        "work_order_type",
    ),
    (re.compile(r"\b(?:status|lifecycle|state)\b", re.IGNORECASE), "lifecycle_status"),
    (re.compile(r"\bcomponent", re.IGNORECASE), "component_ref"),
)

_BUCKET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:week|weekly)\b", re.IGNORECASE), "week"),
    (re.compile(r"\b(?:quarter|quarterly)\b", re.IGNORECASE), "quarter"),
)

_RECORDS_POPULATION = re.compile(
    r"\b(?:maintenance|service)\s+(?:record|history|log)", re.IGNORECASE
)

#: §7.3 domain defaults: completion language → the completion clock;
#: scheduling language → the schedule; otherwise intake (``created_at``).
_COMPLETION_LANGUAGE = re.compile(
    r"\b(?:complet\w*|repair\w*|fix\w*|performed|done)\b",  # codespell:ignore complet
    re.IGNORECASE,
)
_SCHEDULE_LANGUAGE = re.compile(r"\bschedul\w*\b", re.IGNORECASE)


def _date_field(text: str) -> str:
    if _COMPLETION_LANGUAGE.search(text):
        return "actual_completed_at"
    if _SCHEDULE_LANGUAGE.search(text):
        return "scheduled_start"
    return "created_at"


def build_aggregate_plan(text: str) -> dict[str, Any]:
    """The fleet_aggregate plan: one grouping over the work-order population."""
    content = str(text or "")
    grouping = "machine"
    for pattern, name in _GROUPING_PATTERNS:
        if pattern.search(content):
            grouping = name
            break
    return {
        "plan_version": 1,
        "intent": "fleet_aggregate",
        "population_type": "work_orders",
        "grouping": grouping,
        "date_field": _date_field(content),
        "population_requirement": "complete",
    }


def default_trend_window(today: datetime.date, *, months: int = 12) -> tuple[str, str]:
    """Twelve calendar months including the partial current month, half-open.

    A trend question with no explicit window must still be bounded (the
    36-bucket cap refuses open-ended history); "the last year" is the
    domain default and is always echoed in the answer's filters.
    """
    end_exclusive = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    if not 1 <= months <= 36:
        raise ValueError("trend month window must be between 1 and 36")
    first_month = today.year * 12 + today.month - months
    year, month = divmod(first_month, 12)
    start = today.replace(year=year, month=month + 1, day=1)
    return start.isoformat(), end_exclusive.isoformat()


def build_trend_plan(text: str) -> dict[str, Any]:
    """The trend_analysis plan: a bucketed series over one population."""
    content = str(text or "")
    bucket = "month"
    for pattern, name in _BUCKET_PATTERNS:
        if pattern.search(content):
            bucket = name
            break
    if _RECORDS_POPULATION.search(content):
        population = "maintenance_records"
        date_field = "date"
    else:
        population = "work_orders"
        date_field = _date_field(content)
    plan = {
        "plan_version": 1,
        "intent": "trend_analysis",
        "population_type": population,
        "bucket": bucket,
        "date_field": date_field,
        "population_requirement": "complete",
    }
    window = re.search(r"\b(?:last|past)\s+(\d+)\s+months?\b", content, re.IGNORECASE)
    if window is not None:
        # Longer numbers are necessarily outside the 36-month limit. Do
        # not parse arbitrary-length integers supplied in turn content.
        plan["window_months"] = int(window[1]) if len(window[1]) <= 2 else 37
    return plan


__all__ = ["build_aggregate_plan", "build_trend_plan", "default_trend_window"]
