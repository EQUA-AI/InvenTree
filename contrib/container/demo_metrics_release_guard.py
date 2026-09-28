"""Pure guard/allowlist logic for the demo-metrics release-image harness.

Harness-only (never used by any deployment, never shipped in the production
image). Stdlib-only and offline: no Django import, no network, no database.
Shared by ``demo_metrics_release_image`` (CLI), the release probe settings shim
(``demo_metrics_release_settings``) and the focused offline tests
(``test_demo_metrics_release_guard.py``).

Safety properties enforced here:

* The release probe database is an EXACT dedicated disposable name
  (``inventree_dm_release_probe_v1``) — never the default/shared ``inventree``
  database, never another probe's database, never a cloud host.
* The frozen build context is an explicit allowlist: git-tracked build inputs
  plus explicitly named new demo source/harness files. Credentials, local
  state, ``IDEA.md``, ``LocalDocs`` and caches are denied by rule, and any
  denylist hit inside the allowlist FAILS CLOSED (the freeze refuses to run).
* The candidate is labeled ``UNCOMMITTED LOCAL CANDIDATE`` and the commit hash
  build argument is deliberately EMPTY — HEAD is never passed off as the commit
  of uncommitted code.
"""

from __future__ import annotations

import hashlib
import re
import subprocess

#: Honest candidate label. This tree is NOT a reviewed/approved release.
CANDIDATE_LABEL = 'UNCOMMITTED LOCAL CANDIDATE (not an approved release)'
MANIFEST_SCHEMA = 'equa.demo-metrics.release-candidate-input-manifest/1'

#: Deliberately empty: the demo implementation is uncommitted, so no commit
#: hash may be passed to the image build. The frontend build consequently
#: self-reports ``{"commit": null, "dirty": true}``.
COMMIT_HASH_BUILD_ARG = ''
COMMIT_TAG_BUILD_ARG = 'uncommitted-local-candidate'

#: Image tag for the local, unpublished candidate.
IMAGE_TAG = 'inventree-demo-metrics-candidate:uncommitted-local'

#: The ONLY database the release probe may touch: dedicated, disposable,
#: exact-name guarded, kept (``keepdb``) and never dropped.
RELEASE_PROBE_DB_NAME = 'inventree_dm_release_probe_v1'
RELEASE_PROBE_DB_HOST = 'inventree_dm_release_probe_db'
RELEASE_PROBE_DB_USER = 'postgres'
#: SELECT-only role used for the read-only probe window.
RELEASE_PROBE_RO_ROLE = 'dm_release_ro'

#: Repository-relative paths of the git-tracked files the production Dockerfile
#: copies (build inputs), beyond everything tracked under ``src/``.
NAMED_TRACKED_BUILD_INPUTS = (
    'contrib/container/Dockerfile',
    'contrib/container/requirements.txt',
    'contrib/container/gunicorn.conf.py',
    'contrib/container/init.sh',
    'contrib/container/gcp-entra-token.sh',
    'tasks.py',
    'pyproject.toml',
)

