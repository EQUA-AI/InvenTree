#!/usr/bin/env python3
"""Reproducible offline local runner for the AIMMS MAF ai/core pytest scope.

Stdlib-only host driver plus Docker. It runs the pinned baseline environment
(exact image ID, named setup volume, baseline interpreter) with no network, a
read-only root, dropped capabilities and three mounts only: the migration
worktree at ``/repo:ro``, the named ``/scratch:ro`` dependency volume and an
ephemeral ``/deps`` tmpfs holding a root-prepared, read-only-to-tests copy of
the image's installed package bytes.

All orchestration truth lives on the host. The container's PID1 is a minimal
root-owned keeper (environment scrub, then ``signal.pause``) with no
application imports. The host drives three separate real subprocess phases
through ``docker exec`` and records every phase return code, raw log and
collection record on the host before any later candidate execution runs:

* ``prepare`` (root): snapshot installed dependency bytes into ``/deps``
  and create the per-run root-owned writable test-data directories;
* ``collect`` (UID 65534): ``python -m pytest --collect-only``;
* ``execute`` (UID 65534): ``python -m pytest`` with junit output.

Pytest always runs as ``--user 65534:65534`` under a scrubbed child wrapper
that launches ``python -m pytest`` as a subprocess. Each ``docker exec``
return code is the authoritative phase exit; container-generated metadata
(control frames, report files, outcomes) is never read back as truth. The
only container artifact ever copied out is the allowlisted ``junit.xml``,
imported while the container is alive as test-produced evidence,
structurally validated with ``ElementTree`` (supported root, well-formed
XML, coherent outcome records) and reconciled case-by-case against the
recorded collection nodes, and never allowed to override an actual exit
code.

Ownership is explicit: the container is created under a unique
``--name aimms-<run_id>`` with a run-scoped ownership label. Literal format
validation of the creation token is preserved; a malformed token is recorded
as an error and never passed to rm/inspect. Cleanup resolves the ID from an
owned exact-name inspect and removes only a container whose returned ID,
name and label all match. The owned container ID is pinned once (the
creation token when valid, otherwise the first owned exact-name inspect)
and every later start/exec/report/cleanup command targets that pinned ID;
a final inspect whose ID, name or label differs from the pinned identity is
refused and never removed.

Trust labels are explicit: authoritative host-recorded transport and phase
statuses are kept distinct from candidate-produced collection and junit
observations. Host-owned capture and storage do not authenticate candidate
stdout; node/execution consistency is checked and recorded while
``authenticated`` stays false under a trusted, reviewed test-code
assumption. This runner is not a cryptographic proof against arbitrary test
code that monkeypatches pytest and coherently forges both observations;
cross-phase node/execution reconciliation rejects observed one-channel
forgeries and reports honest provenance instead.

Reports are written to a brand-new external output directory that this
invocation itself creates atomically; a directory that appears after
validation is never adopted, deleted or written to. Full
discovery/collection/outcome accounting is recorded; the run fails on
missing reports, unexplained source omissions, unexpectedly empty
collection, failed pytest phases, phase/junit inconsistency or a lost
sandbox.

Usage:
    python3 contrib/aimms_harness/run_tests.py --repo <worktree> --output <newdir>
    optional: [--collect-only] [--test SEL ...]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_IMAGE = (
    'sha256:b6ce427887301a8d10058e59d55ee99f0e01f52ce4ef7e3e250687afa557fc07'
)
DEFAULT_VOLUME = 'aimms-maf-harness-setup-fe1e6a7f'
DEFAULT_INTERPRETER = '/scratch/baseline-venv/bin/python'
DOCKER_CONTEXT = 'default'
CONTAINER_REPO = '/repo'
CONTAINER_SCRATCH = '/scratch'
CONTAINER_RUNTIME = '/runs'
CONTAINER_DEPS = '/deps'
DEPS_SITE = '/deps/image-site'
IMAGE_SITE_SOURCE = '/root/.local/lib/python3.14/site-packages'
TEST_USER = '65534:65534'
OWNERSHIP_LABEL = 'aimms.maf.harness.run'
TESTS_SUBPATH = Path('src/backend/InvenTree/ai/core/tests')
PYTEST_CONFIG = '/repo/src/backend/InvenTree/ai/pyproject.toml'
CONTAINER_TESTS = '/repo/src/backend/InvenTree/ai/core/tests'
SOURCE_PYTHONPATH = '/repo/src/backend/InvenTree:/repo/src/backend'

# The only container-produced artifact ever copied out. Everything else the
# container could write (databases, env, config, control frames, outcome
# files) is forgeable test-side output and is never imported as truth.
REPORT_ALLOWLIST = ('junit.xml',)
JUNIT_NAME = 'junit.xml'

NODE_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
CONTAINER_ID = re.compile(r'^[0-9a-f]{64}$')

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_TRANSPORT = 3

JUNIT_NOTE = (
    'normal pytest node totals and junit subtest rows are not equal by '
    'definition; both are recorded separately and never equated'
)


class UsageError(Exception):
    """Invalid user input rejected before any docker call."""


class _StopError(Exception):
    """Internal: abort the docker stage while keeping collected state."""


def run_cmd(argv, timeout=None):
    """Run one bounded command; the only subprocess boundary of this driver."""
    return subprocess.run(
        [str(a) for a in argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def container_name_for(run_id):
    """Return the explicit owned container name for one run identifier."""
    return f'aimms-{run_id}'


def validate_repo(raw):
    """Validate that the path is a migration worktree with an ai/core test tree."""
    repo = Path(raw)
    if not repo.is_absolute():
        raise UsageError(f'repo path must be absolute: {raw!r}')
    if not repo.is_dir():
        raise UsageError(f'repo path is not a migration worktree directory: {raw}')
    if not (repo / TESTS_SUBPATH).is_dir():
        raise UsageError(
            f'repo path is missing {TESTS_SUBPATH} (not a migration worktree)'
        )
    return repo


def _has_symlink_component(path):
    for part in [path, *path.parents]:
        if part.is_symlink():
            return part
    return None


def validate_output_dir(raw, repo):
    """Reject anything but a brand-new external non-symlinked directory."""
    out = Path(raw)
    if not out.is_absolute():
        raise UsageError(f'output path must be absolute: {raw!r}')
    if out.exists() or out.is_symlink():
        raise UsageError(f'output path must not already exist: {raw}')
    bad = _has_symlink_component(out)
    if bad is not None:
        raise UsageError(f'output path has symlinked component: {bad}')
    repo_res = repo.resolve()
    out_res = out.resolve()
    if out_res == repo_res or repo_res in out_res.parents:
        raise UsageError(
            f'output must be outside the repository (repository-contained '
            f'output rejected): {raw}'
        )
    if not out.parent.is_dir():
        raise UsageError(f'output parent directory does not exist: {out.parent}')
    return out


def validate_selections(selectors, repo):
    """Validate --test selectors literally; no normalisation, no guessing.

    Allowed: an existing non-symlink ``.py`` source path relative to
    ``ai/core/tests``, optionally followed by ``::ident`` node parts.
    """
    tests_dir = (repo / TESTS_SUBPATH).resolve()
    accepted = []
    for sel in selectors:
        reason = _selection_problem(sel, tests_dir)
        if reason is not None:
            raise UsageError(f'invalid selector {sel!r}: {reason}')
        accepted.append(sel)
    return accepted


def _selection_problem(sel, tests_dir):
    if not sel or sel != sel.strip() or '\\' in sel:
        return 'selector must be a non-empty literal path'
    head, sep, rest = sel.partition('::')
    if head.startswith(('/', '~')):
        return 'selector must be relative to ai/core/tests'
    parts = head.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return 'selector must not contain empty, "." or ".." path parts'
    if not head.endswith('.py'):
        return 'selector source must be a .py file'
    cursor = tests_dir
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return f'symlinked source not allowed: {cursor}'
    candidate = tests_dir.joinpath(*parts)
    if not candidate.is_file():
        return 'selector source does not exist inside ai/core/tests'
    resolved = candidate.resolve()
    if tests_dir not in resolved.parents:
        return 'selector source escapes ai/core/tests'
    if sep:
        node_parts = rest.split('::')
        if any(not NODE_IDENT.match(part) for part in node_parts):
            return 'node selector parts must match [A-Za-z_][A-Za-z0-9_]*'
    return None


def discover_test_sources(repo):
    """Dynamically discover every real test source under ai/core/tests."""
    tests_dir = repo / TESTS_SUBPATH
    found = []
    for path in sorted(tests_dir.rglob('test_*.py')):
        if path.is_symlink() or not path.is_file():
            continue
        found.append(path.relative_to(tests_dir).as_posix())
    return sorted(found)


def count_test_callables(path):
    """Conservative count under this scope's pytest naming; never imports.

    Plain non-test classes and nested helpers are not pytest entry points.
    Classes with bases remain candidates regardless of name, so unittest
    collection or an unknown inherited test contract cannot be hidden.
    """
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError):
        return -1
    total = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith('test_'):
                total += 1
        elif isinstance(node, ast.ClassDef) and (
            node.name.startswith('Test') or node.bases
        ):
            total += sum(
                isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                and method.name.startswith('test')
                for method in node.body
            )
    return total


def build_keeper():
    """PID1 script: root-owned env scrub and pause, no application imports."""
    return """import os
