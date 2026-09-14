"""V4 preserves failures, exclusions, missingness and measurement provenance."""

import pytest
from ai.core.voice.validation import FAMILIES, ValidationCampaign, percentile, summarize
from pydantic import ValidationError


def campaign():
    return {
        "run_id": "a" * 32,
        "source_sha": "b" * 40,
        "flags_evidence_ref": "c" * 64,
        "earcons_enabled": True,
        "attempts": [
            {
                "id": f"{index:032x}",
                "scenario": "S01",
                "workflow": family,
                "accent": "US",
                "input_voice": "Jenny",
                "rate": "base",
                "setup_ref": "d" * 32,
                "route": "virtual",
                "mode": "continuous",
                "temperature": "warm",
                "task_kind": "read",
                "method": "emulated",
                "status": "not_run",
                "hands_free_eligible": True,
            }
            for index, family in enumerate(sorted(FAMILIES), 1)
        ],
    }


def test_missing_is_not_zero_and_simulation_is_not_a_baseline():
    result = summarize(ValidationCampaign(**campaign()))
    assert result["planned"] == 6 and result["attempted"] == 0
    assert result["groups"][0]["metrics"]["local_stop_ms"]["p95_ms"] is None
    assert result["blocking"] == {"safety": "NOT MEASURED", "local_stop": "NOT MEASURED"}
    assert result["minimum_baseline_met"] is False


def test_baseline_counts_distinct_observed_provider_turns_per_accent():
    data = campaign()
    base = data["attempts"]
    voices = {"US": ("Jenny", "Andrew"), "CA": ("Clara", "Liam"), "GB": ("Sonia", "Ryan")}
    data["attempts"] = []
    for i in range(90):
        accent = ("US", "CA", "GB")[i // 30]
        data["attempts"].append({
            **base[i % 6],
            "id": f"{i + 1:032x}",
            "accent": accent,
            "input_voice": voices[accent][i % 2],
            "status": "failed",
            "method": "synthetic_real_provider",
            "provider_submitted": True,
            "provider_turn_ref": f"{i + 1:064x}",
        })
    result = summarize(ValidationCampaign(**data))
    assert result["real_provider_base_turns"] == {"US": 30, "CA": 30, "GB": 30}
    assert result["minimum_baseline_met"] is True
    assert result["blocking"]["safety"] == "NOT MEASURED"
    assert result["owner_enforcement_decision"].startswith("PENDING")
    data["attempts"][1]["provider_turn_ref"] = data["attempts"][0]["provider_turn_ref"]
    with pytest.raises(ValidationError):
        ValidationCampaign(**data)


def test_failures_setup_timeouts_and_hybrid_remain_in_denominator():
    data = campaign()
    data["attempts"][0].update(status="completed", completion="hands_free")
    data["attempts"][1].update(status="completed", completion="hybrid")
    data["attempts"][2].update(status="failed")
    data["attempts"][3].update(status="setup_failed")
    data["attempts"][4].update(status="timeout")
    result = summarize(ValidationCampaign(**data))["groups"][0]
    assert result["planned"] == 6 and result["attempted"] == 5 and result["failed"] == 3
    assert result["hands_free_percent"] == 20
    assert result["metrics"]["audible_ack_ms"]["missing"] == 5


def test_rerun_is_separate_and_cannot_erase_safety_failure():
    data = campaign()
    original = data["attempts"][0]
    original.update(status="failed", wrong_effects=1)
    data["attempts"].append({
        **original,
        "id": "f" * 32,
        "rerun_of": original["id"],
        "status": "completed",
        "completion": "hands_free",
        "wrong_effects": 0,
    })
    result = summarize(ValidationCampaign(**data))
    assert result["blocking"]["safety"] == "FAIL"
    assert result["reruns"] == 1 and result["primary_planned"] == 6
    assert len(result["groups"]) == 2


def test_first_measured_local_stop_blocks_but_other_targets_track():
    data = campaign()
    data["attempts"][0].update(status="failed", timing={"local_stop_ms": 251})
    result = summarize(ValidationCampaign(**data))
    assert result["blocking"]["local_stop"] == "FAIL"
    assert result["owner_enforcement_decision"].startswith("PENDING")
    assert percentile([1, 2, 3, 4, 100], 0.95) == 100
    assert percentile([], 0.95) is None


def test_proxies_and_boolean_latencies_cannot_qualify_audio():
    for changes in (
        {"audible_ack_ms": 1.0},
        {"audible_ack_ms": True},
        {"provider_submitted": True},
    ):
        data = campaign()
        data["attempts"][0].update(status="completed", **changes)
        with pytest.raises(ValidationError):
            ValidationCampaign(**data)


def test_exclusions_are_explicit_and_do_not_hide_planned_coverage():
    data = campaign()
    data["attempts"][0].update(hands_free_eligible=False, exclusion="screen_policy")
    result = summarize(ValidationCampaign(**data))
    assert len(result["exclusions"]) == 1 and result["planned"] == 6
    data["attempts"][1]["hands_free_eligible"] = False
    with pytest.raises(ValidationError):
        ValidationCampaign(**data)


def test_duplicates_missing_families_and_extra_content_are_rejected():
    data = campaign()
    data["attempts"][0]["transcript"] = "PRIVATE_SENTINEL"
    with pytest.raises(ValidationError):
        ValidationCampaign(**data)


def test_unrun_measurements_and_synthetic_human_labels_are_rejected():
    for changes in ({"timing": {"local_stop_ms": 1}}, {"method": "human"}):
        data = campaign()
        data["attempts"][0].update(changes)
        with pytest.raises(ValidationError):
            ValidationCampaign(**data)
    data = campaign()
    data["attempts"].append(data["attempts"][0])
    with pytest.raises(ValidationError):
        ValidationCampaign(**data)
    data = campaign()
    data["attempts"][0]["workflow"] = data["attempts"][1]["workflow"]
    with pytest.raises(ValidationError):
        ValidationCampaign(**data)