#: Explicitly named new (untracked) demo source/harness files that join the
#: frozen context. Everything else untracked stays OUT. E2E run artifacts
#: (``demo-metrics-e2e-results/``) are local state and are deliberately absent.
NAMED_NEW_FILES = (
    # demo-metrics backend source (enter the image via COPY src)
    'src/backend/InvenTree/assets/demo_metrics/__init__.py',
    'src/backend/InvenTree/assets/demo_metrics/apply_service.py',
    'src/backend/InvenTree/assets/demo_metrics/cleanup.py',
    'src/backend/InvenTree/assets/demo_metrics/cli.py',
    'src/backend/InvenTree/assets/demo_metrics/cohort.py',
    'src/backend/InvenTree/assets/demo_metrics/contract.py',
    'src/backend/InvenTree/assets/demo_metrics/demo_test_support.py',
    'src/backend/InvenTree/assets/demo_metrics/effects.py',
    'src/backend/InvenTree/assets/demo_metrics/fingerprint.py',
    'src/backend/InvenTree/assets/demo_metrics/history.py',
    'src/backend/InvenTree/assets/demo_metrics/planner.py',
    'src/backend/InvenTree/assets/demo_metrics/reference.py',
    'src/backend/InvenTree/assets/demo_metrics/replay.py',
    'src/backend/InvenTree/assets/demo_metrics/resources/demo_fixture.json',
    'src/backend/InvenTree/assets/demo_metrics/resources/expected_metrics.json',
    'src/backend/InvenTree/assets/demo_metrics/resources/target_mapping.example.json',
    'src/backend/InvenTree/assets/demo_metrics_api.py',
    'src/backend/InvenTree/assets/demo_metrics_models.py',
    'src/backend/InvenTree/assets/management/commands/apply_demo_metrics.py',
    'src/backend/InvenTree/assets/management/commands/cleanup_demo_metrics.py',
    'src/backend/InvenTree/assets/management/commands/plan_demo_metrics.py',
    'src/backend/InvenTree/assets/management/commands/replay_demo_metrics.py',
    'src/backend/InvenTree/assets/management/commands/stop_demo_metrics.py',
    'src/backend/InvenTree/assets/management/commands/verify_demo_metrics.py',
    'src/backend/InvenTree/assets/migrations/0017_demometricscoverageinterval_demometricssession_and_more.py',
    'src/backend/InvenTree/assets/migrations/0018_alter_demometricsreceipt_operation_kind.py',
    'src/backend/InvenTree/assets/test_demo_metrics_apply.py',
    'src/backend/InvenTree/assets/test_demo_metrics_authorization.py',
    'src/backend/InvenTree/assets/test_demo_metrics_cohort_api.py',
    'src/backend/InvenTree/assets/test_demo_metrics_contract.py',
    'src/backend/InvenTree/assets/test_demo_metrics_history_cleanup.py',
    'src/backend/InvenTree/assets/test_demo_metrics_lifecycle.py',
    'src/backend/InvenTree/assets/test_demo_metrics_models.py',
    'src/backend/InvenTree/assets/test_demo_metrics_notifications.py',
    'src/backend/InvenTree/assets/test_demo_metrics_reconcile.py',
    # demo-metrics frontend source (compiled by the image's builder stage)
    'src/frontend/src/pages/assets/locations/DemoMetricsPanel.tsx',
    'src/frontend/src/pages/assets/locations/demoMetrics.test.ts',
    'src/frontend/src/pages/assets/locations/demoMetrics.ts',
    'src/frontend/tests/pages/pui_demo_metrics.spec.ts',
    'src/frontend/playwright.demo-metrics.config.ts',
    'src/frontend/playwright/demo-metrics-lingui.ts',
    'src/frontend/playwright/demo-metrics.html',
    'src/frontend/playwright/demo-metrics.tsx',
    'src/frontend/playwright/tests/playwright.demo-metrics-e2e.config.ts',
    'src/frontend/playwright/tests/pui_demo_metrics_e2e.spec.ts',
    # demo-metrics harness (host-side; never COPY'd into the image)
    'contrib/container/bootstrap_probe/harness_validators.py',
    'contrib/container/bootstrap_probe/net_guard.py',
    'contrib/container/bootstrap_probe/sitecustomize.py',
    'contrib/container/bootstrap_probe/test_harness_validators.py',
    'contrib/container/demo-metrics-bootstrap-probe.sh',
    'contrib/container/demo-metrics-e2e.sh',
    'contrib/container/demo-metrics-job-input.example.json',
    'contrib/container/demo-metrics-job-spec-tests.py',
    'contrib/container/demo-metrics-job-spec.py',
    'contrib/container/demo-metrics-runbook.md',
    'contrib/container/demo-metrics-session-regression-tests.py',
    'contrib/container/demo_metrics_bootstrap_fingerprint.py',
    'contrib/container/demo_metrics_bootstrap_negative.py',
    'contrib/container/demo_metrics_bootstrap_settings.py',
    'contrib/container/demo_metrics_bootstrap_setup.py',
    'contrib/container/demo_metrics_e2e_proxy.py',
    'contrib/container/demo_metrics_e2e_session_regression.py',
    'contrib/container/demo_metrics_e2e_settings.py',
    'contrib/container/demo_metrics_e2e_setup.py',
    # release-image harness (this work; host-side)
    'contrib/container/demo_metrics_release_guard.py',
    'contrib/container/demo_metrics_release_image.py',
    'contrib/container/demo_metrics_release_settings.py',
    'contrib/container/demo_metrics_release_setup.py',
    'contrib/container/demo_metrics_release_fingerprint.py',
    'contrib/container/demo_metrics_release_negative.py',
    'contrib/container/demo-metrics-release-image-probe.sh',
    'contrib/container/test_demo_metrics_release_guard.py',
)