import signal

os.environ.clear()
os.environ['PATH'] = (
    '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
)
signal.pause()
"""


def build_prepare_script(run_dir):
    """Root preparation script: dependency snapshot and owned run layout.

    Copies only the installed package tree (never HOME, config or secret
    material), normalises copied bytes to root-owned 755/644 so UID 65534
    can read but never write them, then creates the per-run root-owned
    writable test-data directories and the junit sink.
    """
    template = """import os
import shutil

os.environ.clear()
os.environ['PATH'] = (
    '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
)
SOURCE = __SOURCE__
DEST = __DEST__
shutil.copytree(
    SOURCE,
    DEST,
    ignore=shutil.ignore_patterns('__pycache__'),
    dirs_exist_ok=True,
)
for _root, _dirs, _files in os.walk(DEST):
    os.chmod(_root, 0o755)
    for _name in _dirs:
        os.chmod(os.path.join(_root, _name), 0o755)
    for _name in _files:
        os.chmod(os.path.join(_root, _name), 0o644)
RUN = __RUN__
os.makedirs(RUN, exist_ok=True)
os.chmod(RUN, 0o755)
for _name in (
    'home', 'tmp', 'media', 'cache', 'backup', 'static', 'db',
    '.pytest_cache',
):
    _path = RUN + '/' + _name
    os.makedirs(_path, exist_ok=True)
    os.chmod(_path, 0o777)
REPORTS = RUN + '/reports'
os.makedirs(REPORTS, exist_ok=True)
os.chmod(REPORTS, 0o755)
with open(RUN + '/inventree_config.yaml', 'w') as _fh:
    _fh.write('# synthetic disposable AIMMS harness config (owned, empty)\\n')
os.chmod(RUN + '/inventree_config.yaml', 0o644)
with open(REPORTS + '/junit.xml', 'w') as _fh:
    _fh.write('')
os.chmod(REPORTS + '/junit.xml', 0o666)
print('AIMMS_PREPARE_V1 ok')
"""
    values = {
        '__SOURCE__': json.dumps(IMAGE_SITE_SOURCE),
        '__DEST__': json.dumps(DEPS_SITE),
        '__RUN__': json.dumps(run_dir),
    }
    script = template
    for token, value in values.items():
        script = script.replace(token, value)
    return script


def build_child_wrapper(run_dir, interpreter, config, targets, phase, junit_path=''):
    """Scrubbed child wrapper: run one pytest phase as a subprocess.

    The inherited environment is cleared before anything else. Dependency
    roots come from the pinned interpreter's own readable site-packages plus
    the root-prepared ``/deps/image-site`` snapshot; private ``/root`` paths
    are never carried. pytest runs as ``python -m pytest`` under the
    unprivileged test user; never embedded, never as keeper root.
    """
    if phase == 'collect':
        extra = ['--collect-only', '-q']
    else:
        extra = ['--junitxml=' + junit_path]
    template = """import os
os.environ.clear()
import subprocess, sys

