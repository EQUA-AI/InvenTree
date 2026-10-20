"""VOICE-H07: the isolated ai/core registry must carry a real, applicable
migration graph.

The prior registry (auth/contenttypes/sessions/approvals/aichat/voice only)
could not load the migration graph at all: ``aichat.0042_memory_consent``
depends on ``assets.0015_alter_machineanomaly_work_order`` and the loader
raised ``NodeNotFoundError`` before any table existed. These tests pin the
real, unmodified repository migration history for the isolated registry:

* the loader graph builds and is history-consistent for the installed apps;
* a fresh, empty, file-backed scratch database migrates to every head from
  nothing (no schema-editor shortcut, no stub models, no disabled modules);
* an intermediate history state upgrades forward to head;
* a repeated migrate is idempotent.

Every database these tests touch is a disposable per-test SQLite file created
here; the shared island database and its fixtures are untouched.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import django
import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

if not django_apps.ready:
    django.setup()

# The exact cross-app edge whose absence broke the island graph (VOICE-H07).
CONSENT_NODE = ("aichat", "0042_memory_consent")
CONSENT_DEP = ("assets", "0015_alter_machineanomaly_work_order")
INTERMEDIATE_NODE = ("aichat", "0041_accounterasuretombstone")


def _heads_applied(executor: MigrationExecutor) -> bool:
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return plan == []


def _leaf_keys() -> set[tuple[str, str]]:
    loader = MigrationLoader(connection)
    loader.build_graph()
    return set(loader.graph.leaf_nodes())


@pytest.fixture
def disposable_default_db():
    """Swap the DEFAULT connection onto a brand-new empty scratch SQLite file.

    The previous fixture registered a hand-built extra alias whose settings
    dict lacked the normalised CONN_*/TEST entries, while repository
    ``RunPython`` data migrations read and write through default managers and
    are therefore default-connection coupled. This fixture keeps the real,
    normalised connection wrapper and only replaces its ``NAME`` (a
    disposable per-test file under the runner scratch directory), restoring
    the original default afterwards.
    """
    wrapper_settings = connection.settings_dict
    original_name = wrapper_settings["NAME"]
    original_entry = connections.databases["default"]
    scratch_dir = Path(tempfile.mkdtemp(prefix="registry-migrate-"))
    scratch_name = str(scratch_dir / "scratch.sqlite3")
    connections.close_all()
    wrapper_settings["NAME"] = scratch_name
    original_entry["NAME"] = scratch_name
    try:
        yield connection
    finally:
        connections.close_all()
        wrapper_settings["NAME"] = original_name
        original_entry["NAME"] = original_name


class TestMigrationRegistryConsistency:
    def test_loader_builds_complete_graph(self, disposable_default_db):
        started = time.monotonic()
        loader = MigrationLoader(connection)
        loader.build_graph()
        for node in (CONSENT_NODE, CONSENT_DEP):
            assert node in loader.graph.nodes, f"missing migration node {node}"
        assert CONSENT_DEP in {n.key for n in loader.graph.node_map[CONSENT_NODE].parents}, (
            "assets.0015 must remain a dependency of aichat.0042"
        )
        print(
            f"REGISTRY_CASE=loader_graph status=ok nodes={len(loader.graph.nodes)}"
            f" elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )

    def test_graph_is_history_consistent(self, disposable_default_db):
        started = time.monotonic()
        assert MigrationRecorder(connection).applied_migrations() == {}
        loader = MigrationLoader(connection)
        loader.build_graph()
        loader.check_consistent_history(connection)
        print(
            f"REGISTRY_CASE=consistent_history status=ok elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )


@pytest.mark.timeout(600)
class TestRealDisposableMigrations:
    def test_fresh_database_migrates_to_head_from_empty(self, disposable_default_db):
        started = time.monotonic()
        recorder = MigrationRecorder(connection)
        assert recorder.applied_migrations() == {}, "case must start from empty history"
        call_command("migrate", verbosity=0, interactive=False)
        applied = recorder.applied_migrations()
        assert CONSENT_NODE in applied
        assert CONSENT_DEP in applied
        leaves = _leaf_keys()
        assert leaves <= set(applied), "unapplied migrations after fresh migrate"
        executor = MigrationExecutor(connection)
        assert _heads_applied(executor), "unapplied migrations after fresh migrate"
        print(
            f"REGISTRY_CASE=fresh_to_head status=ok applied={len(applied)}"
            f" leaves={len(leaves)} elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )

    def test_intermediate_history_upgrades_to_head(self, disposable_default_db):
        started = time.monotonic()
        recorder = MigrationRecorder(connection)
        assert recorder.applied_migrations() == {}, "case must start from empty history"
        call_command(
            "migrate",
            "aichat",
            "0041_accounterasuretombstone",
            verbosity=0,
            interactive=False,
        )
        applied = recorder.applied_migrations()
        assert INTERMEDIATE_NODE in applied
        assert CONSENT_NODE not in applied, "0042 must not exist at 0041"
        executor = MigrationExecutor(connection)
        assert not _heads_applied(executor), "intermediate state must be below head"
        call_command("migrate", verbosity=0, interactive=False)
        applied = recorder.applied_migrations()
        leaves = _leaf_keys()
        assert leaves <= set(applied), "upgrade did not reach head"
        assert CONSENT_NODE in applied
        print(
            f"REGISTRY_CASE=intermediate_upgrade status=ok applied={len(applied)}"
            f" leaves={len(leaves)} elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )

    def test_repeated_migrate_is_idempotent(self, disposable_default_db):
        started = time.monotonic()
        recorder = MigrationRecorder(connection)
        assert recorder.applied_migrations() == {}, "case must start from empty history"
        call_command("migrate", verbosity=0, interactive=False)
        before = dict(recorder.applied_migrations())
        leaves = _leaf_keys()
        assert leaves <= set(before), "first migrate must reach head"
        call_command("migrate", verbosity=0, interactive=False)
        after = dict(recorder.applied_migrations())
        assert before == after, "second migrate changed the applied history"
        print(
            f"REGISTRY_CASE=repeat_migrate status=ok applied={len(after)}"
            f" elapsed={time.monotonic() - started:.1f}s",
            flush=True,
        )


# Child program for the durable startup/root-URL regression below: runs in a
# genuinely fresh interpreter (own process, own empty disposable SQLite from
# the fixture module) and reports real startup evidence - std-logging error
# records, the real root URLconf import, the fixture's Site registry contract,
# the real migration graph, and the abstract-token control.
STARTUP_CHILD = """
import logging, os, sys, time, traceback
sys.argv = ['manage.py', 'test', 'migrate']
os.environ['DJANGO_SETTINGS_MODULE'] = 'ai.core.tests.settings'


