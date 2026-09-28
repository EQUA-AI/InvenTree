#!/usr/bin/env python3
"""RED/GREEN self-tests for the bootstrap-probe orchestration validators.

Run from the repository root:

    python3 contrib/container/bootstrap_probe/test_harness_validators.py

UNIT SELF-TEST ONLY. Every fixture below is FABRICATED in a scratch directory
(``unit-test-fixtures-*``) — these are unit-test fixtures, NOT acceptance
evidence, and must never be cited as probe results or read-only-window
evidence. The real evidence is produced only by
``contrib/container/demo-metrics-bootstrap-probe.sh`` against the disposable
database.

Each validator gets a GREEN case (a valid fixture must be accepted) and RED
cases (invalid fixtures must be rejected, i.e. the validator raises
``HarnessValidationError``):

* failed commands  — a nonzero probe exit code must fail the harness;
* blocked/missing transport evidence — a missing/empty/garbled transport log,
  a missing ``guard_installed`` marker, or ANY blocked connect attempt in a
  read-only command's log must fail the harness; the negative probe's log
  must instead CONTAIN a blocked attempt;
* artifact presence — missing or empty expected artifacts must fail.

Stdlib-only; no network, database or cloud work. Temporary files go to the
runtime scratch directory (TMPDIR), never /tmp.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harness_validators as hv

#: Scratch directory for the fabricated fixtures (never the system /tmp).
SCRATCH = os.environ.get('TMPDIR') or tempfile.gettempdir()

GUARD = {'event': 'guard_installed', 'allow': ['db:5432']}
ALLOWED = {
    'event': 'connect',
    'target': 'db:5432',
    'result': 'allowed',
    'reason': 'allowlist',
}
BLOCKED = {
    'event': 'connect',
    'target': '203.0.113.1:9',
    'result': 'blocked',
    'reason': 'not-on-allowlist',
}
BLOCKED_EX = {
    'event': 'connect_ex',
    'target': '198.51.100.7:443',
    'result': 'blocked',
    'reason': 'not-on-allowlist',
}


class ValidatorTestCase(unittest.TestCase):
    """Base: one fabricated-fixture directory per test, clearly labeled."""

    def setUp(self):
        """Create a per-test ``unit-test-fixtures-*`` scratch directory."""
        self.fixture_dir = tempfile.mkdtemp(prefix='unit-test-fixtures-', dir=SCRATCH)
        self.addCleanup(shutil.rmtree, self.fixture_dir, ignore_errors=True)

    def fixture(self, name, lines):
        """Write a fabricated JSONL transport log fixture; return its path."""
        path = os.path.join(self.fixture_dir, name)
        with open(path, 'w', encoding='utf-8') as handle:
            for line in lines:
                handle.write(line if isinstance(line, str) else json.dumps(line) + '\n')
        return path

    def assertRejected(self, fn, *args, **kwargs):
        """RED helper: the validator must reject the fabricated fixture."""
        with self.assertRaises(hv.HarnessValidationError):
            fn(*args, **kwargs)


class TestProbeExit(ValidatorTestCase):
    """Failed commands (syntax/auth/refusal/crash) must fail the harness."""

    def test_green_zero_exit_accepted(self):
        """GREEN: a zero probe exit code is accepted."""
        hv.check_probe_exit('plan', 0)

    def test_red_nonzero_exit_rejected(self):
        """RED: every nonzero probe exit code is rejected."""
        for rc in (1, 2, 3, 127, 255):
            with self.subTest(rc=rc):
                self.assertRejected(hv.check_probe_exit, 'plan', rc)


class TestNetlogGuardInstalled(ValidatorTestCase):
    """Missing guard evidence must fail the harness (fail closed)."""

    def test_green_guard_with_only_allowed(self):
        """GREEN: guard marker plus only allowlisted connects is accepted."""
        summary = hv.check_netlog(
            self.fixture('net_plan.jsonl', [GUARD, ALLOWED]), 'net_plan'
        )
        self.assertEqual(summary['blocked'], 0)

    def test_green_guard_with_zero_connects(self):
        """GREEN: guard marker alone suffices when no connect was attempted."""
        # Real probe processes make no Python-level connects at all (libpq
        # bypasses the socket wrapper): guard_installed alone is the evidence.
        summary = hv.check_netlog(
            self.fixture('net_verify.jsonl', [GUARD]), 'net_verify'
        )
        self.assertEqual(summary['allowed'], 0)
        self.assertEqual(summary['blocked'], 0)

    def test_red_missing_log_file_rejected(self):
        """RED: a missing transport log is unverified, so it is rejected."""
        self.assertRejected(
            hv.check_netlog,
            os.path.join(self.fixture_dir, 'net_absent.jsonl'),
            'net_absent',
        )

    def test_red_empty_log_rejected(self):
        """RED: an empty transport log carries no guard evidence."""
        self.assertRejected(
            hv.check_netlog, self.fixture('net_empty.jsonl', []), 'net_empty'
        )

    def test_red_missing_guard_marker_rejected(self):
        """RED: connects logged without guard_installed are rejected."""
        self.assertRejected(
            hv.check_netlog,
            self.fixture('net_noguard.jsonl', [ALLOWED, ALLOWED]),
            'net_noguard',
        )

    def test_red_malformed_json_line_rejected(self):
        """RED: a garbled log line invalidates the whole log."""
        self.assertRejected(
            hv.check_netlog,
            self.fixture('net_garbled.jsonl', [GUARD, 'not-json\n']),
            'net_garbled',
        )

    def test_red_non_object_entry_rejected(self):
        """RED: a non-object JSON entry invalidates the whole log."""
        self.assertRejected(
            hv.check_netlog,
            self.fixture('net_nonobj.jsonl', [GUARD, '[1, 2]\n']),
            'net_nonobj',
        )


class TestNetlogBlockedTransport(ValidatorTestCase):
    """Any attempted forbidden transport in a read-only command fails."""

    def test_red_blocked_connect_rejected(self):
        """RED: a blocked connect in a command log fails the harness."""
        self.assertRejected(
            hv.check_netlog,
            self.fixture('net_plan.jsonl', [GUARD, ALLOWED, BLOCKED]),
            'net_plan',
        )

    def test_red_blocked_connect_ex_rejected(self):
        """RED: a blocked connect_ex in a command log fails the harness."""
        self.assertRejected(
            hv.check_netlog,
            self.fixture('net_cleanup.jsonl', [GUARD, BLOCKED_EX]),
            'net_cleanup',
        )

    def test_red_blocked_even_when_guard_caught_it(self):
        """RED: an attempt the guard caught still fails the harness."""
        # The attempt being caught downstream (command still exited 0) is
        # exactly the silent-success case: it must STILL fail the harness.
        self.assertRejected(
            hv.check_netlog,
            self.fixture('net_verify.jsonl', [GUARD, BLOCKED]),
            'net_verify',
        )


class TestNetlogNegativeProbe(ValidatorTestCase):
    """The negative probe's transport failure evidence is expected — separately."""

    def test_green_blocked_attempt_recorded(self):
        """GREEN: the negative log must record the blocked attempt."""
        summary = hv.check_netlog(
            self.fixture('net_negative.jsonl', [GUARD, BLOCKED]),
            'net_negative',
            expect_blocked=True,
        )
        self.assertEqual(summary['blocked'], 1)

    def test_red_expected_block_missing_rejected(self):
        """RED: no blocked attempt means the defense was never exercised."""
        self.assertRejected(
            hv.check_netlog,
            self.fixture('net_negative.jsonl', [GUARD, ALLOWED]),
            'net_negative',
            expect_blocked=True,
        )

    def test_red_expected_block_without_guard_rejected(self):
        """RED: a blocked attempt without the guard marker is rejected."""
        self.assertRejected(
            hv.check_netlog,
            self.fixture('net_negative.jsonl', [BLOCKED]),
            'net_negative',
            expect_blocked=True,
        )


class TestArtifacts(ValidatorTestCase):
    """Expected logs/artifacts must exist and be non-empty."""

    def test_green_nonempty_artifact_accepted(self):
        """GREEN: an existing non-empty artifact is accepted."""
        path = os.path.join(self.fixture_dir, 'plan.json')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('{}\n')
        hv.check_artifact(path, 'plan.json')

    def test_red_missing_artifact_rejected(self):
        """RED: a missing expected artifact is rejected."""
        self.assertRejected(
            hv.check_artifact,
            os.path.join(self.fixture_dir, 'absent.json'),
            'absent.json',
        )

    def test_red_empty_artifact_rejected(self):
        """RED: an empty expected artifact is rejected."""
        self.assertRejected(
            hv.check_artifact, self.fixture('empty.json', []), 'empty.json'
        )


if __name__ == '__main__':
    print(
        'UNIT SELF-TEST of harness_validators — all fixtures below are '
        'FABRICATED unit-test fixtures, NOT acceptance evidence.'
    )
    unittest.main()