DEPENDENCY_ROOTS = [
    _p for _p in sys.path
    if _p and _p.endswith('/site-packages') and os.path.isdir(_p)
    and not _p.startswith('/root')
]
RUN = __RUN__
PY = __PY__
CONFIG = __CONFIG__
TARGETS = __TARGETS__

os.environ.update({
    'HOME': __HOME__,
    'TMPDIR': __TMPDIR__,
    'XDG_CACHE_HOME': __CACHE__,
    'DJANGO_SETTINGS_MODULE': 'ai.core.tests.settings',
    'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
    'PYTHONDONTWRITEBYTECODE': '1',
    'AIMMS_AZURE_INTEGRATION': '0',
    'AIMMS_GOLDEN_LIVE': '0',
    'PATH': os.path.dirname(PY) + ':/usr/local/bin:/usr/bin:/bin',
    'PYTHONPATH': os.pathsep.join([
        '/repo/src/backend/InvenTree:/repo/src/backend',
        '/deps/image-site',
        *DEPENDENCY_ROOTS,
    ]),
    'INVENTREE_CONFIG_FILE': __CONFIGFILE__,
    'INVENTREE_MEDIA_ROOT': __MEDIA__,
    'INVENTREE_BACKUP_DIR': __BACKUP__,
    'PYTEST_ADDOPTS': '',
    'PYTHONNOUSERSITE': '1',
})
os.chdir(RUN)

command = [
    PY, '-m', 'pytest', '-c', CONFIG, '-p', 'pytest_asyncio.plugin',
    '-o', 'cache_dir=' + RUN + '/.pytest_cache',
    '-o', 'asyncio_mode=auto',
    '-o', 'addopts=--tb=short --strict-markers',
] + __EXTRA__ + TARGETS
raise SystemExit(subprocess.run(command, env=dict(os.environ)).returncode)
"""
    values = {
        '__RUN__': json.dumps(run_dir),
        '__PY__': json.dumps(interpreter),
        '__CONFIG__': json.dumps(config),
        '__TARGETS__': json.dumps(list(targets)),
        '__EXTRA__': repr(extra),
        '__HOME__': json.dumps(run_dir + '/home'),
        '__TMPDIR__': json.dumps(run_dir + '/tmp'),
        '__CACHE__': json.dumps(run_dir + '/cache'),
        '__CONFIGFILE__': json.dumps(run_dir + '/inventree_config.yaml'),
        '__MEDIA__': json.dumps(run_dir + '/media'),
        '__BACKUP__': json.dumps(run_dir + '/backup'),
    }
    wrapper = template
    for token, value in values.items():
        wrapper = wrapper.replace(token, value)
    return wrapper


def build_create_argv(repo, image, volume, run_id, keeper, interpreter=None):
    """Exact container command: pinned image, named owner, hardened."""
    if interpreter is None:
        interpreter = DEFAULT_INTERPRETER
    name = container_name_for(run_id)
    return [
        'docker',
        '--context',
        DOCKER_CONTEXT,
        'create',
        '--pull=never',
        '--network',
        'none',
        '--read-only',
        '--cap-drop',
        'ALL',
        '--security-opt',
        'no-new-privileges',
        '--name',
        name,
        '--label',
        f'{OWNERSHIP_LABEL}={run_id}',
        '--env',
        'PYTHONDONTWRITEBYTECODE=1',
        '--workdir',
        CONTAINER_RUNTIME,
        '-v',
        f'{repo.resolve()}:{CONTAINER_REPO}:ro',
        '-v',
        f'{volume}:{CONTAINER_SCRATCH}:ro',
        '--tmpfs',
        f'{CONTAINER_RUNTIME}:rw,noexec,nosuid,nodev,size=536870912',
        '--tmpfs',
        f'{CONTAINER_DEPS}:rw,exec,nosuid,nodev,size=1073741824',
        '--entrypoint',
        interpreter,
        image,
        '-c',
        keeper,
    ]


def build_exec_argv(name, interpreter, script, phase, user=TEST_USER):
    """One bounded docker exec of a single host-supplied phase script."""
    return [
        'docker',
        '--context',
        DOCKER_CONTEXT,
        'exec',
        '--user',
        user,
        '--workdir',
        CONTAINER_RUNTIME,
        name,
        interpreter,
        '-c',
        script,
    ]


def build_junit_reader(junit_path):
    """Bounded reader for the single allowlisted container artifact.

    Not every docker daemon can archive tmpfs-backed paths with docker cp,
    so the junit candidate artifact is imported while the container is
    alive through this host-supplied root read. The bytes remain
    test-produced evidence only; host-recorded phase return codes stay the
    sole authority over status and collection.
    """
    template = """# aimms-junit-reader-v1