#: Denylist: forbidden path segments (anywhere in the path).
DENY_SEGMENTS = frozenset({
    'node_modules',
    '.venv',
    '__pycache__',
    '.git',
    '.idea',
    '.vscode',
    'LocalDocs',
    'LocalTesting',
    'secrets',
    'dev',
})

#: Denylist: forbidden exact file names.
DENY_NAMES = frozenset({'IDEA.md', '.env', 'docker.dev.env', 'docker.dev.env.example'})

#: Denylist: forbidden name patterns (any file type).
_DENY_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r'^\.env(\..*)?$',
        r'.*\.env$',
        r'.*\.pyc$',
        r'.*\.pem$',
        r'.*\.key$',
        r'.*\.p12$',
        r'.*\.pfx$',
        r'.*\.ppk$',
        r'.*\.kdbx$',
        r'^id_(rsa|ed25519|ecdsa)',
    )
)

#: Secret-ish names are denied for DATA/CONFIG file types only — source code
#: that merely handles credentials (e.g. ``credentials.py``) is a build input.
_SECRETISH_NAME = re.compile(
    r'(secret|credential|password|passwd|api[_-]?key|private[_-]?key|token)'
)
_SECRETISH_DATA_SUFFIXES = (
    '',
    '.json',
    '.yaml',
    '.yml',
    '.ini',
    '.cfg',
    '.conf',
    '.txt',
    '.env',
    '.properties',
    '.toml',
    '.xml',
    '.csv',
    '.bak',
    '.zip',
    '.tar',
    '.gz',
)

#: Where a context path lands inside the production image (source/image parity).
_IMAGE_PATH_MAP = {
    'tasks.py': '/home/inventree/tasks.py',
    'src/backend/requirements.txt': '/home/inventree/src/backend/requirements.txt',
    'contrib/container/requirements.txt': '/home/inventree/base_requirements.txt',
    'contrib/container/gunicorn.conf.py': '/home/inventree/gunicorn.conf.py',
    'contrib/container/init.sh': '/home/inventree/init.sh',
    'contrib/container/gcp-entra-token.sh': '/usr/local/bin/gcp-entra-token',
}

#: Server-side statement-log scan patterns (postgres `log_statement=all`).
_WRITE_SHAPED = re.compile(
    r'LOG:\s+(statement|execute [^:]*):\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|COPY)'
)
_WRITE_REFUSED = re.compile(
    r'ERROR:\s+(cannot execute [A-Z]+ in a read-only transaction|permission denied)'
)


class ReleaseGuardError(Exception):
    """Raised when a release-harness guard refuses to proceed."""


# --------------------------------------------------------------------------
# database guards
# --------------------------------------------------------------------------


def check_probe_db_name(name) -> str:
    """Return the name if and only if it is the exact disposable probe DB."""
    name = str(name)
    if name != RELEASE_PROBE_DB_NAME:
        raise ReleaseGuardError(
            f'release probe refuses database {name!r}; want {RELEASE_PROBE_DB_NAME!r}'
        )
    return name