class _Capture(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self, level=logging.ERROR)
        self.records = []

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = '<unprintable>'
        exc = None
        if record.exc_info and record.exc_info[0] is not None:
            exc = ''.join(traceback.format_exception(*record.exc_info))
        self.records.append((record.name, record.levelname, msg, exc))


cap = _Capture()
logging.getLogger().addHandler(cap)
import django
t0 = time.monotonic()
django.setup()
print('REGISTRY_CHILD django_setup=ok elapsed=%.1fs' % (time.monotonic() - t0), flush=True)
failed = False
try:
    import InvenTree.urls
    print('REGISTRY_URL_IMPORT=status=ok patterns=%d' % len(InvenTree.urls.urlpatterns), flush=True)
except Exception:
    failed = True
    print('REGISTRY_URL_IMPORT=status=error', flush=True)
    traceback.print_exc()
from django.conf import settings
print('REGISTRY_SITE_CONTRACT=sites_%s site_id=%r site_multi=%r' % (
    'installed' if 'django.contrib.sites' in settings.INSTALLED_APPS else 'uninstalled',
    settings.SITE_ID, getattr(settings, 'SITE_MULTI', None)), flush=True)
print('REGISTRY_STARTUP_ERRORS=%d' % len(cap.records), flush=True)
for name, lvl, msg, exc in cap.records:
    print('REGISTRY_STARTUP_ERROR_LOG logger=%s level=%s msg=%s' % (name, lvl, msg), flush=True)
    if exc:
        print(exc, flush=True)
try:
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader
    loader = MigrationLoader(connection)
    loader.build_graph()
    edge = (('aichat', '0042_memory_consent') in loader.graph.nodes
            and ('assets', '0015_alter_machineanomaly_work_order') in loader.graph.nodes)
    print('REGISTRY_GRAPH=ok nodes=%d edge=%s' % (len(loader.graph.nodes), edge), flush=True)
    failed = failed or not edge
except Exception:
    failed = True
    print('REGISTRY_GRAPH=error', flush=True)
    traceback.print_exc()
try:
    from rest_framework.authtoken.models import Token
    from users.models import ApiToken
    token_ok = ('rest_framework.authtoken' not in settings.INSTALLED_APPS
                and Token._meta.abstract and not ApiToken._meta.abstract)
    print('REGISTRY_TOKEN_CONTROL=%s' % ('ok' if token_ok else 'failed'), flush=True)
    failed = failed or not token_ok
except Exception:
    failed = True
    print('REGISTRY_TOKEN_CONTROL=error', flush=True)
    traceback.print_exc()
