"""Live references update mutable facts without weakening golden behavior."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "evals"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


schema = _load("schema")
reference = _load("inventory_reference")


def _snapshot():
    return {
        "version": reference.VERSION,
        "captured_at": "2026-09-16T00:00:00+00:00",
        "part_count": 800,
        "zero_stock_count": 40,
        "stock_part": reference.PART_NAME,
        "stock_part_id": 28,
        "stock_by_location": [
            {"location": "Shelf A", "quantity": "12.5"},
            {"location": "Shelf B", "quantity": "7.5"},
        ],
    }


def test_reference_updates_live_facts_preserving_question_and_assertions():
    items = schema.load_items()
    changed = reference.apply_reference(items, _snapshot())
    originals = {item.id: item for item in items}
    updated = {item.id: item for item in changed}
    assert "800" in updated["part-count-total"].ground_truth
    assert "40" in updated["zero-stock-count"].ground_truth
    assert "20.0" in updated["stock-breakdown-location"].ground_truth
    assert "Shelf A: 12.5" in updated["stock-breakdown-location"].ground_truth
    for item in changed:
        original = originals[item.id]
        assert item.question == original.question
        assert item.expected_behavior == original.expected_behavior
        assert item.ground_truth_keys == original.ground_truth_keys
        assert item.corpus_version == original.corpus_version
        assert item.locale == original.locale
    assert updated["bom-widget-assembly"] == originals["bom-widget-assembly"]
    assert updated["ambiguous-symptom-noise"] == originals["ambiguous-symptom-noise"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", "unknown"),
        ("stock_part", "different fixture"),
        ("part_count", -1),
        ("part_count", True),
        ("zero_stock_count", 801),
        ("stock_by_location", []),
        ("stock_by_location", [{"location": "Shelf A", "quantity": "NaN"}]),
    ],
)
def test_invalid_reference_fails_closed(key, value):
    snapshot = copy.deepcopy(_snapshot())
    snapshot[key] = value
    with pytest.raises(ValueError):
        reference.apply_reference([], snapshot)


def test_snapshot_digest_changes_when_inventory_changes():
    before = _snapshot()
    after = {**before, "part_count": 801}
    assert reference.reference_digest(before) != reference.reference_digest(after)


def test_bom_reference_includes_verified_quantities_and_limiting_stock():
    snapshot = _snapshot()
    snapshot["bom"] = {
        "part_name": reference.BOM_PART_NAME,
        "part_id": 77,
        "items": [
            {
                "part_id": 1,
                "name": "Bracket",
                "quantity": "2",
                "stock_quantity": "12",
                "optional": False,
            },
            {
                "part_id": 2,
                "name": "Bolt",
                "quantity": "4",
                "stock_quantity": "20",
                "optional": False,
            },
            {
                "part_id": 3,
                "name": "Cover",
                "quantity": "1",
                "stock_quantity": "0",
                "optional": True,
            },
        ],
    }
    original = next(item for item in schema.load_items() if item.id == "bom-widget-assembly")
    updated = reference.apply_reference([original], snapshot)[0]
    assert "Bracket: quantity 2, stock 12" in updated.ground_truth
    assert "permits 5 assemblies arithmetically" in updated.ground_truth
    assert updated.question == original.question
    assert updated.expected_behavior == original.expected_behavior
    snapshot["bom"]["items"][0]["quantity"] = "-1"
    with pytest.raises(ValueError):
        reference.apply_reference([original], snapshot)