def check_probe_db_host(host) -> str:
    """Return the host if and only if it is the dedicated probe DB host."""
    host = str(host)
    if host != RELEASE_PROBE_DB_HOST:
        raise ReleaseGuardError(
            f'release probe refuses database host {host!r}; '
            f'want {RELEASE_PROBE_DB_HOST!r}'
        )
    return host


# --------------------------------------------------------------------------
# frozen build-context allowlist
# --------------------------------------------------------------------------


def deny_reason(relpath: str) -> str | None:
    """Return a human reason if ``relpath`` is denied, else ``None``."""
    relpath = relpath.replace('\\', '/')
    while relpath.startswith('./'):
        relpath = relpath[2:]
    parts = relpath.split('/')
    name = parts[-1]
    for segment in parts[:-1]:
        if segment in DENY_SEGMENTS:
            return f'forbidden path segment {segment!r}'
    if name in DENY_SEGMENTS:
        return f'forbidden path segment {name!r}'
    if name in DENY_NAMES:
        return f'forbidden file name {name!r}'
    if name.startswith('.env') and name.endswith(('.template', '.example')):
        # Tracked placeholder templates (values like "your-api-key") are build
        # inputs, not credentials; verified placeholders only.
        return None
    for pattern in _DENY_PATTERNS:
        if pattern.match(name):
            return f'forbidden file name pattern {pattern.pattern!r}'
    import os as _os

    suffix = _os.path.splitext(name)[1]
    if _SECRETISH_NAME.search(name) and suffix in _SECRETISH_DATA_SUFFIXES:
        return f'secret-ish data/config file name {name!r}'
    return None


def plan_context_files(repo_root, tracked_files=None, named_files=None) -> dict:
    """Compute the frozen-context allowlist for ``repo_root``.

    Returns ``{relative_path: klass}`` where klass is ``tracked`` (git-tracked
    build input, working-tree content) or ``named-new`` (explicitly named new
    demo source/harness file). Fails closed on any denylist hit or missing
    named file.
    """
    import os

    repo_root = os.fspath(repo_root)
    if tracked_files is None:
        out = subprocess.run(
            ['git', 'ls-files', '-z'], cwd=repo_root, capture_output=True, check=True
        )
        tracked_files = [p for p in out.stdout.decode('utf-8').split('\0') if p]
    named = tuple(named_files) if named_files is not None else NAMED_NEW_FILES

    include: dict[str, str] = {}
    for rel in tracked_files:
        rel = rel.replace('\\', '/').strip()
        if not rel:
            continue
        if rel.startswith('src/') or rel in NAMED_TRACKED_BUILD_INPUTS:
            include[rel] = 'tracked'
    for rel in named:
        path = os.path.join(repo_root, rel)
        if not os.path.isfile(path):
            raise ReleaseGuardError(
                f'named demo source/harness file missing from tree: {rel!r} — '
                'refusing to build a partial candidate'
            )
        include[rel.replace('\\', '/')] = 'named-new'

    for rel in include:
        reason = deny_reason(rel)
        if reason:
            raise ReleaseGuardError(
                f'refusing to freeze build context: {rel!r} is denied ({reason})'
            )
    return dict(sorted(include.items()))


# --------------------------------------------------------------------------
# source/image parity
# --------------------------------------------------------------------------


def image_path_for(relpath: str):
    """Return the in-image path for a context path, or None if not in-image.

    The production stage copies only backend sources (``src/backend/``) plus a
    handful of named contrib/container files; frontend source is compiled in
    the builder stage and is NOT present in the production image.
    """
    relpath = relpath.replace('\\', '/')
    if relpath in _IMAGE_PATH_MAP:
        return _IMAGE_PATH_MAP[relpath]
    if relpath == 'src/backend/requirements.txt':
        return '/home/inventree/src/backend/requirements.txt'
    if relpath.startswith(('src/backend/InvenTree/', 'src/backend/tasks/')):
        return f'/home/inventree/{relpath}'
    return None


