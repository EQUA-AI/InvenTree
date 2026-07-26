"""V26/V27/V28: stock tools must return the answer, not raw serializer rows.

Regression cover for a live production failure (2026-07-26): asked for the stock
level of part 49 (C_100pF_0402), both API calls succeeded -- search resolved the
part with in_stock 8902, and the stock endpoint returned eight rows totalling
8902 -- yet the assistant answered "I couldn't find any stock information",
because the tool handed the model ~13 KB of 42-field rows with no total, no part
name, and a null location on every row.
"""

from __future__ import annotations

import json

import pytest
from ai.core.tools.inventree.read.stock import (
    stock_location_label,
    summarize_stock_items,
)

#: The real shape returned for part 49, trimmed to the fields that matter here.
#: Quantities and locations are the production values.
PART_49 = {
    "pk": 49,
    "name": "C_100pF_0402",
    "IPN": None,
    "description": "Ceramic capacitor, 100pF in 0402 SMD package",
    "in_stock": 8902.0,
}
STOCK_49 = [
    {
        "pk": 727,
        "part": 49,
        "quantity": 74.0,
        "location": 11,
        "location_name": None,
        "location_detail": {"name": "Loose Parts", "pathstring": "Electronics Lab/Loose Parts"},
    },
    {
        "pk": 726,
        "part": 49,
        "quantity": 90.0,
        "location": 11,
        "location_name": None,
        "location_detail": {"name": "Loose Parts", "pathstring": "Electronics Lab/Loose Parts"},
    },
    {
        "pk": 728,
        "part": 49,
        "quantity": 91.0,
        "location": 11,
        "location_name": None,
        "location_detail": {"name": "Loose Parts", "pathstring": "Electronics Lab/Loose Parts"},
    },
    {
        "pk": 153,
        "part": 49,
        "quantity": 300.0,
        "location": 8,
        "location_name": None,
        "location_detail": {"name": "Reel Storage", "pathstring": "Electronics Lab/Reel Storage"},
    },
    {
        "pk": 152,
        "part": 49,
        "quantity": 8347.0,
        "location": 8,
        "location_name": None,
        "location_detail": {"name": "Reel Storage", "pathstring": "Electronics Lab/Reel Storage"},
    },
]


def test_summary_reports_the_total_that_was_answered_as_not_found():
    summary = summarize_stock_items(STOCK_49, part=PART_49)

    # The exact figure the assistant failed to report in production.
    assert summary["total_in_stock"] == pytest.approx(8902.0)
    assert summary["resolved"] is True
    assert summary["part_name"] == "C_100pF_0402"
    assert summary["item_count"] == 5


def test_summary_groups_quantities_by_named_location():
    summary = summarize_stock_items(STOCK_49, part=PART_49)

    # Named, path-qualified, and ordered by quantity -- not bare location ids.
    assert summary["locations"] == [
        {"name": "Electronics Lab/Reel Storage", "quantity": 8647.0},
        {"name": "Electronics Lab/Loose Parts", "quantity": 255.0},
    ]


def test_zero_on_hand_is_resolved_not_missing():
    """A part that exists with no stock rows must not read as 'not found'."""
    summary = summarize_stock_items([], part={"pk": 77, "name": "X", "in_stock": 0.0})

    assert summary["resolved"] is True
    assert summary["total_in_stock"] == pytest.approx(0.0)
    assert summary["locations"] == []


def test_total_prefers_the_part_record_over_the_returned_rows():
    """The part figure is authoritative when rows are capped by `limit`."""
    truncated = STOCK_49[:2]  # 164 units of the real 8902

    summary = summarize_stock_items(truncated, part=PART_49)

    assert summary["total_in_stock"] == pytest.approx(8902.0)
    assert summary["item_count"] == 2


def test_total_falls_back_to_summing_rows_without_a_part_record():
    summary = summarize_stock_items(STOCK_49, part={}, part_id=49)

    assert summary["total_in_stock"] == pytest.approx(8902.0)
    assert summary["part_id"] == 49


def test_location_label_prefers_path_then_name_then_id():
    assert stock_location_label({"location_detail": {"pathstring": "A/B", "name": "B"}}) == "A/B"
    assert stock_location_label({"location_detail": {"name": "B"}}) == "B"
    # The production shape before the location_detail fix: bare id, null name.
    assert stock_location_label({"location": 11, "location_name": None}) == "Location 11"
    assert stock_location_label({}) == "Unassigned"


def test_summary_is_small_enough_to_reason_over():
    """The raw payload was ~13 KB for eight rows; the answer must stay tiny."""
    summary = summarize_stock_items(STOCK_49, part=PART_49)

    assert len(json.dumps(summary)) < 1024


def test_summary_survives_malformed_quantities():
    rows = [
        {"quantity": None, "location_detail": {"name": "A"}},
        {"quantity": "not-a-number", "location_detail": {"name": "A"}},
        {"quantity": 5, "location_detail": {"name": "A"}},
    ]

    summary = summarize_stock_items(rows, part={})

    assert summary["total_in_stock"] == pytest.approx(5.0)
