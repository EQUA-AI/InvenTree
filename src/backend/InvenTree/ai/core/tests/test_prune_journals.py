"""Q48 journal-pruning CLI tests (S16): 12-month window, gold untouched."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai.core.evals import prune_journals

OLD = datetime.now(UTC) - timedelta(days=400)
FRESH = datetime.now(UTC) - timedelta(days=10)


def _touch(path: Path, stamp: datetime) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}\n", encoding="utf-8")
    os.utime(path, (stamp.timestamp(), stamp.timestamp()))
    return path


def _journal(path: Path, *, generated_at: datetime | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"seed": 1, "pass": 1}
    if generated_at is not None:
        header["generated_at"] = generated_at.isoformat()
    path.write_text(json.dumps(header) + "\n", encoding="utf-8")
    return path


def test_header_generated_at_wins_over_mtime(tmp_path):
    journal = _journal(tmp_path / "run-x.jsonl", generated_at=OLD)
    os.utime(journal, None)  # fresh mtime; the header must still win
    assert prune_journals.journal_age(journal) < datetime.now(UTC) - timedelta(days=390)


def test_filename_epoch_used_when_no_header_stamp(tmp_path):
    epoch = int(time.time()) - 400 * 86400
    journal = _journal(tmp_path / f"run-{epoch}-pass1.jsonl", generated_at=None)
    os.utime(journal, None)
    age = prune_journals.journal_age(journal)
    assert age.timestamp() == epoch


def test_months_boundary_and_campaign_units(tmp_path):
    old_journal = _journal(tmp_path / "loose" / "run-old.jsonl", generated_at=OLD)
    fresh_journal = _journal(tmp_path / "loose" / "run-new.jsonl", generated_at=FRESH)
    old_campaign = tmp_path / "camp-old"
    _touch(old_campaign / "preflight.json", OLD)
    _journal(old_campaign / "run-01" / "run-x.jsonl", generated_at=FRESH)
    fresh_campaign = tmp_path / "camp-new"
    _touch(fresh_campaign / "preflight.json", FRESH)

    cutoff = datetime.now(UTC) - timedelta(days=31 * 12)
    prunable = prune_journals.collect_prunable(tmp_path, cutoff)

    # The old campaign prunes as ONE unit (even holding a fresh journal);
    # fresh artifacts survive.
    assert old_journal in prunable
    assert old_campaign in prunable
    assert fresh_journal not in prunable
    assert fresh_campaign not in prunable
    assert all(old_campaign not in p.parents for p in prunable)


def test_gold_is_never_prunable(tmp_path):
    _touch(tmp_path / "questions.yaml", OLD)
    _journal(tmp_path / "gold" / "run-old.jsonl", generated_at=OLD)
    cutoff = datetime.now(UTC)
    assert prune_journals.collect_prunable(tmp_path, cutoff) == []


def test_dry_run_deletes_nothing_and_destructive_matches(tmp_path, capsys):
    journal = _journal(tmp_path / "run-old.jsonl", generated_at=OLD)
    argv = ["--journal-root", str(tmp_path), "--months", "12"]

    assert prune_journals.main([*argv, "--dry-run"]) == 0
    assert journal.exists()
    assert "would prune 1 item(s)" in capsys.readouterr().out

    # Destructive without --yes refuses.
    assert prune_journals.main(argv) == 2
    assert journal.exists()

    assert prune_journals.main([*argv, "--yes"]) == 0
    assert not journal.exists()
    assert "pruned 1 item(s)" in capsys.readouterr().out


def test_in_repo_root_is_refused():
    repo_dir = Path(__file__).resolve().parent
    assert prune_journals.main(["--journal-root", str(repo_dir), "--dry-run"]) == 2


def test_no_root_is_a_clean_refusal(monkeypatch):
    monkeypatch.delenv("AIMMS_BATTERY_JOURNAL_DIR", raising=False)
    assert prune_journals.main(["--dry-run"]) == 2