# --------------------------------------------------------------------------
# server-side statement-log scan
# --------------------------------------------------------------------------


def scan_statement_log(text: str):
    """Return (write_shaped_lines, write_refused_lines) in a statement log."""
    shaped, refused = [], []
    for line in text.splitlines():
        if _WRITE_REFUSED.search(line):
            refused.append(line)
        elif _WRITE_SHAPED.search(line):
            shaped.append(line)
    return shaped, refused


# --------------------------------------------------------------------------
# server statement evidence
# --------------------------------------------------------------------------

#: A real server-side statement-log line (postgres `log_statement=all`).
_SERVER_STATEMENT = re.compile(r'LOG:\s+(statement|execute [^:]*):')

#: Text that means the CAPTURE failed — never server SQL evidence. An empty
#: capture or one of these messages must not pass as "zero write attempts".
_CAPTURE_ERROR = re.compile(
    r'(?i)(error (response )?from daemon|error fetching logs'
    r'|no such (container|object|image))'
)


def require_statement_evidence(text: str) -> int:
    """Fail closed unless ``text`` is a real server statement-log capture.

    Returns the number of server statement/execute lines. An empty capture, a
    docker daemon error message, or startup chatter without SQL statements is
    NOT evidence and must never count as "zero write attempts reached the
    server".
    """
    if not text.strip():
        raise ReleaseGuardError(
            'statement-log capture is empty — no server SQL evidence'
        )
    hit = _CAPTURE_ERROR.search(text)
    if hit:
        raise ReleaseGuardError(
            'statement-log capture is a capture error, not server evidence: '
            + repr(hit.group(0))
        )
    count = sum(1 for _ in _SERVER_STATEMENT.finditer(text))
    if not count:
        raise ReleaseGuardError(
            'statement-log capture contains no server SQL statement lines'
        )
    return count


# --------------------------------------------------------------------------
# write-defense proof classification (negative ORM probe)
# --------------------------------------------------------------------------

#: The ONLY SQLSTATEs that prove the server refused a write: 25006
#: (read_only_sql_transaction) and 42501 (insufficient_privilege). Anything
#: else — a random exception, a connection error, an unrelated database
#: error — is NOT proof of the write defense.
WRITE_DEFENSE_SQLSTATES = frozenset({'25006', '42501'})

#: Server statement-log refusal text that must MATCH each proof SQLSTATE. The
#: separately captured server statement log is the evidence; the client-side
#: exception is only the pointer to it.
WRITE_DEFENSE_REFUSAL_PATTERNS = {
    '25006': r'ERROR:\s+cannot execute [A-Z]+ in a read-only transaction',
    '42501': r'ERROR:\s+permission denied',
}


def find_write_defense_sqlstate(exc):
    """Return the write-defense SQLSTATE in ``exc``'s chained exception graph.

    Walks ``__cause__`` and ``__context__`` transitively and honors both
    psycopg's ``sqlstate`` and psycopg2's ``pgcode`` attributes. Returns
    ``None`` when nothing in the chain is a server read-only/privilege
    refusal.
    """
    seen: dict = {}
    pending = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen[id(current)] = current
        for attr in ('sqlstate', 'pgcode'):
            code = getattr(current, attr, None)
            if code is not None and str(code) in WRITE_DEFENSE_SQLSTATES:
                return str(code)
        pending.append(getattr(current, '__cause__', None))
        pending.append(getattr(current, '__context__', None))
    return None


def require_write_defense_refusal(exc) -> str:
    """Return the refusal SQLSTATE proven by ``exc``'s chain, or fail closed.

    Only a server read-only/privilege refusal (SQLSTATE 25006 or 42501)
    anywhere in the chained exception graph counts as proof that the
    server-side write defense rejected the attempt.
    """
    code = find_write_defense_sqlstate(exc)
    if code is None:
        raise ReleaseGuardError(
            'caught exception is NOT a server read-only/privilege refusal; '
            'write-defense proof requires SQLSTATE '
            + ' or '.join(sorted(WRITE_DEFENSE_SQLSTATES))
            + f' in the chained exception, got {type(exc).__name__}: {exc!r}'
        )
    return code


