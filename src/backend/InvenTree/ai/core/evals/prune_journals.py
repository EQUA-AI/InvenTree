"""Prune raw battery journals past the 12-month private-store window (Q48).

Human-run, never scheduled: the private evaluation store is not mounted
server-side, so this CLI (the ``run_battery`` idiom — import-safe without
Django or Azure) is the honest implementation of the journal retention
row. It deletes whole run files and campaign directories older than the
window and never touches gold — ``questions.yaml`` and gold atoms are
program-lifetime with their disposition recorded at closure.

Age determination, most- to least-authoritative:
1. the ``generated_at`` field in a journal's header line;
2. the ``run-<epoch>-…`` filename stamp;
3. a campaign directory's ``preflight.json`` mtime;
4. the file mtime.

Usage::

    python -m ai.core.evals.prune_journals --journal-root /secure/journals \
        --months 12 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .scenarios import assert_outside_repo

_RUN_NAME = re.compile(r"^run-(\d{9,12})\b")
#: Files never deleted by this tool, regardless of age.
_PROTECTED_NAMES = {"questions.yaml", "questions.yml"}
_PROTECTED_DIRS = {"gold"}


def _journal_generated_at(path: Path) -> datetime | None:
    """The header line's ``generated_at``, if the journal carries one."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
        stamp = json.loads(first).get("generated_at")
        if stamp:
            parsed = datetime.fromisoformat(stamp)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _filename_epoch(path: Path) -> datetime | None:
    match = _RUN_NAME.match(path.name)
    if match:
        return datetime.fromtimestamp(int(match.group(1)), tz=UTC)
    return None


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def journal_age(path: Path) -> datetime:
    """Best-available creation time for one journal file."""
    return _journal_generated_at(path) or _filename_epoch(path) or _mtime(path)


def campaign_age(directory: Path) -> datetime:
    """Best-available creation time for one campaign directory."""
    preflight = directory / "preflight.json"
    if preflight.is_file():
        return _mtime(preflight)
    return _mtime(directory)


def _is_protected(path: Path) -> bool:
    if path.name in _PROTECTED_NAMES:
        return True
    return any(part in _PROTECTED_DIRS for part in path.parts)


def collect_prunable(root: Path, cutoff: datetime) -> list[Path]:
    """Journal files and campaign dirs under ``root`` older than ``cutoff``.

    A directory containing ``preflight.json`` is treated as one campaign
    unit (pruned whole); loose ``*.jsonl`` files are aged individually.
    Gold artifacts are never returned.
    """
    prunable: list[Path] = []
    for entry in sorted(root.iterdir()):
        if _is_protected(entry):
            continue
        if entry.is_dir():
            if (entry / "preflight.json").is_file():
                if campaign_age(entry) < cutoff:
                    prunable.append(entry)
            else:
                prunable.extend(collect_prunable(entry, cutoff))
        elif entry.is_file() and entry.suffix == ".jsonl" and journal_age(entry) < cutoff:
            prunable.append(entry)
    return prunable


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns a process exit code."""
    import os
    import shutil

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--journal-root",
        default=os.environ.get("AIMMS_BATTERY_JOURNAL_DIR"),
        help="Private journal store (default: env AIMMS_BATTERY_JOURNAL_DIR)",
    )
    parser.add_argument(
        "--extra-root",
        action="append",
        default=[],
        help="Additional private dirs to prune with the same window (repeatable)",
    )
    parser.add_argument(
        "--months", type=int, default=12, help="Retention window in months (Q48: 12)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List what would be pruned; delete nothing"
    )
    parser.add_argument(
        "--yes", action="store_true", help="Confirm deletion (required unless --dry-run)"
    )
    args = parser.parse_args(argv)

    if not args.journal_root:
        print(
            "No journal root: pass --journal-root or set AIMMS_BATTERY_JOURNAL_DIR",
            file=sys.stderr,
        )
        return 2
    if args.months < 1:
        print("--months must be at least 1", file=sys.stderr)
        return 2
    if not args.dry_run and not args.yes:
        print("Destructive run: pass --yes (or use --dry-run)", file=sys.stderr)
        return 2

    roots = []
    for raw in [args.journal_root, *args.extra_root]:
        try:
            root = assert_outside_repo(Path(raw))
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        if not root.is_dir():
            print(f"Not a directory: {root}", file=sys.stderr)
            return 2
        roots.append(root)

    cutoff = datetime.now(UTC) - timedelta(days=31 * args.months)
    pruned = 0
    for root in roots:
        for target in collect_prunable(root, cutoff):
            age_days = (
                datetime.now(UTC)
                - (campaign_age(target) if target.is_dir() else journal_age(target))
            ).days
            label = "campaign" if target.is_dir() else "journal"
            if args.dry_run:
                print(f"WOULD PRUNE {label} {target} (age {age_days}d)")
            else:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                print(f"pruned {label} {target} (age {age_days}d)")
            pruned += 1
    print(f"{'would prune' if args.dry_run else 'pruned'} {pruned} item(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
