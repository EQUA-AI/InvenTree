"""Fail-closed orchestration validators for the whole-process bootstrap probe.

Harness-only (never used by any deployment, never imported by probe
processes). The orchestrator ``demo-metrics-bootstrap-probe.sh`` calls this
module via its CLI after every stage so a broken run can never look green:

* ``probe-exit``  — a nonzero probe exit code (syntax error, auth failure,
  command refusal, crash) is a FAILED probe; the harness must propagate it,
  never log it and continue.
* ``netlog``      — each probe command's transport log must exist, must show
  the ``guard_installed`` marker (the guard was active for the process's whole
  lifetime) and — for the real read-only commands — must contain ZERO blocked
  connect attempts. An attempted forbidden transport fails the harness even
  when the guard caught it and the command kept going. Only the negative
  probe (``--expect-blocked``) must RECORD a blocked attempt.
* ``artifact``    — expected output artifacts and logs must exist and be
  non-empty.

Stdlib-only; no network, database or cloud access. Exit code 0 = validated,
1 = validation failed (printed to stderr).

Unit-test coverage for these validators lives in
``test_harness_validators.py`` (fabricated fixtures, not acceptance evidence).
"""

from __future__ import annotations

import argparse
import json
import os
import sys


class HarnessValidationError(Exception):
    """Raised when a harness check fails; the probe must not proceed."""


def read_netlog(path: str) -> list[dict]:
    """Load a transport-guard JSONL log; fail closed on any anomaly."""
    if not os.path.isfile(path):
        raise HarnessValidationError(
            f'transport log missing: {path!r} — the guard never produced '
            'evidence for this process, so the run is unverified'
        )
    entries = []
    with open(path, encoding='utf-8') as handle:
        for lineno, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise HarnessValidationError(
                    f'transport log {path!r} line {lineno} is not valid JSON '
                    f'({exc}); refusing to treat the log as evidence'
                ) from exc
            if not isinstance(entry, dict):
                raise HarnessValidationError(
                    f'transport log {path!r} line {lineno} is not a JSON '
                    'object; refusing to treat the log as evidence'
                )
            entries.append(entry)
    if not entries:
        raise HarnessValidationError(
            f'transport log {path!r} is empty — no guard_installed marker, '
            'so the run is unverified'
        )
    return entries


def check_probe_exit(tag: str, rc: int) -> None:
    """A nonzero command exit is a failed probe; never a silent success."""
    if rc != 0:
        raise HarnessValidationError(
            f'probe [{tag}] exited {rc} — a nonzero exit (syntax error, auth '
            'failure, refusal or crash) is a FAILED probe, never evidence'
        )


def check_netlog(path: str, label: str, *, expect_blocked: bool = False) -> dict:
    """Validate one process's transport log.

    Always required: the log exists and shows ``guard_installed`` (the guard
    was armed before any application code ran). For real read-only commands
    any ``blocked`` entry is a FAILURE — an attempted forbidden transport is
    evidence of a defect even when the guard caught it downstream. For the
    negative probe (``expect_blocked=True``) at least one blocked attempt must
    be recorded, otherwise the transport defense was never exercised.
    """
    entries = read_netlog(path)
    guard = [e for e in entries if e.get('event') == 'guard_installed']
    if not guard:
        raise HarnessValidationError(
            f'{label}: guard_installed marker missing from {path!r} — the '
            'transport guard was not active for this process'
        )
    blocked = [e for e in entries if e.get('result') == 'blocked']
    allowed = [e for e in entries if e.get('result') == 'allowed']
    summary = {
        'label': label,
        'events': len(entries),
        'guard_installed': len(guard),
        'allowed': len(allowed),
        'blocked': len(blocked),
    }
    if expect_blocked:
        if not blocked:
            raise HarnessValidationError(
                f'{label}: expected a blocked forbidden-transport attempt in '
                f'{path!r}, but none was recorded — the transport defense was '
                'never exercised'
            )
    elif blocked:
        targets = sorted({str(e.get('target')) for e in blocked})
        raise HarnessValidationError(
            f'{label}: forbidden transport attempt(s) recorded in {path!r}: '
            f'{targets} — the harness fails on ANY attempted forbidden '
            'transport, even one the guard blocked and the command survived'
        )
    return summary


def check_artifact(path: str, label: str) -> None:
    """An expected artifact/log must exist and be non-empty."""
    if not os.path.isfile(path):
        raise HarnessValidationError(f'{label}: expected artifact missing: {path!r}')
    if os.path.getsize(path) == 0:
        raise HarnessValidationError(f'{label}: expected artifact is empty: {path!r}')


def _run(args) -> int:
    try:
        if args.cmd == 'probe-exit':
            check_probe_exit(args.tag, args.rc)
            print(f'HARNESS VALIDATION OK: probe [{args.tag}] exit code 0')
        elif args.cmd == 'netlog':
            summary = check_netlog(
                args.path, args.label, expect_blocked=args.expect_blocked
            )
            note = (
                ' (blocked attempt EXPECTED and recorded here)'
                if args.expect_blocked
                else ''
            )
            print(
                'HARNESS VALIDATION OK: '
                f'{summary["label"]}: guard_installed='
                f'{summary["guard_installed"]} allowed={summary["allowed"]} '
                f'blocked={summary["blocked"]}{note}'
            )
        elif args.cmd == 'artifact':
            check_artifact(args.path, args.label)
            print(f'HARNESS VALIDATION OK: {args.label}: {args.path}')
        else:  # pragma: no cover - argparse constrains this
            raise HarnessValidationError(f'unknown command {args.cmd!r}')
    except HarnessValidationError as exc:
        print(f'HARNESS VALIDATION FAILED: {exc}', file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    """CLI entry point: parse args, run one validator, exit 0 or 1."""
    parser = argparse.ArgumentParser(
        prog='harness_validators',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('probe-exit', help='require a probe exit code of 0')
    p.add_argument('--tag', required=True)
    p.add_argument('--rc', required=True, type=int)

    p = sub.add_parser('netlog', help='validate one process transport log')
    p.add_argument('--path', required=True)
    p.add_argument('--label', required=True)
    p.add_argument(
        '--expect-blocked',
        action='store_true',
        help='negative probe: a blocked forbidden-transport attempt must be '
        'recorded (the default instead fails on any blocked attempt)',
    )

    p = sub.add_parser('artifact', help='require a non-empty artifact file')
    p.add_argument('--path', required=True)
    p.add_argument('--label', required=True)

    return _run(parser.parse_args(argv))


if __name__ == '__main__':
    sys.exit(main())
