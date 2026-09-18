"""Source ordering is pure; no provider, Django setup or authority is implied."""

from types import SimpleNamespace

import pytest
from ai.core.memory.retrieval_planning import manual_retrieval_plan


def test_serial_less_selection_has_only_verified_exact_route():
    plan = manual_retrieval_plan(
        asset_set=SimpleNamespace(machine_pks=(1,), serials=(), models=("Pump",)),
        document_selected=True,
        attachments_available=True,
    )
    assert plan.serial_unresolved
    assert plan.steps == ("verified_exact_controlled",)
    assert "fleet_wide_controlled" not in plan.steps


def test_fallback_coordinates_are_frozen_and_ordered():
    serials = {"B", "A"}
    plan = manual_retrieval_plan(
        asset_set=SimpleNamespace(machine_pks=(2, 1), serials=serials, models=("Pump",)),
        document_selected=True,
        attachments_available=True,
    )
    serials.add("FOREIGN")
    assert plan.serials == ("A", "B")
    assert plan.machine_ids == (1, 2)
    assert plan.steps == (
        "pinned_revision",
        "exact_asset_controlled",
        "verified_model_config",
        "fleet_wide_controlled",
        "asset_attachments",
    )


def test_unbounded_or_invalid_native_coordinates_refuse():
    with pytest.raises(ValueError):
        manual_retrieval_plan(asset_set=SimpleNamespace(machine_pks=(True,), serials=(), models=()))
    with pytest.raises(ValueError):
        manual_retrieval_plan(
            asset_set=SimpleNamespace(machine_pks=range(1, 102), serials=(), models=())
        )
