"""S39: golden-set harness — always-on schema/scoring tests + the live gate.

The live test is marked ``golden`` and skips cleanly unless
AIMMS_GOLDEN_LIVE=1 with a base URL configured (fork CI cannot hold Azure
secrets, so the automated gate arrives only where credentials exist; the
documented policy makes the manual run mandatory for prompt-touching PRs).
"""

# ruff: noqa: E402

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")
os.environ.setdefault("INVENTREE_TOKEN", "test-token")

import django

django.setup()

import pytest
from ai.core.evals import judge as judge_mod
from ai.core.evals import schema as schema_mod

GOLDEN_LIVE = os.environ.get("AIMMS_GOLDEN_LIVE") == "1" and bool(
    os.environ.get("AIMMS_GOLDEN_BASE_URL")
)


# --- always-on: the curated files stay structurally valid --------------------


def test_items_load_and_validate():
    items = schema_mod.load_items()
    assert len(items) >= 15, "the golden set must not silently shrink"
    problems = schema_mod.validate_items(items)
    assert not problems, [f"{p.item_id}: {p.problem}" for p in problems]


def test_redteam_loads_and_validates():
    cases = schema_mod.load_redteam()
    assert len(cases) >= 5
    problems = schema_mod.validate_redteam(cases)
    assert not problems, [f"{p.item_id}: {p.problem}" for p in problems]


def test_trap_items_exist_for_every_trap_type():
    """The set must keep at least one item per trap class (EX-ADR-002)."""
    items = schema_mod.load_items()
    present = {item.trap_type for item in items}
    assert {"absent_spec", "ambiguous_symptom", "wrong_machine"} <= present


def test_attachment_fixture_items_pin_the_fixture_set():
    """R2 items pin the reserved fixture-set version, never the index name."""
    items = schema_mod.load_items()
    attachment_items = [i for i in items if i.id.startswith("attachment-")]
    assert len(attachment_items) >= 4
    assert {i.corpus_version for i in attachment_items} == {"aimms-attachment-fixtures-v1"}
    assert any(i.trap_type == "absent_spec" for i in attachment_items)


def test_attachment_fixture_documents_exist():
    """The pinned fixture set is only usable if its documents ship with the
    repo — the seed command reads them and cannot regenerate them."""
    import pathlib

    import ai.core.evals as evals_pkg

    fixtures = (
        pathlib.Path(evals_pkg.__file__).resolve().parent / "golden" / "fixtures" / "attachments"
    )
    expected = {
        "eval-hx200-manual.md",
        "eval-hx200-gasket-datasheet.md",
        "eval-zr9-offlimits-manual.md",
    }
    present = {p.name for p in fixtures.glob("*.md")}
    assert expected <= present, f"missing fixture documents: {expected - present}"


def test_corpus_env_is_set_valued():
    """AIMMS_GOLDEN_CORPUS pins several corpora at once (comma-separated):
    items skip only when their pin is absent from the set; a single value
    and an unset env behave exactly as before."""
    from ai.core.evals.run_golden import run_items

    def _item(item_id, corpus_version):
        return schema_mod.GoldenItem(
            id=item_id,
            question="q?",
            expected_behavior="answer",
            ground_truth="a",
            ground_truth_keys=(),
            trap_type="none",
            locale="en",
            corpus_version=corpus_version,
            dataset="demo",
        )

    items = [
        _item("governed", "eaits-manuals-v4a"),
        _item("attachment", "aimms-attachment-fixtures-v1"),
        _item("stale", "eaits-manuals-v3"),
        _item("unpinned", ""),
    ]

    class _NeverCalled:
        def post(self, *a, **k):  # pragma: no cover - skip paths only
            raise AssertionError("no HTTP expected")

    scores = run_items(
        _NeverCalled(),
        items,
        "eaits-manuals-v4a,aimms-attachment-fixtures-v1",
        "live",  # every item above is demo/unpinned -> dataset skip catches
        False,
    )
    by_id = {score.item_id: score for score in scores}
    # Both pinned-and-deployed corpora pass the corpus gate (they then skip
    # on the dataset gate, proving the corpus gate admitted them).
    assert "corpus" not in by_id["governed"].detail
    assert "corpus" not in by_id["attachment"].detail
    assert by_id["stale"].outcome == "skip"
    assert "corpus" in by_id["stale"].detail
    # Single-value env keeps the original semantics.
    single = run_items(_NeverCalled(), items, "eaits-manuals-v4a", "live", False)
    single_by_id = {score.item_id: score for score in single}
    assert "corpus" in single_by_id["attachment"].detail
    # Unset env skips nothing on corpus grounds.
    unset = run_items(_NeverCalled(), items, "", "live", False)
    assert all("corpus" not in score.detail for score in unset)