print('REGISTRY_URL_GATE=%s' % ('failed' if failed else 'ok'), flush=True)
raise SystemExit(3 if failed else 0)
"""


class TestCleanStartupRootUrl:
    """Durable clean-startup / root-URL regression for the isolated fixture.

    A genuinely fresh interpreter (own process, own empty disposable SQLite
    created by ``ai.core.tests.settings``) must import the real
    ``InvenTree.urls`` root URLconf with zero captured errors, on the
    fixture's native conditional Site registry semantics (``SITE_MULTI`` is
    False: the sites app is not installed and ``SITE_ID`` is ``None``, the
    production conditional at ``InvenTree/settings.py`` lines 800-807) and
    its explicit owned disposable native-config environment for direct
    production-settings imports (``users.api`` imports ``InvenTree.settings``
    directly). Error inspection covers both the std-logging error records and
    the raw captured stream (structlog-rendered events included); the child's
    exit code alone is never trusted as proof of clean startup.
    """

    def test_fresh_interpreter_startup_and_root_url_are_clean(self, tmp_path):
        import ai.core.tests.settings as fixture_settings

        package_root = str(Path(fixture_settings.__file__).resolve().parents[3])
        env = dict(os.environ)
        # Mirror the parent interpreter's real import path exactly (the
        # offline carrier exposes its dependency set through sys.path), so
        # the child is a genuinely fresh interpreter on the same environment.
        env["PYTHONPATH"] = os.pathsep.join(
            [package_root] + [entry or str(Path.cwd()) for entry in sys.path if entry is not None]
        )
        env["DJANGO_SETTINGS_MODULE"] = "ai.core.tests.settings"
        env["HOME"] = str(tmp_path)
        env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
        proc = subprocess.run(
            [sys.executable, "-c", STARTUP_CHILD],
            capture_output=True,
            text=True,
            env=env,
            cwd=package_root,
            timeout=100,
        )
        out = proc.stdout + "\n" + proc.stderr
        assert proc.returncode == 0, out
        assert "REGISTRY_URL_GATE=ok" in out, out
        assert "REGISTRY_URL_IMPORT=status=ok" in out, out
        assert "REGISTRY_STARTUP_ERRORS=0" in out, out
        assert "REGISTRY_SITE_CONTRACT=sites_uninstalled site_id=None site_multi=False" in out, out
        assert "REGISTRY_TOKEN_CONTROL=ok" in out, out
        assert "REGISTRY_GRAPH=ok" in out and "edge=True" in out, out
        markers = [
            line
            for line in out.splitlines()
            if re.search(
                r"\[(?:error|critical)\s*\]|level\s*=\s*(?:error|critical)\b"
                r'|"level":\s*"(?:error|critical)"',
                re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line),
                re.IGNORECASE,
            )
        ]
        assert markers == [], out

    @pytest.mark.parametrize(
        "error_line",
        [
            "[error    ] swallowed plugin startup failure",
            "\x1b[31m[error    ]\x1b[0m swallowed plugin startup failure",
            "[critical ] swallowed plugin startup failure",
            "level=error swallowed plugin startup failure",
            '{"level": "error", "event": "swallowed plugin startup failure"}',
        ],
    )
    def test_rejects_swallowed_startup_errors(self, tmp_path, monkeypatch, error_line):
        """A zero-exit child and success markers cannot hide an error event."""
        output = (
            "REGISTRY_URL_GATE=ok\n"
            "REGISTRY_URL_IMPORT=status=ok patterns=17\n"
            "REGISTRY_STARTUP_ERRORS=0\n"
            "REGISTRY_SITE_CONTRACT=sites_uninstalled site_id=None site_multi=False\n"
            "REGISTRY_TOKEN_CONTROL=ok\n"
            "REGISTRY_GRAPH=ok edge=True\n"
        )
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr=error_line)
        monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: result)
        with pytest.raises(AssertionError):
            self.test_fresh_interpreter_startup_and_root_url_are_clean(tmp_path)

    @pytest.mark.parametrize(
        "other_line", ["", "[warning ] expected warning", '"level": "warning"']
    )
    def test_retains_clean_startup_control(self, tmp_path, monkeypatch, other_line):
        """Clean startup and non-error diagnostics remain accepted."""
        output = (
            "REGISTRY_URL_GATE=ok\n"
            "REGISTRY_URL_IMPORT=status=ok patterns=17\n"
            "REGISTRY_STARTUP_ERRORS=0\n"
            "REGISTRY_SITE_CONTRACT=sites_uninstalled site_id=None site_multi=False\n"
            "REGISTRY_TOKEN_CONTROL=ok\n"
            "REGISTRY_GRAPH=ok edge=True\n"
        )
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr=other_line)
        monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: result)
        self.test_fresh_interpreter_startup_and_root_url_are_clean(tmp_path)