import sys
sys.stdout.write(open(__PATH__, encoding='utf-8').read())
"""
    return template.replace('__PATH__', json.dumps(junit_path))


def verify_container(doc, volume):
    """Check sandbox posture from inspect output; never touch Config.Env."""
    entry = doc[0] if isinstance(doc, list) and doc else {}
    host = entry.get('HostConfig') or {}
    problems = []
    details = {}
    details['network_mode'] = host.get('NetworkMode')
    if host.get('NetworkMode') != 'none':
        problems.append(f"network mode is {host.get('NetworkMode')!r}, not 'none'")
    details['read_only_rootfs'] = host.get('ReadonlyRootfs')
    if host.get('ReadonlyRootfs') is not True:
        problems.append('root filesystem is not read-only')
    cap_drop = list(host.get('CapDrop') or [])
    details['cap_drop'] = cap_drop
    if 'ALL' not in cap_drop:
        problems.append(f'cap_drop is {cap_drop!r}, not [ALL]')
    sec_opt = list(host.get('SecurityOpt') or [])
    details['security_opt'] = sec_opt
    if not any('no-new-privileges' in str(opt) for opt in sec_opt):
        problems.append(f'security_opt {sec_opt!r} lacks no-new-privileges')
    mounts = []
    seen = {}
    for mount in entry.get('Mounts') or []:
        dest = mount.get('Destination')
        mounts.append({
            'destination': dest,
            'rw': mount.get('RW'),
            'name': mount.get('Name'),
        })
        seen[dest] = mount
    details['mounts'] = mounts
    repo_mount = seen.get(CONTAINER_REPO)
    if not repo_mount or repo_mount.get('RW') is not False:
        problems.append(f'{CONTAINER_REPO} mount missing or not read-only')
    scratch_mount = seen.get(CONTAINER_SCRATCH)
    if not scratch_mount or scratch_mount.get('RW') is not False:
        problems.append(
            f'{CONTAINER_SCRATCH} dependency mount missing or not read-only'
        )
    elif scratch_mount.get('Name') != volume:
        problems.append(
            f'{CONTAINER_SCRATCH} mount uses volume '
            f'{scratch_mount.get("Name")!r}, expected {volume!r}'
        )
    tmpfs = host.get('Tmpfs') or {}
    options = set(str(tmpfs.get(CONTAINER_RUNTIME, '')).split(','))
    details['runtime_tmpfs'] = tmpfs.get(CONTAINER_RUNTIME)
    if not {'rw', 'noexec', 'nosuid', 'nodev', 'size=536870912'} <= options:
        problems.append(f'{CONTAINER_RUNTIME} owned runtime tmpfs missing or unsafe')
    dep_options = set(str(tmpfs.get(CONTAINER_DEPS, '')).split(','))
    details['deps_tmpfs'] = tmpfs.get(CONTAINER_DEPS)
    if not {'rw', 'exec', 'nosuid', 'nodev', 'size=1073741824'} <= dep_options:
        problems.append(f'{CONTAINER_DEPS} dependency snapshot tmpfs missing or unsafe')
    elif 'noexec' in dep_options:
        problems.append(
            f'{CONTAINER_DEPS} dependency tmpfs must permit extension mapping'
        )
    return problems, details


def verify_ownership(doc, name, run_id):
    """Resolve the owned container identity from an exact-name inspect.

    Requires a valid returned ID plus the expected exact name and run-scoped
    ownership label before any deletion target is accepted.
    """
    entry = doc[0] if isinstance(doc, list) and doc else {}
    resolved = str(entry.get('Id') or '')
    doc_name = str(entry.get('Name') or '').lstrip('/')
    labels = (entry.get('Config') or {}).get('Labels') or {}
    problems = []
    if not CONTAINER_ID.match(resolved):
        problems.append(f'inspect returned invalid container id {resolved!r}')
    if doc_name != name:
        problems.append(f'container name {doc_name!r} does not match owned {name!r}')
    if labels.get(OWNERSHIP_LABEL) != run_id:
        problems.append(f'ownership label {OWNERSHIP_LABEL} is missing or mismatched')
    return not problems, problems, resolved


def parse_collect_nodes(text):
    """Parse node identifiers from host-captured collect-phase output."""
    found = set()
    for line in (text or '').splitlines():
        stripped = line.strip()
        if not stripped.startswith((
            '/repo/src/backend/InvenTree/ai/core/tests/',
            'core/tests/',
        )):
            continue
        if '::' not in stripped:
            continue
        if not stripped.split('::', 1)[0].endswith('.py'):
            continue
        found.add(stripped)
    return sorted(found)


SUBTEST_MARKERS = ('(Subtest', '(record')


def node_case_identity(node):
    """Map one recorded node id to its junit ``(classname, name)`` identity.

    The tail is partitioned at the first ``'['`` before any ``'::'``
    splitting and the parameter suffix is reattached verbatim, because
    literal parameter values may themselves contain ``'::'`` (for example
    ``[[::1]]``). Identities are never repaired or normalised.
    """
    head, sep, tail = node.partition('::')
    marker = 'core/tests/'
    if not sep or marker not in head:
        return None
    module = head.split(marker, 1)[1]
    if not module.endswith('.py'):
        return None
    module = module[: -len('.py')].replace('/', '.')
    cut = tail.find('[')
    if cut == -1:
        prefix, params = tail, ''
    else:
        prefix, params = tail[:cut], tail[cut:]
    parts = prefix.split('::')
    callable_name = parts[-1]
    if not callable_name:
        return None
    classname = 'core.tests.' + module
    if parts[:-1]:
        classname += '.' + '.'.join(parts[:-1])
    return classname, callable_name + params


def subtest_parent_name(name):
    """Return the parent case name a subtest row is keyed to."""
    for marker in SUBTEST_MARKERS:
        cut = name.find(marker)
        if cut != -1:
            return name[:cut].rstrip(' ')
    return name


def parse_junit_document(junit_text, expected_identities=()):
    """Structural junit parse with ElementTree; regex is never validity.

    Requires a supported ``testsuite``/``testsuites`` root, well-formed XML
    and coherent testcase outcome records (name and classname attributes,
    and at most one outcome entry per row). Normal, subtest, skipped, error
    and failure rows are counted explicitly; error or skip rows matching no
    recorded node identity are disclosed as collection rows and are never counted as
    executed nodes or equated with node totals.
    """
    counts = {
        'normal': 0,
        'subtest': 0,
        'skipped': 0,
        'error': 0,
        'failure': 0,
        'collection': 0,
    }
    result = {
        'problems': [],
        'counts': counts,
        'normal': [],
        'subtests': [],
        'collection_rows': [],
        'rows': [],
    }
    text = junit_text or ''
    if not text.strip():
        result['problems'] = ['junit report is empty']
        return result
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        result['problems'] = [f'junit report is not well-formed XML: {exc}']
        return result
    problems = []
    if root.tag not in ('testsuite', 'testsuites'):
        problems.append(
            f'unsupported junit root element {root.tag!r}; expected '
            "'testsuite' or 'testsuites'"
        )
    expected = set(expected_identities or ())
    for case in root.iter('testcase'):
        name = case.get('name')
        if name is None:
            problems.append('junit testcase row without a name attribute')
            continue
        classname = case.get('classname')
        if classname is None:
            problems.append(f'junit testcase {name!r} without a classname attribute')
            continue
        outcomes = [
            child.tag for child in case if child.tag in ('failure', 'error', 'skipped')
        ]
        if len(outcomes) > 1:
            problems.append(f'junit testcase {name!r} carries multiple outcome entries')
            continue
        outcome = outcomes[0] if outcomes else 'passed'
        if outcome != 'passed':
            counts[outcome] += 1
        identity = (classname, name)
        row = {
            'classname': classname,
            'name': name,
            'outcome': outcome,
            'text': ' '.join([classname, name, *case.itertext()]),
        }
        if any(marker in name for marker in SUBTEST_MARKERS):
            counts['subtest'] += 1
            row['kind'] = 'subtest'
            result['subtests'].append(identity)
        elif outcome in ('error', 'skipped') and identity not in expected:
            counts['collection'] += 1
            row['kind'] = 'collection'
            result['collection_rows'].append(identity)
        else:
            counts['normal'] += 1
            row['kind'] = 'normal'
            result['normal'].append(identity)
        result['rows'].append(row)
    if not problems and not result['normal']:
        problems.append('junit report records zero executed normal test cases')
    result['problems'] = problems
    return result


def _has_source_path_token(text, source):
    """Match a literal source path, not a substring of a longer path."""
    return (
        re.search(rf"(?:^|[\s'\"(\[,]){re.escape(source)}(?=$|[\s'\"),:\]])", text)
        is not None
    )


def reconcile_node_cases(node_ids, document, collection_sources=()):
    """Fail-closed reconciliation of recorded nodes against junit rows.

    The expected identity Counter is built from the actually recorded
    collection nodes and compared to the parsed normal junit case Counter,
    so missing, extra, duplicate or same-count-wrong identities all fail.
    Subtest rows are keyed to their parent cases separately and are never
    equated with node totals. Collection diagnostics must have pytest's
    module-level identity, cite an exact in-scope source path token and belong
    to a module with no collected cases; unrelated or duplicate rows fail.
    """
    expected = Counter()
    unmapped = []
    for node in node_ids or []:
        identity = node_case_identity(node)
        if identity is None:
            unmapped.append(node)
        else:
            expected[identity] += 1
    observed = Counter(document['normal'])
    problems = []
    if unmapped:
        problems.append(
            'collected nodes without a junit identity mapping: '
            + ', '.join(sorted(unmapped))
        )
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    duplicates = sorted(
        identity
        for identity, count in observed.items()
        if count > expected.get(identity, 0)
    )
    if missing:
        problems.append(
            'junit rows missing for collected nodes: '
            + ', '.join(f'{cls}::{name}' for cls, name in missing)
        )
    if extra:
        problems.append(
            'junit rows without a collected node identity: '
            + ', '.join(f'{cls}::{name}' for cls, name in extra)
        )
    if duplicates:
        problems.append(
            'duplicate junit case identities: '
            + ', '.join(f'{cls}::{name}' for cls, name in duplicates)
        )
    for classname, name in document['subtests']:
        if (classname, subtest_parent_name(name)) not in expected:
            problems.append(f'subtest row {name!r} is not keyed to a parent case')
    collection_paths = {
        (
            '',
            'core.tests.' + rel.removesuffix('.py').replace('/', '.'),
        ): f'{CONTAINER_TESTS}/{rel}'
        for rel in collection_sources
    }
    seen_collection = set()
    for row in document['rows']:
        if row['kind'] != 'collection':
            continue
        identity = (row['classname'], row['name'])
        source = collection_paths.get(identity)
        has_collected_cases = any(
            cls == row['name'] or cls.startswith(row['name'] + '.')
            for cls, _ in expected
        )
        if (
            source is None
            or not _has_source_path_token(row['text'], source)
            or has_collected_cases
        ):
            problems.append(
                f'collection diagnostic {identity!r} is not attributable '
                'to an uncollected in-scope source'
            )
        elif identity in seen_collection:
            problems.append(f'duplicate collection diagnostic {identity!r}')
        seen_collection.add(identity)
    return {'expected': expected, 'observed': observed, 'problems': problems}


def _junit_row_for(junit_rows, rel):
    """Return a source-attributed module diagnostic, never a normal row."""
    identity = ('', 'core.tests.' + rel.removesuffix('.py').replace('/', '.'))
    for row in junit_rows or []:
        if (
            row['kind'] == 'collection'
            and (row['classname'], row['name']) == identity
            and _has_source_path_token(row['text'], f'{CONTAINER_TESTS}/{rel}')
        ):
            return row
    return None


def parse_junit_rows(junit_text):
    """Explicit row accounting from the structural junit parse."""
    return parse_junit_document(junit_text)['counts']


def build_accounting(repo, discovered, node_text, junit_rows, collect_log):
    """Account for every discovered source file; omissions are failures."""
    counts = {}
    sources = set(discovered)
    unmatched_nodes = []
    for line in (node_text or '').splitlines():
        node = line.strip()
        if not node:
            continue
        source = node.partition('::')[0]
        for prefix in (f'{CONTAINER_TESTS}/', 'core/tests/'):
            if source.startswith(prefix):
                source = source[len(prefix) :]
                break
        if source in sources:
            counts[source] = counts.get(source, 0) + 1
        else:
            unmatched_nodes.append(node)
    statuses = {}
    collected_files = []
    empty_files = []
    omitted_files = []
    collection_issues = {}
    for rel in discovered:
        count = counts.get(rel, 0)
        if count > 0:
            statuses[rel] = 'collected'
            collected_files.append(rel)
            continue
        probes = count_test_callables(repo / TESTS_SUBPATH / rel)
        if probes == 0:
            statuses[rel] = 'empty'
            empty_files.append(rel)
            continue
        if probes < 0:
            statuses[rel] = 'unparsable-source'
            omitted_files.append(rel)
            continue
        row = _junit_row_for(junit_rows, rel)
        if row is not None and row['outcome'] == 'error':
            statuses[rel] = 'collection-error'
            collection_issues[rel] = 'junit error entry'
        elif row is not None and row['outcome'] == 'skipped':
            statuses[rel] = 'collection-skip'
            collection_issues[rel] = 'junit skipped entry'
        else:
            statuses[rel] = 'omission'
            omitted_files.append(rel)
    return {
        'discovered': len(discovered),
        'collected_files': len(collected_files),
        'empty_files': sorted(empty_files),
        'omitted_files': sorted(omitted_files),
        'collection_issues': collection_issues,
        'statuses': statuses,
        'node_total': sum(counts.values()),
        'unmatched_nodes': sorted(unmatched_nodes),
    }


def _read_text(path):
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


def main(argv=None):
    """Run the ai/core test scope in the pinned container; return an exit code."""
    parser = argparse.ArgumentParser(
        prog='run_tests.py',
        description=(
            'Offline reproducible pytest runner for ai/core tests inside the '
            'pinned baseline container.'
        ),
    )
    parser.add_argument('--repo', required=True, help='migration worktree')
    parser.add_argument('--output', required=True, help='new external report dir')
    parser.add_argument(
        '--collect-only', action='store_true', help='collection-only mode'
    )
    parser.add_argument(
        '--test',
        dest='tests',
        action='append',
        default=[],
        help='selector inside ai/core/tests (repeatable; selected, never full)',
    )
    parser.add_argument('--image', default=DEFAULT_IMAGE)
    parser.add_argument('--volume', default=DEFAULT_VOLUME)
    parser.add_argument('--interpreter', default=DEFAULT_INTERPRETER)
    parser.add_argument('--timeout', type=float, default=1800.0)
    args = parser.parse_args(argv)

    try:
        repo = validate_repo(args.repo)
        out_dir = validate_output_dir(args.output, repo)
        selections = validate_selections(args.tests, repo)
    except UsageError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return EXIT_USAGE

    if args.collect_only:
        mode = 'collect-only'
    elif selections:
        mode = 'selected'
    else:
        mode = 'full'
    targets = [f'{CONTAINER_TESTS}/{sel}' for sel in selections] or [CONTAINER_TESTS]
    run_id = f'run-{uuid.uuid4().hex}'
    name = container_name_for(run_id)
    run_dir = f'{CONTAINER_RUNTIME}/{run_id}'

    state = {
        'failures': [],
        'transport_failures': [],
        'pytest_exit': None,
        'phases': [],
        'reports_copied': [],
        'security_verification': {},
        'collection_verification': None,
        'junit_import': {},
        'container_id': None,
        'container_name': name,
        'container_command': [],
        'exec_commands': [],
        'cleanup': {},
        'start_exit': None,
    }
    ownership_attempted = False
    created_id = None
    pinned_id = None
    out_dir_created_by_us = False
    accounting = None
    junit_text = ''
    junit_rows = []
    junit_counts = parse_junit_rows('')
    node_total = 0
    discovered = []
    collect_log = ''
    phase_logs = {}

    def record_phase(phase_name, report_type, phase_argv, result, log_name):
        """Record one real phase result on the host before anything follows."""
        state['phases'].append({
            'name': phase_name,
            'report_type': report_type,
            'exit_code': result.returncode,
            'argv': [str(a) for a in phase_argv],
        })
        text = (result.stdout or '') + (result.stderr or '')
        phase_logs[log_name] = text
        (out_dir / log_name).write_text(text, encoding='utf-8')
        state['exec_commands'].append([str(a) for a in phase_argv])
        return text

    try:
        try:
            out_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            state['transport_failures'].append(
                'output directory appeared after validation and before '
                'creation; its bytes are never adopted, deleted or written'
            )
            raise _StopError()
        out_dir_created_by_us = True
        for probe, label in (
            (['image', 'inspect', args.image], 'image'),
            (['volume', 'inspect', args.volume], 'volume'),
        ):
            result = run_cmd(['docker', '--context', DOCKER_CONTEXT, *probe])
            if result.returncode != 0:
                state['transport_failures'].append(
                    f'{label} {probe[-1]!r} is not present locally; '
                    f'no pull/install is performed'
                )
                raise _StopError()
        existing = run_cmd([
            'docker',
            '--context',
            DOCKER_CONTEXT,
            'container',
            'inspect',
            name,
        ])
        if existing.returncode == 0:
            state['transport_failures'].append(
                f'owned container name {name!r} is already in use; '
                f'nothing is adopted or overwritten'
            )
            raise _StopError()
        keeper = build_keeper()
        create_argv = build_create_argv(
            repo, args.image, args.volume, run_id, keeper, interpreter=args.interpreter
        )
        state['container_command'] = create_argv
        ownership_attempted = True
        made = run_cmd(create_argv)
        if made.returncode != 0:
            state['transport_failures'].append(
                f'docker create failed: {(made.stderr or "").strip()}'
            )
            raise _StopError()
        create_token = (made.stdout or '').strip()
        if CONTAINER_ID.match(create_token):
            created_id = create_token
        else:
            state['transport_failures'].append(
                f'docker create returned malformed container id '
                f'{create_token!r}; literal format validation is kept and '
                f'the token is never passed to rm or inspect'
            )

        inspected = run_cmd(['docker', '--context', DOCKER_CONTEXT, 'inspect', name])
        if inspected.returncode != 0:
            state['transport_failures'].append(
                f'docker inspect {name!r} failed: {(inspected.stderr or "").strip()}'
            )
            raise _StopError()
        doc = json.loads(inspected.stdout or '[]')
        problems, details = verify_container(doc, args.volume)
        state['security_verification'] = details
        if problems:
            state['transport_failures'].append(
                'security verification failed: ' + '; '.join(problems)
            )
            raise _StopError()
        owned, own_problems, resolved_id = verify_ownership(doc, name, run_id)
        if not owned:
            state['transport_failures'].append(
                'owned container identity mismatch: ' + '; '.join(own_problems)
            )
            raise _StopError()
        if created_id is not None:
            if resolved_id != created_id:
                pinned_id = created_id
                state['container_id'] = pinned_id
                state['transport_failures'].append(
                    'owned exact-name inspect returned id '
                    f'{resolved_id} instead of the created id {created_id}; '
                    'aborting before start/exec and never removing the '
                    'replacement container'
                )
                raise _StopError()
            pinned_id = created_id
        else:
            pinned_id = resolved_id
        state['container_id'] = pinned_id

        started = run_cmd(['docker', '--context', DOCKER_CONTEXT, 'start', pinned_id])
        state['start_exit'] = started.returncode
        if started.returncode != 0:
            state['transport_failures'].append(
                f'docker start failed: {(started.stderr or "").strip()}'
            )
            raise _StopError()
        # docker start reports startup status only; it is never a phase exit.

        prep_argv = build_exec_argv(
            pinned_id,
            args.interpreter,
            build_prepare_script(run_dir),
            'prepare',
            user='0:0',
        )
        try:
            prep = run_cmd(prep_argv, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            state['transport_failures'].append(
                f'timeout: prepare phase exceeded {args.timeout}s'
            )
            raise _StopError()
        record_phase('prepare', 'host-orchestration', prep_argv, prep, 'prepare.log')
        if prep.returncode != 0:
            state['failures'].append(
                f'preparation phase failed with exit {prep.returncode}'
            )
            state['pytest_exit'] = prep.returncode
            raise _StopError()

        collect_argv = build_exec_argv(
            pinned_id,
            args.interpreter,
            build_child_wrapper(
                run_dir, args.interpreter, PYTEST_CONFIG, targets, 'collect'
            ),
            'collect',
        )
        try:
            collect = run_cmd(collect_argv, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            state['transport_failures'].append(
                f'timeout: collect phase exceeded {args.timeout}s'
            )
            raise _StopError()
        collect_log = record_phase(
            'collect', 'collection', collect_argv, collect, 'collect.log'
        )
        # Host-side truth, recorded BEFORE any later candidate execution can
        # run: raw log above and the node identifiers parsed from it.
        node_ids = parse_collect_nodes(collect_log)
        node_text = ''.join(n + '\n' for n in node_ids)
        (out_dir / 'collected_nodes.txt').write_text(node_text, encoding='utf-8')
        (out_dir / 'collection-evidence.json').write_text(
            json.dumps(
                {
                    'node_ids': node_ids,
                    'collect_exit': collect.returncode,
                    'recorded_by': 'host-captured docker exec',
                    'recorded_before_execution': True,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding='utf-8',
        )
        state['collection_verification'] = {
            'verified': False,
            'authenticated': False,
            'consistency_checked': False,
            'source': 'host-captured-collect-stdout',
            'provenance': (
                'candidate-produced collection observation captured by the '
                'host transport; host-owned storage does not authenticate it '
                '(trusted reviewed test-code assumption)'
            ),
            'recorded_before_execution': True,
        }

        discovered = discover_test_sources(repo)
        requested = {sel.split('::', 1)[0] for sel in selections}
        in_scope = [rel for rel in discovered if not requested or rel in requested]
        exec_exit = 0
        if not args.collect_only:
            junit_path = f'{run_dir}/reports/{JUNIT_NAME}'
            exec_argv = build_exec_argv(
                pinned_id,
                args.interpreter,
                build_child_wrapper(
                    run_dir,
                    args.interpreter,
                    PYTEST_CONFIG,
                    targets,
                    'execute',
                    junit_path=junit_path,
                ),
                'execute',
            )
            try:
                executed = run_cmd(exec_argv, timeout=args.timeout)
            except subprocess.TimeoutExpired:
                state['transport_failures'].append(
                    f'timeout: execute phase exceeded {args.timeout}s'
                )
                raise _StopError()
            record_phase(
                'execute', 'normal+subtest', exec_argv, executed, 'execution.log'
            )
            exec_exit = executed.returncode
            dst = out_dir / JUNIT_NAME
            copied = run_cmd([
                'docker',
                '--context',
                DOCKER_CONTEXT,
                'cp',
                f'{pinned_id}:{junit_path}',
                str(dst),
            ])
            junit_text = ''
            imported = False
            method = 'docker cp'
            if copied.returncode == 0 and dst.is_file():
                imported = True
            else:
                cp_error = (
                    f'docker cp exit {copied.returncode}: '
                    f'{(copied.stderr or copied.stdout or "").strip()}'
                )
                reader_argv = build_exec_argv(
                    pinned_id,
                    args.interpreter,
                    build_junit_reader(junit_path),
                    'import-junit',
                    user='0:0',
                )
                try:
                    read_res = run_cmd(reader_argv, timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    read_res = None
                if read_res is not None and read_res.returncode == 0:
                    junit_text = read_res.stdout or ''
                    dst.write_text(junit_text, encoding='utf-8')
                    imported = True
                    method = 'host-orchestrated exec read'
                else:
                    state['failures'].append(
                        f'missing or incomplete report: {JUNIT_NAME} ({cp_error})'
                    )
            state['junit_import'] = {'method': method}
            if imported:
                state['reports_copied'].append(JUNIT_NAME)
                junit_text = _read_text(dst)
            if JUNIT_NAME in state['reports_copied']:
                expected_identities = [
                    identity
                    for identity in (node_case_identity(n) for n in node_ids)
                    if identity is not None
                ]
                document = parse_junit_document(junit_text, expected_identities)
                junit_rows = document['rows']
                junit_counts = document['counts']
                state['junit_import']['structural_problems'] = document['problems']
                if document['problems']:
                    state['failures'].append(
                        'junit report rejected: ' + '; '.join(document['problems'])
                    )
                    state['failures'].append(
                        'node/execution consistency not established: the '
                        'junit candidate artifact is structurally invalid'
                    )
                else:
                    problem_rows = (
                        document['counts']['failure'] + document['counts']['error']
                    )
                    if exec_exit == 0 and problem_rows:
                        state['failures'].append(
                            'phase/junit inconsistency: junit reports failure '
                            'or error rows while the execute phase returned 0'
                        )
                    elif exec_exit != 0 and problem_rows == 0:
                        state['failures'].append(
                            f'phase/junit inconsistency: execute phase '
                            f'returned {exec_exit} but junit reports no '
                            f'failure or error rows'
                        )
                    reconciliation = reconcile_node_cases(node_ids, document, in_scope)
                    state['junit_import']['reconciliation'] = {
                        'expected_cases': sum(reconciliation['expected'].values()),
                        'observed_cases': sum(reconciliation['observed'].values()),
                        'problems': reconciliation['problems'],
                    }
                    for problem in reconciliation['problems']:
                        state['failures'].append(
                            'node/execution reconciliation: ' + problem
                        )
                    state['collection_verification']['consistency_checked'] = True

        collect_exit = collect.returncode
        state['pytest_exit'] = collect_exit or exec_exit

        accounting = build_accounting(
            repo, in_scope, node_text, junit_rows, collect_log
        )
        accounting['not_selected_files'] = sorted(set(discovered) - set(in_scope))
        node_total = accounting['node_total']
        if node_total == 0:
            state['failures'].append(
                'zero nodes collected unexpectedly in ai/core/tests'
            )
        if accounting['omitted_files']:
            state['failures'].append(
                'source accounting omission, test files never accounted '
                'for: ' + ', '.join(accounting['omitted_files'])
            )
    except subprocess.TimeoutExpired:
        state['transport_failures'].append(
            f'timeout: docker transport exceeded {args.timeout}s'
        )
    except OSError as exc:
        state['transport_failures'].append(f'docker transport error: {exc}')
    except _StopError:
        pass
    finally:
        cleanup_info = {
            'container_removed': False,
            'container_id': None,
            'pinned_id': pinned_id,
            'refused': [],
        }
        if ownership_attempted or created_id or pinned_id:
            found = run_cmd(['docker', '--context', DOCKER_CONTEXT, 'inspect', name])
            if found.returncode == 0:
                try:
                    doc = json.loads(found.stdout or '[]')
                except ValueError:
                    doc = []
                owned, own_problems, resolved_id = verify_ownership(doc, name, run_id)
                if pinned_id is None:
                    cleanup_info['refused'] = ['owned container id never pinned']
                    state['failures'].append(
                        'cleanup refused: owned container id was never '
                        'pinned; no unverified container is removed'
                    )
                elif not owned:
                    cleanup_info['refused'] = own_problems
                    state['failures'].append(
                        'cleanup refused: resolved container does not match '
                        'owned name/label: ' + '; '.join(own_problems)
                    )
                elif resolved_id != pinned_id:
                    cleanup_info['refused'] = [
                        f'resolved id {resolved_id} does not match pinned id '
                        f'{pinned_id}'
                    ]
                    state['failures'].append(
                        'cleanup refused: final inspect id does not match the '
                        'pinned owned container id; the replacement container '
                        'is never removed'
                    )
                else:
                    removed = run_cmd([
                        'docker',
                        '--context',
                        DOCKER_CONTEXT,
                        'rm',
                        '-f',
                        pinned_id,
                    ])
                    cleanup_info['container_id'] = pinned_id
                    if removed.returncode == 0:
                        cleanup_info['container_removed'] = True
                    else:
                        state['failures'].append(
                            f'cleanup issue: docker rm {pinned_id} failed: '
                            f'{(removed.stderr or "").strip()}'
                        )
            elif pinned_id:
                state['failures'].append(
                    'cleanup issue: owned container missing at cleanup'
                )
        state['cleanup'] = cleanup_info
        if out_dir_created_by_us and out_dir.is_dir():
            (out_dir / 'stdout.log').write_text(
                phase_logs.get('prepare.log', '')
                + phase_logs.get('collect.log', '')
                + phase_logs.get('execution.log', ''),
                encoding='utf-8',
            )
            runtime = {
                'schema': 'aimms-maf-harness-runtime',
                'host_python': sys.version,
                'container_interpreter': args.interpreter,
                'test_user': TEST_USER,
                'dependency_snapshot': DEPS_SITE,
                'orchestrated_by': 'host',
                'env_scrubbed_by': 'child-wrapper',
            }
            (out_dir / 'runtime.json').write_text(
                json.dumps(runtime, indent=2, sort_keys=True), encoding='utf-8'
            )
            outcomes = {
                'phases': state['phases'],
                'pytest_exit': state['pytest_exit'],
                'authority': 'host-captured docker exec return codes',
                'warnings_note': (
                    'pytest warnings summary preserved verbatim in '
                    'collect.log and execution.log; warnings are not '
                    'suppressed'
                ),
            }
            (out_dir / 'outcomes.json').write_text(
                json.dumps(outcomes, indent=2, sort_keys=True), encoding='utf-8'
            )
            (out_dir / 'cleanup.json').write_text(
                json.dumps(cleanup_info, indent=2, sort_keys=True), encoding='utf-8'
            )
            manifest = {
                'schema': 'aimms-maf-harness-run-manifest',
                'schema_version': 1,
                'run_id': run_id,
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'mode': mode,
                'labels': {
                    'full_suite': mode == 'full',
                    'collect_only': args.collect_only,
                    'selected': list(selections),
                },
                'repo_path': str(repo),
                'image': args.image,
                'volume': args.volume,
                'interpreter': args.interpreter,
                'container_id': state['container_id'],
                'container_name': name,
                'ownership_label': f'{OWNERSHIP_LABEL}={run_id}',
                'container_command': state['container_command'],
                'exec_commands': state['exec_commands'],
                'security_verification': state['security_verification'],
                'collection_verification': state['collection_verification'],
                'source_files': discovered,
                'phases': state['phases'],
                'reports_copied': state['reports_copied'],
                'junit_import': state['junit_import'],
                'pytest_exit': state['pytest_exit'],
            }
            (out_dir / 'manifest.json').write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding='utf-8'
            )
            (out_dir / 'container_command.txt').write_text(
                json.dumps(state['container_command'], indent=1), encoding='utf-8'
            )
            summary = {
                'schema': 'aimms-maf-harness-run-summary',
                'schema_version': 1,
                'run_id': run_id,
                'mode': mode,
                'source_accounting': accounting
                or {
                    'discovered': 0,
                    'collected_files': 0,
                    'empty_files': [],
                    'omitted_files': [],
                    'collection_issues': {},
                    'statuses': {},
                },
                'node_total': node_total,
                'junit_rows': junit_counts,
                'collection_verification': state['collection_verification'],
                'junit_note': JUNIT_NOTE,
                'phases': state['phases'],
                'pytest_exit': state['pytest_exit'],
                'reports_copied': state['reports_copied'],
                'junit_import': state['junit_import'],
                'cleanup': cleanup_info,
                'failures': state['failures'] + state['transport_failures'],
            }
            (out_dir / 'summary.json').write_text(
                json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8'
            )

    failures = state['failures'] + state['transport_failures']
    for line in failures:
        print(f'FAIL: {line}')
    print(
        f'run {run_id} mode={mode} nodes={node_total} '
        f'pytest_exit={state["pytest_exit"]} reports={len(state["reports_copied"])}'
    )
    if state['transport_failures']:
        return EXIT_TRANSPORT
    if state['pytest_exit'] not in (None, 0):
        return int(state['pytest_exit']) or EXIT_FAILURE
    if state['failures']:
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