def test_judge_schema_is_strict():
    assert judge_mod.JUDGE_SCHEMA["additionalProperties"] is False
    assert set(judge_mod.JUDGE_SCHEMA["required"]) == set(judge_mod.JUDGE_SCHEMA["properties"])


# --- always-on: EX-ADR-002 scoring semantics --------------------------------


def _item(**overrides) -> schema_mod.GoldenItem:
    values = {
        "id": "x",
        "question": "q",
        "expected_behavior": "answer",
        "ground_truth": "truth",
        "trap_type": "none",
    }
    values.update(overrides)
    return schema_mod.GoldenItem(**values)


def test_wrong_answer_is_a_hard_fail():
    score = judge_mod.score_item(_item(), {"verdict": "wrong", "cited_keys_present": True})
    assert score.outcome == "fail"


def test_abstain_on_trap_is_a_pass():
    item = _item(expected_behavior="abstain", trap_type="absent_spec")
    score = judge_mod.score_item(item, {"verdict": "abstained", "cited_keys_present": True})
    assert score.outcome == "pass"


def test_abstain_on_answerable_is_a_warn_never_a_fail():
    score = judge_mod.score_item(_item(), {"verdict": "abstained", "cited_keys_present": True})
    assert score.outcome == "warn"


def test_correct_on_a_trap_passes_because_ground_truth_encodes_the_trap():
    """Trap ground truths DESCRIBE the required refusal/correction, so a
    'correct' verdict means the answer matched that contract (e.g. corrected
    a wrong-machine premise); fabrication contradicts the ground truth and
    comes back as 'wrong', which hard-fails."""
    item = _item(expected_behavior="abstain", trap_type="wrong_machine")
    score = judge_mod.score_item(item, {"verdict": "correct", "cited_keys_present": True})
    assert score.outcome == "pass"


def test_wrong_on_a_trap_is_still_a_hard_fail():
    item = _item(expected_behavior="abstain", trap_type="absent_spec")
    score = judge_mod.score_item(item, {"verdict": "wrong", "cited_keys_present": True})
    assert score.outcome == "fail"


def test_missing_required_citations_fail_a_correct_answer():
    item = _item(ground_truth_keys=("manual:pump:seals",))
    score = judge_mod.score_item(item, {"verdict": "correct", "cited_keys_present": False})
    assert score.outcome == "fail"


def test_clarify_expected_and_delivered_passes():
    item = _item(expected_behavior="clarify", ground_truth="")
    score = judge_mod.score_item(item, {"verdict": "clarified", "cited_keys_present": True})
    assert score.outcome == "pass"


def test_runner_imports_standalone_as_evals_package():
    """CI runs `python -m evals.run_golden` from ai/core with only the
    harness deps installed — the relative-import chain must hold and a
    missing base URL must exit 2 before any network use."""
    import pathlib
    import subprocess
    import sys

    core_dir = pathlib.Path(__file__).resolve().parents[1]
    env = {**os.environ, "AIMMS_GOLDEN_BASE_URL": ""}
    result = subprocess.run(
        [sys.executable, "-m", "evals.run_golden"],
        cwd=core_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    assert "AIMMS_GOLDEN_BASE_URL" in result.stderr


def test_judge_item_serializes_without_creds():
    """judge_item builds its payload locally; only the call needs Azure."""
    captured = {}

    def fake_call(payload: str) -> dict:
        captured["payload"] = payload
        return {"verdict": "correct", "cited_keys_present": True, "rationale": ""}

    verdict = judge_mod.judge_item(_item(), "an answer", judge_call=fake_call)
    assert verdict["verdict"] == "correct"
    assert '"question": "q"' in captured["payload"]


# --- live gate ---------------------------------------------------------------


@pytest.mark.golden
@pytest.mark.skipif(
    not GOLDEN_LIVE,
    reason="live golden gate; set AIMMS_GOLDEN_LIVE=1 + AIMMS_GOLDEN_BASE_URL (+ creds)",
)
def test_golden_set_against_live_deployment():
    """The phase exit gate: zero hard fails against the live dev endpoint."""
    from ai.core.evals.run_golden import main

    assert main([]) == 0
