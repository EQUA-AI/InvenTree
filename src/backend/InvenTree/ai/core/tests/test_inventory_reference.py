"""Live references update mutable facts without weakening golden behavior."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        ("version", "live-inventory-reference-v1"),
        ("version", "live-inventory-reference-v2"),
        ("version", "live-inventory-reference-v3"),
        ("version", "live-inventory-reference-v4"),
        ("version", "live-inventory-reference-v5"),
        ("version", "live-inventory-reference-v6"),
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


@pytest.mark.parametrize(
    "kind,app,model_name,row,expected",
    [
        (
            "assetmachine",
            "assets",
            "AssetMachine",
            {"name": "Pump", "serial": "PUMP-7"},
            {"owner_reference": None, "owner_name": "Pump", "owner_serial": "PUMP-7"},
        ),
        (
            "part",
            "part",
            "Part",
            {"name": "Seal", "IPN": "SEAL-1"},
            {"owner_reference": None, "owner_name": "Seal", "owner_ipn": "SEAL-1"},
        ),
        ("workorder", "tasks", "WorkOrder", {"reference": "WO-24"}, {"owner_reference": "WO-24"}),
    ],
)
def test_source_owner_identity_uses_the_typed_domain_record(kind, app, model_name, row, expected):
    model = Mock()
    model.objects.filter.return_value.values.return_value.first.return_value = row
    with patch("django.apps.apps.get_model", return_value=model) as resolve:
        assert reference._source_owner_identity(kind, 19) == expected
    resolve.assert_called_once_with(app, model_name)
    model.objects.filter.assert_called_once_with(pk=19)
    model.objects.filter.return_value.values.assert_called_once_with(*row)


def test_missing_source_owner_does_not_invent_labels():
    model = Mock()
    model.objects.filter.return_value.values.return_value.first.return_value = None
    with patch("django.apps.apps.get_model", return_value=model):
        assert reference._source_owner_identity("assetmachine", 19) == {"owner_reference": None}


def test_supplementary_source_requires_exact_indexed_bytes():
    data = b"# Uploaded source\nA verified additional fact.\n"
    attachment = SimpleNamespace(attachment=Mock())
    attachment.attachment.open.side_effect = lambda _mode: io.BytesIO(data)
    digest = hashlib.sha256(data).hexdigest()
    assert reference._indexed_markdown(attachment, {digest}) == {
        "source_sha256": digest,
        "source_text": data.decode(),
    }
    with pytest.raises(ValueError, match="differs from indexed"):
        reference._indexed_markdown(attachment, {"old-revision"})


def test_supplementary_source_limit_does_not_silently_truncate():
    data = b"x" * 65537
    attachment = SimpleNamespace(attachment=Mock())
    attachment.attachment.open.return_value = io.BytesIO(data)
    with pytest.raises(ValueError, match="exceeds 64 KiB"):
        reference._indexed_markdown(attachment, {hashlib.sha256(data).hexdigest()})


def test_stored_source_identity_supplements_only_the_matching_corpus():
    snapshot = _snapshot()
    snapshot["corpus_sources"] = [
        {
            "corpus_version": "aimms-media-fixtures-v2",
            "canonical_filename": "eval-hx200-nameplate.png",
            "stored_filename": "eval-hx200-nameplate_suffix.png",
            "owner_type": "workorder",
            "owner_id": 131,
            "owner_reference": "WO-EVAL-HX200",
        }
    ]
    originals = {item.id: item for item in schema.load_items()}
    updated = {
        item.id: item for item in reference.apply_reference(list(originals.values()), snapshot)
    }
    for name in ("media-nameplate-grounded", "cross-corpus-pressure-agreement"):
        assert "eval-hx200-nameplate_suffix.png" in updated[name].reference_context
        assert updated[name].ground_truth == originals[name].ground_truth
        assert updated[name].ground_truth_keys == originals[name].ground_truth_keys
    assert updated["attachment-torque-grounded"] == originals["attachment-torque-grounded"]


def test_video_reference_distinguishes_domain_record_id_from_attachment_id():
    """The work-order primary key and reference identify the same owner record."""
    snapshot = _snapshot()
    snapshot["corpus_sources"] = [
        {
            "corpus_version": "aimms-video-fixtures-v2",
            "attachment_id": 11,
            "owner_type": "workorder",
            "owner_id": 132,
            "owner_reference": "WO-EVAL-HX200-VIDEO",
        }
    ]
    originals = {item.id: item for item in schema.load_items()}
    updated = {
        item.id: item for item in reference.apply_reference(list(originals.values()), snapshot)
    }
    video = updated["video-seal-segment-grounded"]
    assert "owner_id is the work order ID" in video.reference_context
    assert "attachment_id is the separate attachment record ID" in video.reference_context
    assert '"owner_id": 132' in video.reference_context
    assert '"owner_reference": "WO-EVAL-HX200-VIDEO"' in video.reference_context
    assert video.ground_truth == originals[video.id].ground_truth
    assert video.ground_truth_keys == originals[video.id].ground_truth_keys
    assert video.question == originals[video.id].question
    assert updated["attachment-torque-grounded"] == originals["attachment-torque-grounded"]


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
    assert "Bracket; Bolt; Cover" in updated.ground_truth
    assert "stock" not in updated.ground_truth
    assert "quantity" not in updated.ground_truth
    context = json.loads(updated.reference_context)
    assert context["items"][0]["stock_quantity"] == "12"
    assert context["arithmetic_buildable_quantity"] == 5
    assert updated.question == original.question
    assert updated.expected_behavior == original.expected_behavior
    snapshot["bom"]["items"][0]["quantity"] = "-1"
    with pytest.raises(ValueError):
        reference.apply_reference([original], snapshot)