def write_refusal_pattern_for(sqlstate: str) -> str:
    """Return the server-log refusal regex matching a write-defense SQLSTATE.

    The orchestrator validates the matching server-side refusal SEPARATELY in
    the captured server statement log using this pattern.
    """
    code = str(sqlstate)
    if code not in WRITE_DEFENSE_SQLSTATES:
        raise ReleaseGuardError(
            f'{code!r} is not a write-defense SQLSTATE; refusing to treat it '
            'as proof of the server-side write defense'
        )
    return WRITE_DEFENSE_REFUSAL_PATTERNS[code]


# --------------------------------------------------------------------------
# row-count fingerprints
# --------------------------------------------------------------------------


def compare_fingerprints(before: dict, after: dict, expect_user: str) -> dict:
    """Fail closed unless before/after fingerprints are identical.

    Both fingerprints must be read-only and run as ``expect_user``. Returns a
    small summary dict.
    """
    diffs = {
        t: (before['tables'].get(t), after['tables'].get(t))
        for t in sorted(set(before['tables']) | set(after['tables']))
        if before['tables'].get(t) != after['tables'].get(t)
    }
    if diffs:
        raise ReleaseGuardError(
            f'row-count differences across the read-only window: {diffs}'
        )
    for tag, fp in (('before', before), ('after', after)):
        if fp.get('user') != expect_user:
            raise ReleaseGuardError(
                f'fingerprint {tag} ran as {fp.get("user")!r}, want {expect_user!r}'
            )
        if fp.get('default_transaction_read_only') != 'on':
            raise ReleaseGuardError(
                f'fingerprint {tag}: default_transaction_read_only was '
                f'{fp.get("default_transaction_read_only")!r}, want "on"'
            )
    return {
        'tables': len(before['tables']),
        'total_rows': sum(before['tables'].values()),
        'user': expect_user,
    }


# --------------------------------------------------------------------------
# input manifest
# --------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    """Return the hex sha256 digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def file_sha256(path) -> str:
    """Return the hex sha256 digest of the file at ``path``."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(files: dict) -> str:
    """Canonical digest over the sorted ``path sha256`` listing."""
    listing = ''.join(f'{path} {files[path]["sha256"]}\n' for path in sorted(files))
    return sha256_text(listing)


def build_manifest(
    include: dict,
    *,
    repo_root,
    head_commit,
    git_dirty_tracked,
    created_utc: str | None = None,
) -> dict:
    """Build the input-sha manifest for the frozen context."""
    import os

    if created_utc is None:
        import datetime

        created_utc = datetime.datetime.now(datetime.timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ'
        )

    files = {}
    dirty = set(git_dirty_tracked or ())
    for rel, klass in include.items():
        path = os.path.join(repo_root, rel)
        files[rel] = {
            'sha256': file_sha256(path),
            'class': klass,
            'git_status': (
                'tracked-modified'
                if rel in dirty
                else ('tracked-clean' if klass == 'tracked' else 'untracked-new')
            ),
        }
    manifest = {
        'schema': MANIFEST_SCHEMA,
        'candidate_status': CANDIDATE_LABEL,
        'approved': False,
        'commit_hash_build_arg': COMMIT_HASH_BUILD_ARG,
        'commit_tag_build_arg': COMMIT_TAG_BUILD_ARG,
        'head_commit_reference': (
            f'{head_commit} (REFERENCE ONLY — NOT the commit of this candidate; '
            'the demo implementation is uncommitted local work)'
        ),
        'created_utc': created_utc,
        'git_dirty_tracked_files': sorted(dirty),
        'files': files,
        'file_count': len(files),
    }
    manifest['files_digest'] = manifest_digest(files)
    return manifest
