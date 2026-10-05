"""Negative-control tests for contrib/aimms_harness/run_tests.py.

TDD contract tests for the offline Docker-backed ai/core test runner with
host-side orchestration.

Two flavours of test are used and labelled in each docstring:

* "Real" tests drive the run_tests.py CLI through a subprocess and exercise
  validation behaviour that must fail before any docker call is made.
* "Mocked" tests (explicitly labelled) patch ``run_tests.run_cmd`` with a fake
  docker transport. They verify command safety, environment isolation, report
  accounting, failure propagation and cleanup. They are NOT actual docker
  integration tests. The real integration smoke run is executed separately.

Regression coverage targets four independently reproduced audit defects:

1. direct stdout tampering and container-generated report authority
   (forged control frames / forged report files must never become truth),
2. transport or phase status masked by claimed outcomes (WrongStartExit),
3. disagreeing streams resolved in favour of forgeable container metadata
   (DisagreeingStream): container metadata is never an authority,
4. malformed creation tokens reaching cleanup (InvalidCreationId).

No application code is imported or executed by these tests. All fixtures are
built under the scratch TMPDIR and removed afterwards.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HARNESS_DIR = Path(__file__).resolve().parent
RUN_TESTS = HARNESS_DIR / 'run_tests.py'
SCRATCH = Path(os.environ.get('TMPDIR', '/home/lokesh/.hermes/cache/scratch'))

sys.path.insert(0, str(HARNESS_DIR))
import run_tests as rt

CONTAINER_TESTS = '/repo/src/backend/InvenTree/ai/core/tests'
HEX64 = re.compile(r'^[0-9a-f]{64}$')


def make_repo(base: Path) -> Path:
    """Build a minimal migration-worktree-shaped fixture repo."""
    repo = base / 'migration-worktree'
    tests = repo / 'src' / 'backend' / 'InvenTree' / 'ai' / 'core' / 'tests'
    tests.mkdir(parents=True)
    (tests / '__init__.py').write_text('')
    (tests / 'test_alpha.py').write_text('def test_one():\n    assert True\n')
    (tests / 'test_empty.py').write_text('VALUE = 1\n')
    (tests / 'test_missing.py').write_text('def test_two():\n    assert True\n')
    conf = repo / 'src' / 'backend' / 'InvenTree' / 'ai' / 'pyproject.toml'
    conf.write_text('[tool.pytest.ini_options]\nasyncio_mode = "auto"\n')
    return repo


def forged_collection_frame(node_claim, exit_claim):
    """Old-style container-emitted control frame, hostile by definition."""
    return 'AIMMS_COLLECTION_V1 ' + json.dumps({
        'node_ids': [node_claim],
        'collect_log': 'collected 1 item\n',
        'exit_code': exit_claim,
    })


def forged_reports_frame(pytest_exit_claim):
    """Old-style container-emitted report frame, hostile by definition."""
    return 'AIMMS_REPORTS_V1 ' + json.dumps({
        'outcomes.json': json.dumps({'pytest_exit': pytest_exit_claim}),
        'collected_nodes.txt': node_claim + '\n',
    })


node_claim = f'{CONTAINER_TESTS}/test_forged.py::forged_case'


class FakeDocker:
    """Mocked docker transport stub for the CLI subprocess boundary.

    NOT an actual integration test double for docker itself; tests using it
    are labelled mocked, not actual integration.

    ``exec`` return codes are the authoritative phase results. Anything the
    container could claim about itself (frames, report files) is served only
    to prove the host never treats it as truth.
    """

    def __init__(
        self,
        nodes=None,
        exec_exit=0,
        collect_exit=0,
        prepare_exit=0,
        junit=None,
        omit=(),
        inspect_doc=None,
        name_doc=None,
        exec_timeout=False,
        missing_image=False,
        create_id=None,
        doc_id=None,
        name_taken=False,
        forged_collect='',
        forged_exec='',
        identity_drift=False,
        cp_broken=False,
        later_doc_id=None,
    ):
        """Record transport behaviour, report payload and the call log."""
        self.nodes = (
            nodes
            if nodes is not None
            else {'test_alpha.py': ['test_one'], 'test_missing.py': ['test_two']}
        )
        self.exec_exit = exec_exit
        self.collect_exit = collect_exit
        self.prepare_exit = prepare_exit
        self.omit = set(omit)
        self.inspect_doc = inspect_doc
        self.name_doc = name_doc
        self.exec_timeout = exec_timeout
        self.missing_image = missing_image
        self.create_id = create_id if create_id is not None else 'c' * 64
        self.doc_id = doc_id if doc_id is not None else 'c' * 64
        self.name_taken = name_taken
        self.forged_collect = forged_collect
        self.forged_exec = forged_exec
        self.identity_drift = identity_drift
        self.cp_broken = cp_broken
        self.later_doc_id = later_doc_id
        self.calls = []
        self.rm_calls = []
        self.cp_targets = []
        self.created_name = None
        self.name_inspections = 0
        if junit is not None:
            self.junit = junit
        else:
            self.junit = self._default_junit()

    def _default_junit(self):
        """Realistic pytest junit identities generated from claimed nodes."""
        cases = []
        for rel, names in sorted(self.nodes.items()):
            classname = 'core.tests.' + rel[:-3]
            cases.extend((classname, name) for name in names)
        rows = [f'<testcase classname="{cls}" name="{name}"/>' for cls, name in cases]
        subtest = ''
        if cases:
            cls, name = cases[0]
            subtest = f'<testcase classname="{cls}" name="{name} (Subtest: 0)"/>'
        return (
            '<?xml version="1.0"?><testsuites><testsuite>'
            + ''.join(rows)
            + subtest
            + '</testsuite></testsuites>'
        )

    def _node_lines(self):
        return [
            f'{CONTAINER_TESTS}/{rel}::{name}'
            for rel, names in sorted(self.nodes.items())
            for name in names
        ]

    def _collect_stdout(self):
        text = ''.join(line + '\n' for line in self._node_lines())
        return self.forged_collect + '\n' + text if self.forged_collect else text

    def _default_doc(self):
        name = self.created_name or 'aimms-run-unknown'
        run_id = name[len('aimms-') :] if name.startswith('aimms-') else ''
        return [
            {
                'Id': self.doc_id,
                'Name': '/' + name,
                'HostConfig': {
                    'NetworkMode': 'none',
                    'ReadonlyRootfs': True,
                    'CapDrop': ['ALL'],
                    'SecurityOpt': ['no-new-privileges'],
                    'Tmpfs': {
                        '/runs': 'rw,noexec,nosuid,nodev,size=536870912',
                        '/deps': 'rw,exec,nosuid,nodev,size=1073741824',
                    },
                },
                'Mounts': [
                    {'Destination': '/repo', 'RW': False, 'Name': ''},
                    {
                        'Destination': '/scratch',
                        'RW': False,
                        'Name': 'aimms-maf-harness-setup-fe1e6a7f',
                    },
                ],
                'Config': {
                    'Env': ['SECRET_TOKEN=hunter2', 'PATH=/x'],
                    'Labels': {rt.OWNERSHIP_LABEL: run_id},
                },
                'State': {'ExitCode': self.exec_exit},
            }
        ]

    def __call__(self, argv, timeout=None):
        """Serve one fake docker invocation and return a completed process."""
        self.calls.append([str(a) for a in argv])
        sub = argv[3]
        if sub in ('image', 'volume'):
            ok = not (sub == 'image' and self.missing_image)
            return subprocess.CompletedProcess(argv, 0 if ok else 1, '', '')
        if sub == 'container':
            self.created_name = argv[5]
            if self.name_taken:
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps(self._default_doc()), ''
                )
            return subprocess.CompletedProcess(argv, 1, '', 'no such container')
        if sub == 'create':
            joined = argv
            self.created_name = joined[joined.index('--name') + 1]
            return subprocess.CompletedProcess(argv, 0, self.create_id + '\n', '')
        if sub == 'inspect':
            self.name_inspections += 1
            if self.name_doc is not None:
                doc = self.name_doc
            elif self.identity_drift and self.name_inspections > 1:
                doc = self._default_doc()
                doc[0]['Name'] = '/someone-elses-container'
            elif self.later_doc_id and self.name_inspections > 1:
                doc = self._default_doc()
                doc[0]['Id'] = self.later_doc_id
            else:
                doc = self.inspect_doc or self._default_doc()
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), '')
        if sub == 'start':
            return subprocess.CompletedProcess(argv, 0, '', '')
        if sub == 'exec':
            if self.exec_timeout:
                raise subprocess.TimeoutExpired(argv, timeout or 1)
            script = argv[-1]
            if 'copytree' in script:
                return subprocess.CompletedProcess(
                    argv, self.prepare_exit, 'AIMMS_PREPARE_V1 ok\n', ''
                )
            if 'aimms-junit-reader-v1' in script:
                if 'junit.xml' in self.omit:
                    return subprocess.CompletedProcess(argv, 1, '', 'no such path')
                return subprocess.CompletedProcess(argv, 0, self.junit, '')
            if '--collect-only' in script:
                return subprocess.CompletedProcess(
                    argv, self.collect_exit, self._collect_stdout(), ''
                )
            exec_text = 'pytest execution output\n'
            if self.forged_exec:
                exec_text = self.forged_exec + '\n' + exec_text
            return subprocess.CompletedProcess(argv, self.exec_exit, exec_text, '')
        if sub == 'cp':
            src = argv[4].split(':', 1)[1]
            dst = Path(argv[5])
            self.cp_targets.append(src.rsplit('/', 1)[-1])
            if self.cp_broken:
                return subprocess.CompletedProcess(argv, 1, '', 'tmpfs blind')
            if src.endswith('/junit.xml') and 'junit.xml' not in self.omit:
                dst.write_text(self.junit, encoding='utf-8')
                return subprocess.CompletedProcess(argv, 0, '', '')
            return subprocess.CompletedProcess(argv, 1, '', 'no such path')
        if sub == 'rm':
            self.rm_calls.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, '', '')
        if sub == 'kill':
            return subprocess.CompletedProcess(argv, 0, '', '')
        return subprocess.CompletedProcess(argv, 125, '', 'unexpected call')


class RealCliTests(unittest.TestCase):
    """Real subprocess tests of validation; no docker is reached."""

    def setUp(self):
        """Build a disposable fixture worktree under scratch."""
        self.tmp = Path(tempfile.mkdtemp(dir=SCRATCH, prefix='rt-cli-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = make_repo(self.tmp)

    def run_cli(self, *args):
        """Invoke the real CLI against the fixture worktree."""
        out = self.tmp / 'out'
        cmd = [
            sys.executable,
            str(RUN_TESTS),
            '--repo',
            str(self.repo),
            '--output',
            str(out),
            *map(str, args),
        ]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def test_missing_repo_is_rejected(self):
        """Real: a nonexistent migration worktree is rejected."""
        res = subprocess.run(
            [
                sys.executable,
                str(RUN_TESTS),
                '--repo',
                str(self.tmp / 'nope'),
                '--output',
                str(self.tmp / 'out'),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn('repo', res.stderr.lower())

    def test_output_inside_repository_is_rejected(self):
        """Real: repository-contained output is rejected."""
        res = subprocess.run(
            [
                sys.executable,
                str(RUN_TESTS),
                '--repo',
                str(self.repo),
                '--output',
                str(self.repo / 'reports'),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn('repository', res.stderr.lower())

    def test_existing_output_directory_is_rejected(self):
        """Real: an existing output directory is never overwritten."""
        existing = self.tmp / 'taken'
        existing.mkdir()
        (existing / 'keep.txt').write_text('keep')
        res = subprocess.run(
            [
                sys.executable,
                str(RUN_TESTS),
                '--repo',
                str(self.repo),
                '--output',
                str(existing),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn('exist', res.stderr.lower())
        self.assertEqual((existing / 'keep.txt').read_text(), 'keep')

    def test_symlinked_output_ancestor_is_rejected(self):
        """Real: output paths with symlinked ancestors are rejected."""
        real = self.tmp / 'real'
        real.mkdir()
        link = self.tmp / 'link'
        link.symlink_to(real)
        res = subprocess.run(
            [
                sys.executable,
                str(RUN_TESTS),
                '--repo',
                str(self.repo),
                '--output',
                str(link / 'out'),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn('symlink', res.stderr.lower())

    def test_relative_output_is_rejected(self):
        """Real: relative output paths are rejected."""
        res = subprocess.run(
            [
                sys.executable,
                str(RUN_TESTS),
                '--repo',
                str(self.repo),
                '--output',
                'relative/out',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 2)

    def test_malformed_selections_are_rejected(self):
        """Real: malformed --test selectors are rejected literally."""
        bad = [
            '../test_alpha.py',
            '/etc/passwd',
            'test_alpha.py::',
            'test_alpha.py::bad name',
            'test_alpha.py::test?x',
            'test_alpha.py::test_one::',
            'no_such_file.py',
            'test_alpha.py/../test_alpha.py',
            'sub/../test_alpha.py',
        ]
        for sel in bad:
            with self.subTest(selector=sel):
                res = self.run_cli('--test', sel)
                self.assertEqual(res.returncode, 2, res.stderr)
                self.assertIn('selector', res.stderr.lower())

    def test_selection_outside_tests_dir_is_rejected(self):
        """Real: selectors escaping ai/core/tests are rejected."""
        res = self.run_cli('--test', '../pyproject.toml')
        self.assertEqual(res.returncode, 2)

    def test_symlinked_selection_source_is_rejected(self):
        """Real: symlinked test sources are rejected as selectors."""
        tests = self.repo / 'src' / 'backend' / 'InvenTree' / 'ai' / 'core' / 'tests'
        (tests / 'test_link.py').symlink_to(tests / 'test_alpha.py')
        res = self.run_cli('--test', 'test_link.py')
        self.assertEqual(res.returncode, 2)
        self.assertIn('symlink', res.stderr.lower())

    def test_valid_selection_passes_validation(self):
        """Real: valid selectors pass validation and reach the docker stage.

        A deliberately absent pinned image keeps the docker stage at the
        local image check, so no container is created; the point under test
        is that validation never rejects a valid selector.
        """
        res = self.run_cli(
            '--test', 'test_alpha.py::test_one', '--image', 'sha256:' + '0' * 64
        )
        self.assertNotEqual(res.returncode, 2)
        self.assertNotIn('selector', res.stderr.lower())


class KeeperAndWrapperTests(unittest.TestCase):
    """Real source-contract tests of keeper, preparation and child scripts."""

    def test_keeper_is_a_minimal_root_stdlib_pause_loop(self):
        """Real: PID1 keeper scrubs env and pauses; no app/test imports."""
        keeper = rt.build_keeper()
        self.assertIn('os.environ.clear()', keeper)
        self.assertIn('signal.pause()', keeper)
        self.assertLess(
            keeper.index('os.environ.clear()'), keeper.index('signal.pause()')
        )
        self.assertNotIn('django', keeper.lower())
        self.assertNotIn('pytest', keeper.lower())
        self.assertNotIn('import InvenTree', keeper)
        self.assertNotIn('sys.path', keeper)

    def test_prepare_script_copies_only_the_installed_package_tree(self):
        """Real: root preparation snapshots installed bytes, never HOME."""
        script = rt.build_prepare_script('/runs/run-abc')
        self.assertIn('shutil.copytree', script)
        self.assertIn('/root/.local/lib/python3.14/site-packages', script)
        self.assertIn('/deps/image-site', script)
        self.assertIn("ignore=shutil.ignore_patterns('__pycache__')", script)
        self.assertIn('dirs_exist_ok=True', script)
        self.assertIn('0o755', script)
        self.assertIn('0o644', script)
        self.assertIn('0o666', script)
        self.assertIn('junit.xml', script)
        self.assertEqual(script.count('shutil.copytree'), 1)
        for private in ('.ssh', '.aws', '.gnupg', 'credentials', 'passwd'):
            self.assertNotIn(private, script)

    def test_child_wrapper_scrubs_env_before_configuration(self):
        """Real: child wrapper scrubs env first and owns all state."""
        wrapper = rt.build_child_wrapper(
            run_dir='/runs/run-abc',
            interpreter=rt.DEFAULT_INTERPRETER,
            config=rt.PYTEST_CONFIG,
            targets=[CONTAINER_TESTS],
            phase='collect',
            junit_path='/runs/run-abc/reports/junit.xml',
        )
        self.assertLess(
            wrapper.index('os.environ.clear()'), wrapper.index('DJANGO_SETTINGS_MODULE')
        )
        for token in (
            "'DJANGO_SETTINGS_MODULE': 'ai.core.tests.settings'",
            "'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'",
            "'AIMMS_AZURE_INTEGRATION': '0'",
            "'AIMMS_GOLDEN_LIVE': '0'",
            "'INVENTREE_CONFIG_FILE'",
            '/repo/src/backend/InvenTree:/repo/src/backend',
            '/deps/image-site',
            'pytest_asyncio.plugin',
            '--tb=short --strict-markers',
            '/runs/run-abc/home',
            'os.chdir(RUN)',
            "'--collect-only'",
            "'-m', 'pytest'",
        ):
            self.assertIn(token, wrapper)
        self.assertNotIn('os.chdir("/repo")', wrapper)
        self.assertNotIn('/root/', wrapper)
        self.assertNotIn('pytest.main', wrapper)

    def test_child_wrapper_runs_pytest_as_a_subprocess(self):
        """Real: pytest runs under `python -m pytest`, never embedded."""
        wrapper = rt.build_child_wrapper(
            run_dir='/runs/run-abc',
            interpreter=rt.DEFAULT_INTERPRETER,
            config=rt.PYTEST_CONFIG,
            targets=[CONTAINER_TESTS],
            phase='execute',
            junit_path='/runs/run-abc/reports/junit.xml',
        )
        self.assertIn('subprocess.run', wrapper)
        self.assertNotIn('pytest.main', wrapper)
        self.assertIn('--junitxml=', wrapper)
        self.assertNotIn("'--collect-only'", wrapper)

    def test_test_user_is_unprivileged_nobody(self):
        """Real: pytest phases must never run as keeper root."""
        self.assertEqual(rt.TEST_USER, '65534:65534')


class RealWrapperProcessTests(unittest.TestCase):
    """Real child-wrapper subprocesses with synthetic packages only."""

    def _wrapper(self, base: Path, phase: str, collect_only: bool):
        package = base / 'site-packages' / 'pytest'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        run = base / 'run'
        run.mkdir()
        wrapper = rt.build_child_wrapper(
            run_dir=str(run),
            interpreter=sys.executable,
            config=rt.PYTEST_CONFIG,
            targets=[CONTAINER_TESTS + '/test_alpha.py'],
            phase=phase,
            junit_path=str(run / 'reports' / 'junit.xml'),
        )
        return package, wrapper

    def test_collection_phase_failure_exit_passes_through_the_wrapper(self):
        """Real: the wrapper returns the real pytest exit code verbatim."""
        with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='rt-phases-') as tmp:
            package, wrapper = self._wrapper(Path(tmp), 'collect', True)
            (package / '__main__.py').write_text(
                'import sys\n'
                "print('core/tests/test_alpha.py::test_one')\n"
                "sys.exit(2 if '--collect-only' in sys.argv else 0)\n"
            )
            result = subprocess.run(
                [sys.executable, '-c', wrapper],
                env=dict(os.environ, PYTHONPATH=str(package.parent)),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_root_relative_collection_nodes_pass_through_to_the_host(self):
        """Real: root-relative node lines reach stdout for host parsing."""
        with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='rt-nodes-') as tmp:
            package, wrapper = self._wrapper(Path(tmp), 'collect', True)
            node = 'core/tests/test_alpha.py::test_one'
            (package / '__main__.py').write_text(f'print({node!r})\n')
            result = subprocess.run(
                [sys.executable, '-c', wrapper],
                env=dict(os.environ, PYTHONPATH=str(package.parent)),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(node, result.stdout)
            self.assertEqual(rt.parse_collect_nodes(result.stdout), [node])

    def test_scrubbed_children_keep_installed_dependency_paths(self):
        """Real: installed packages survive the HOME/env change."""
        with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='rt-shim-') as tmp:
            package, wrapper = self._wrapper(Path(tmp), 'collect', True)
            (package / '__main__.py').write_text(
                'import os, sys\n'
                "assert 'SYNTHETIC_INHERITED_SECRET' not in os.environ\n"
                'assert os.path.dirname(os.path.dirname(__file__)) in sys.path\n'
                "print('/repo/src/backend/InvenTree/ai/core/tests/"
                "test_alpha.py::test_one')\n"
            )
            env = dict(
                os.environ,
                PYTHONPATH=str(package.parent),
                SYNTHETIC_INHERITED_SECRET='synthetic-test-only',
                PYTHONDONTWRITEBYTECODE='1',
            )
            result = subprocess.run(
                [sys.executable, '-c', wrapper],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class SourceAccountingTests(unittest.TestCase):
    """Real source accounting, without an application or Docker import."""

    def test_nested_sources_are_not_credited_to_a_root_basename(self):
        """Real: only the literal source before the first :: gets credit."""
        with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='rt-sources-') as tmp:
            repo = make_repo(Path(tmp))
            tests = repo / rt.TESTS_SUBPATH
            (tests / 'zsub').mkdir()
            (tests / 'test_a.py').write_text('def test_root(): pass\n')
            (tests / 'zsub/test_a.py').write_text('def test_nested(): pass\n')
            for prefix in ('', 'core/tests/', f'{CONTAINER_TESTS}/'):
                with self.subTest(prefix=prefix):
                    node = f'{prefix}zsub/test_a.py::test_nested'
                    accounting = rt.build_accounting(
                        repo, ['test_a.py', 'zsub/test_a.py'], node, [], node
                    )
                    self.assertEqual(
                        accounting['statuses'],
                        {'test_a.py': 'omission', 'zsub/test_a.py': 'collected'},
                    )
                    self.assertEqual(accounting['omitted_files'], ['test_a.py'])
                    self.assertEqual(accounting['unmatched_nodes'], [])

    def test_other_paths_cannot_imitate_a_discovered_source(self):
        """Real: longer paths and other roots stay unmatched, not repaired."""
        with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='rt-sources-') as tmp:
            repo = make_repo(Path(tmp))
            source = f'{CONTAINER_TESTS}/test_alpha.py'
            variants = (
                source + '.unexpected',
                source + '/child',
                '/unexpected' + source,
                'other/' + source,
                'core/tests/../test_alpha.py',
            )
            for path in variants:
                with self.subTest(path=path):
                    node = path + '::test_one'
                    accounting = rt.build_accounting(
                        repo, ['test_alpha.py'], node, [], node
                    )
                    self.assertEqual(
                        accounting['statuses'], {'test_alpha.py': 'omission'}
                    )
                    self.assertEqual(accounting['unmatched_nodes'], [node])

    def test_parameter_text_does_not_change_source_attribution(self):
        """Real: literal source-looking parameters and IPv6 remain opaque."""
        with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='rt-sources-') as tmp:
            repo = make_repo(Path(tmp))
            tests = repo / rt.TESTS_SUBPATH
            (tests / 'test_a.py').write_text('def test_other(): pass\n')
            node = (
                f'{CONTAINER_TESTS}/test_alpha.py::test_one'
                f'[[::1]/{CONTAINER_TESTS}/test_a.py::test_error_marker]'
            )
            accounting = rt.build_accounting(
                repo, ['test_a.py', 'test_alpha.py'], node, [], node
            )
            self.assertEqual(
                accounting['statuses'],
                {'test_a.py': 'omission', 'test_alpha.py': 'collected'},
            )
            self.assertEqual(accounting['omitted_files'], ['test_a.py'])
            self.assertEqual(accounting['node_total'], 1)


class SourceCallableTests(unittest.TestCase):
    """Real source parsing; manual CLI methods are not pytest callables."""

    def test_manual_cli_and_nested_helpers_are_not_counted(self):
        """Real: only module tests and eligible class methods count."""
        with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='rt-callables-') as tmp:
            source = Path(tmp) / 'test_manual.py'
            source.write_text(
                'class WorkflowTester:\n'
                '    async def test_demo(self): pass\n'
                'def helper():\n'
                '    def test_nested(): pass\n'
                'def test_module(): pass\n'
                'class TestComponent:\n'
                '    def test_method(self): pass\n'
            )
            self.assertEqual(rt.count_test_callables(source), 2)

    def test_nonstandard_class_with_base_is_not_silently_omitted(self):
        """Real: unittest or unknown base classes remain omission candidates."""
        with tempfile.TemporaryDirectory(dir=SCRATCH, prefix='rt-callables-') as tmp:
            source = Path(tmp) / 'test_component.py'
            source.write_text(
                'class ComponentChecks(TestCase):\n    def test_method(self): pass\n'
            )
            self.assertEqual(rt.count_test_callables(source), 1)


class MockedDockerTests(unittest.TestCase):
    """Mocked tests: run_cmd is replaced by FakeDocker. Not integration."""

    def setUp(self):
        """Build a disposable fixture worktree and an external output path."""
        self.tmp = Path(tempfile.mkdtemp(dir=SCRATCH, prefix='rt-mock-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = make_repo(self.tmp)
        self.out = self.tmp / 'out'

    def run_main(self, fake, *args):
        """Drive main() with the fake docker transport patched in."""
        argv = ['--repo', str(self.repo), '--output', str(self.out), *map(str, args)]
        with mock.patch.object(rt, 'run_cmd', fake):
            return rt.main(argv)

    # --- regression: audit defect 1 (stdout forgery / report authority) ---

    def test_forged_control_frames_never_rewrite_recorded_outcomes(self):
        """Mocked regression 1: forged frames in exec stdout are ignored.

        Host-captured node lines and real exec return codes are the only
        truth; forged collection/report frames claim one node and exit 0.
        """
        fake = FakeDocker(
            forged_collect=forged_collection_frame(node_claim, 0),
            forged_exec=forged_reports_frame(0),
            exec_exit=5,
        )
        rc = self.run_main(fake)
        self.assertEqual(rc, 5)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['node_total'], 2)
        nodes = (self.out / 'collected_nodes.txt').read_text()
        self.assertEqual(len([n for n in nodes.splitlines() if n]), 2)
        self.assertNotIn('test_forged', nodes)
        self.assertEqual([p['exit_code'] for p in summary['phases']], [0, 0, 5])

    def test_execution_cannot_forge_the_collection_log(self):
        """Mocked regression 1: the collect log is the host capture."""
        fake = FakeDocker()
        rc = self.run_main(fake)
        self.assertEqual(rc, 0)
        captured = (self.out / 'collect.log').read_text()
        self.assertEqual(captured, fake._collect_stdout())
        self.assertEqual(fake.cp_targets, ['junit.xml'])

    def test_container_generated_metadata_is_never_authoritative(self):
        """Mocked regression 3: only junit.xml may cross the boundary.

        outcomes.json, collected_nodes.txt and control frames written by the
        container are never imported and never override phase results.
        """
        fake = FakeDocker(
            forged_collect=forged_collection_frame(node_claim, 0), exec_exit=4
        )
        rc = self.run_main(fake)
        self.assertEqual(rc, 4)
        self.assertEqual(fake.cp_targets, ['junit.xml'])
        outcomes = json.loads((self.out / 'outcomes.json').read_text())
        self.assertEqual([p['exit_code'] for p in outcomes['phases']], [0, 0, 4])
        self.assertEqual(outcomes['pytest_exit'], 4)

    # --- regression: audit defect 2 (WrongStartExit) ---

    def test_exec_transport_status_overrides_claimed_outcome(self):
        """Mocked regression 2: a real nonzero phase exit is authoritative.

        Transport/phase status 7 while every container-claimed outcome says
        0 must return 7, never 0.
        """
        fake = FakeDocker(
            forged_collect=forged_collection_frame(node_claim, 0),
            forged_exec=forged_reports_frame(0),
            exec_exit=7,
        )
        rc = self.run_main(fake)
        self.assertEqual(rc, 7)

    def test_reported_success_cannot_hide_a_failed_phase(self):
        """Mocked: a failed collect phase survives later success."""
        fake = FakeDocker(collect_exit=2)
        rc = self.run_main(fake)
        self.assertEqual(rc, 2)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual([p['exit_code'] for p in summary['phases']], [0, 2, 0])

    def test_preparation_phase_failure_is_authoritative(self):
        """Mocked: root preparation runs as a real phase with a real exit."""
        fake = FakeDocker(prepare_exit=9)
        rc = self.run_main(fake)
        self.assertEqual(rc, 9)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['phases'][0]['name'], 'prepare')
        self.assertEqual(summary['phases'][0]['exit_code'], 9)

    # --- regression: audit defect 4 (InvalidCreationId) ---

    def test_malformed_create_token_never_reaches_rm_or_inspect(self):
        """Mocked regression 4: literal format validation is preserved.

        A malformed creation token is never passed to rm or inspect; the ID
        is resolved from an owned exact-name inspect only.
        """
        fake = FakeDocker(create_id='not-a-valid-token', doc_id='d' * 64)
        rc = self.run_main(fake)
        self.assertEqual(rc, 3)
        targets = [call[-1] for call in fake.calls if call[3] in ('inspect', 'rm')]
        self.assertNotIn('not-a-valid-token', targets)
        for target in fake.rm_calls:
            self.assertTrue(HEX64.match(target), target)
        self.assertEqual(fake.rm_calls, ['d' * 64])

    def test_malformed_creation_is_recorded_as_error_when_cleanup_succeeds(self):
        """Mocked regression 4: cleanup success never erases the error."""
        fake = FakeDocker(create_id='not-a-valid-token', doc_id='d' * 64)
        rc = self.run_main(fake)
        self.assertEqual(rc, 3)
        self.assertTrue(fake.rm_calls)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(
            any('malformed' in f for f in summary['failures']), summary['failures']
        )

    def test_cleanup_refuses_containers_without_owned_identity(self):
        """Mocked: cleanup deletes only the owned name+label match."""
        fake = FakeDocker(identity_drift=True)
        rc = self.run_main(fake)
        self.assertEqual(rc, 1)
        self.assertEqual(fake.rm_calls, [])
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(
            any('cleanup refused' in f for f in summary['failures']),
            summary['failures'],
        )

    def test_preexisting_container_name_blocks_creation(self):
        """Mocked: an occupied owned name is never adopted or overwritten."""
        fake = FakeDocker(name_taken=True)
        rc = self.run_main(fake)
        self.assertEqual(rc, 3)
        create_calls = [c for c in fake.calls if c[3] == 'create']
        self.assertEqual(create_calls, [])
        self.assertEqual(fake.rm_calls, [])

    # --- junit is evidence, never authority ---

    def test_junit_failure_rows_cannot_claim_success(self):
        """Mocked: junit failure rows contradicting exit 0 fail the run."""
        junit = (
            '<?xml version="1.0"?><testsuites><testsuite failures="1">'
            '<testcase classname="core.tests.test_alpha" name="test_one">'
            '<failure message="boom"/></testcase>'
            '</testsuite></testsuites>'
        )
        fake = FakeDocker(junit=junit)
        rc = self.run_main(fake)
        self.assertEqual(rc, 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(
            any('inconsistency' in f for f in summary['failures']), summary['failures']
        )

    def test_clean_junit_cannot_hide_a_nonzero_phase_exit(self):
        """Mocked: a clean junit never rewrites a real nonzero exit."""
        fake = FakeDocker(exec_exit=1)
        rc = self.run_main(fake)
        self.assertEqual(rc, 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(
            any('inconsistency' in f for f in summary['failures']), summary['failures']
        )

    def test_junit_import_falls_back_to_host_orchestrated_read(self):
        """Mocked: a tmpfs-blind docker cp still imports the artifact.

        The fallback is a bounded host-supplied read of exactly the one
        allowlisted artifact, recorded truthfully in the reports.
        """
        fake = FakeDocker(cp_broken=True)
        rc = self.run_main(fake)
        self.assertEqual(rc, 0)
        self.assertTrue((self.out / 'junit.xml').exists())
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['reports_copied'], ['junit.xml'])
        self.assertEqual(
            summary['junit_import']['method'], 'host-orchestrated exec read'
        )

    # --- sandbox posture ---

    def test_baseline_volume_is_read_only_and_runtime_is_ephemeral(self):
        """Real argv: tests cannot mutate the reusable baseline."""
        argv = rt.build_create_argv(
            self.repo, rt.DEFAULT_IMAGE, rt.DEFAULT_VOLUME, 'run-test', 'pass'
        )
        self.assertIn(rt.DEFAULT_VOLUME + ':/scratch:ro', argv)
        self.assertIn('--tmpfs', argv)
        self.assertIn('/runs:rw,noexec,nosuid,nodev,size=536870912', argv)
        self.assertIn('/deps:rw,exec,nosuid,nodev,size=1073741824', argv)

    def test_writable_baseline_mount_is_rejected(self):
        """Mocked inspect: a writable dependency carrier is not accepted."""
        doc = FakeDocker()._default_doc()
        doc[0]['Mounts'][1]['RW'] = True
        problems, _details = rt.verify_container(doc, rt.DEFAULT_VOLUME)
        self.assertTrue(any('read-only' in issue for issue in problems))

    def test_create_argv_is_hardened(self):
        """Real argv: container command enforces the required sandbox."""
        argv = rt.build_create_argv(
            repo=self.repo,
            image=rt.DEFAULT_IMAGE,
            volume=rt.DEFAULT_VOLUME,
            run_id='run-abc',
            keeper='pass',
        )
        joined = ' '.join(argv)
        self.assertIn('--pull=never', joined)
        self.assertIn('--network none', joined)
        self.assertIn('--read-only', joined)
        self.assertIn('--cap-drop ALL', joined)
        self.assertIn('--security-opt no-new-privileges', joined)
        self.assertIn(f'{self.repo.resolve()}:/repo:ro', joined)
        self.assertIn(f'{rt.DEFAULT_VOLUME}:/scratch:ro', joined)
        self.assertEqual(joined.count(' -v '), 2)
        self.assertIn(rt.DEFAULT_IMAGE, joined)
        self.assertIn('--context default', joined)
        self.assertIn('--name aimms-run-abc', joined)
        self.assertIn(f'--label {rt.OWNERSHIP_LABEL}=run-abc', joined)
        self.assertNotIn('bash', joined)
        self.assertNotIn('pip ', joined)
        self.assertNotIn('docker.sock', joined)
        self.assertNotIn('--cap-add', joined)

    def test_exec_argv_pins_the_unprivileged_test_user(self):
        """Real argv: pytest phases run as nobody, never as keeper root."""
        argv = rt.build_exec_argv(
            'aimms-run-abc', rt.DEFAULT_INTERPRETER, 'pass', 'collect'
        )
        joined = ' '.join(argv)
        self.assertIn('--user 65534:65534', joined)
        self.assertIn('--context default', joined)
        self.assertIn('exec', argv)
        self.assertNotIn('--privileged', joined)

    def test_security_mismatch_fails_the_run(self):
        """Mocked: a container that lost its sandbox fails verification."""
        doc = FakeDocker()._default_doc()
        doc[0]['HostConfig']['NetworkMode'] = 'host'
        rc = self.run_main(FakeDocker(inspect_doc=doc))
        self.assertEqual(rc, 3)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(any('security' in f for f in summary['failures']))

    def test_environment_secrets_never_reach_reports(self):
        """Mocked: inspect Config.Env content is never written to disk."""
        fake = FakeDocker()
        self.run_main(fake)
        for path in self.out.rglob('*'):
            if path.is_file():
                self.assertNotIn('hunter2', path.read_text())
                self.assertNotIn('SECRET_TOKEN', path.read_text())

    # --- cleanup ownership ---

    def test_only_exact_created_container_is_removed(self):
        """Mocked: cleanup removes exactly the owned resolved container.

        Shared volumes and other containers are never touched.
        """
        fake = FakeDocker()
        self.run_main(fake)
        self.assertEqual(fake.rm_calls, [fake.doc_id])
        for call in fake.calls:
            joined = ' '.join(call)
            self.assertNotIn('volume rm', joined)
            self.assertNotIn('rmi ', joined)
            self.assertNotIn('prune', joined)

    def test_timeout_still_removes_container_and_fails(self):
        """Mocked: a phase timeout keeps reports and removes the container."""
        fake = FakeDocker(exec_timeout=True)
        rc = self.run_main(fake)
        self.assertEqual(rc, 3)
        self.assertEqual(fake.rm_calls, [fake.doc_id])
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(any('timeout' in f for f in summary['failures']))

    def test_timeout_removal_can_stop_the_running_owned_container(self):
        """Mocked: rm -f kills the owned container and its descendants."""
        fake = FakeDocker(exec_timeout=True)
        self.assertEqual(self.run_main(fake), 3)
        removals = [call for call in fake.calls if call[3] == 'rm']
        self.assertEqual(
            removals, [['docker', '--context', 'default', 'rm', '-f', fake.doc_id]]
        )

    def test_missing_image_fails_without_any_pull(self):
        """Mocked: a missing pinned image is an error; no pull is attempted."""
        fake = FakeDocker(missing_image=True)
        rc = self.run_main(fake)
        self.assertEqual(rc, 3)
        for call in fake.calls:
            self.assertNotIn('pull', call)
            self.assertNotIn('run', call[3:4])

    # --- accounting and reporting semantics ---

    def test_successful_full_run_accounts_for_every_source_file(self):
        """Mocked: happy path exit 0 with full per-file accounting."""
        fake = FakeDocker()
        rc = self.run_main(fake)
        self.assertEqual(rc, 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        accounting = summary['source_accounting']
        self.assertEqual(accounting['discovered'], 3)
        self.assertEqual(accounting['collected_files'], 2)
        self.assertEqual(accounting['empty_files'], ['test_empty.py'])
        self.assertEqual(accounting['omitted_files'], [])
        self.assertEqual(summary['node_total'], 2)

    def test_pytest_failure_exit_code_is_propagated(self):
        """Mocked: real pytest failure status is returned, not zero."""
        rc = self.run_main(FakeDocker(exec_exit=7))
        self.assertEqual(rc, 7)

    def test_missing_required_report_is_a_failure(self):
        """Mocked: incomplete report sets fail the run."""
        rc = self.run_main(FakeDocker(omit=('junit.xml',)))
        self.assertEqual(rc, 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(
            any('junit' in f for f in summary['failures']), summary['failures']
        )

    def test_source_accounting_omission_is_a_failure(self):
        """Mocked: a discoverable test file that never collected is a failure."""
        fake = FakeDocker(nodes={'test_alpha.py': ['test_one']})
        rc = self.run_main(fake)
        self.assertEqual(rc, 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertIn('test_missing.py', summary['source_accounting']['omitted_files'])

    def test_free_text_and_unrelated_error_cannot_excuse_an_omission(self):
        """Mocked: zero phase exits and benign log text cannot hide a source."""
        variants = (
            'Diagnostic: test_missing.py.unexpected\nUnrelated error word\n',
            f'Diagnostic: {CONTAINER_TESTS}/test_missing.py\n'
            'Unrelated test_error_marker\n',
        )
        for index, text in enumerate(variants):
            with self.subTest(text=text):
                self.out = self.tmp / f'free-text-{index}'
                fake = FakeDocker(
                    nodes={'test_alpha.py': ['test_one']}, forged_collect=text
                )
                self.assertEqual(self.run_main(fake), 1)
                summary = json.loads((self.out / 'summary.json').read_text())
                self.assertTrue(all(p['exit_code'] == 0 for p in summary['phases']))
                self.assertEqual(summary['junit_rows']['normal'], 1)
                self.assertEqual(
                    summary['source_accounting']['statuses']['test_missing.py'],
                    'omission',
                )
                self.assertEqual(fake.rm_calls, [fake.doc_id])

    def test_parameter_error_text_cannot_excuse_an_omission(self):
        """Mocked: matching collection/JUnit plus error-named params still fail."""
        fake = FakeDocker(
            nodes={'test_alpha.py': ['test_one[test_missing.py_error_marker]']}
        )
        self.assertEqual(self.run_main(fake), 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['junit_import']['reconciliation']['problems'], [])
        self.assertTrue(all(p['exit_code'] == 0 for p in summary['phases']))
        self.assertEqual(
            summary['source_accounting']['omitted_files'], ['test_missing.py']
        )
        self.assertEqual(fake.rm_calls, [fake.doc_id])

    def test_empty_file_is_explicit_not_an_omission(self):
        """Mocked: files with no test callables are recorded as empty."""
        fake = FakeDocker()
        self.run_main(fake)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['source_accounting']['empty_files'], ['test_empty.py'])

    def test_collection_skip_is_reported_not_fabricated(self):
        """Mocked: junit collection errors are surfaced in accounting."""
        junit = (
            '<?xml version="1.0"?><testsuites><testsuite errors="1">'
            '<testcase classname="" name="core.tests.test_missing">'
            '<error message="boom">'
            f'{CONTAINER_TESTS}/test_missing.py</error></testcase>'
            '</testsuite></testsuites>'
        )
        fake = FakeDocker(
            nodes={'test_alpha.py': ['test_one']}, junit=junit, exec_exit=2
        )
        rc = self.run_main(fake)
        self.assertNotEqual(rc, 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        statuses = summary['source_accounting']['statuses']
        self.assertEqual(statuses['test_missing.py'], 'collection-error')

    def test_zero_collection_is_a_failure(self):
        """Mocked: unexpectedly empty collection fails the run."""
        rc = self.run_main(FakeDocker(nodes={}))
        self.assertEqual(rc, 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(any('zero' in f for f in summary['failures']))

    def test_unmatched_skipped_row_is_not_a_collection_escape(self):
        """Mocked: an unrelated skipped row is still an extra identity."""
        fake = FakeDocker()
        fake.junit = fake.junit.replace(
            '</testsuite>',
            '<testcase classname="core.tests.test_other" name="test_not_collected">'
            '<skipped/></testcase></testsuite>',
        )
        self.assertEqual(self.run_main(fake), 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(any('collection diagnostic' in f for f in summary['failures']))

    def test_source_attributed_module_skip_is_disclosed_separately(self):
        """Mocked: the real pytest module-skip identity remains supported."""
        junit = (
            '<testsuites><testsuite>'
            '<testcase classname="core.tests.test_alpha" name="test_one"/>'
            '<testcase classname="" name="core.tests.test_missing">'
            '<skipped message="collection skipped">'
            f"('{CONTAINER_TESTS}/test_missing.py', 2, 'Skipped: synthetic')"
            '</skipped></testcase></testsuite></testsuites>'
        )
        fake = FakeDocker(nodes={'test_alpha.py': ['test_one']}, junit=junit)
        self.assertEqual(self.run_main(fake), 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['junit_rows']['normal'], 1)
        self.assertEqual(summary['junit_rows']['collection'], 1)
        self.assertEqual(
            summary['source_accounting']['statuses']['test_missing.py'],
            'collection-skip',
        )
        self.assertEqual(summary['junit_import']['reconciliation']['observed_cases'], 1)

    def test_module_diagnostic_requires_present_classname(self):
        """Mocked: an absent classname is not an explicitly empty identity."""
        junit = (
            '<testsuites><testsuite>'
            '<testcase classname="core.tests.test_alpha" name="test_one"/>'
            '<testcase name="core.tests.test_missing"><skipped>'
            f"('{CONTAINER_TESTS}/test_missing.py', 2, 'Skipped: synthetic')"
            '</skipped></testcase></testsuite></testsuites>'
        )
        fake = FakeDocker(nodes={'test_alpha.py': ['test_one']}, junit=junit)
        self.assertEqual(self.run_main(fake), 1)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(any('classname attribute' in f for f in summary['failures']))

    def test_module_diagnostic_requires_an_exact_source_path_token(self):
        """Mocked: a source substring inside a longer path is not attribution."""
        variants = [
            ('', '.unexpected'),
            ('', '_extra'),
            ('', '/child'),
            ('', '?other'),
            ('', '-suffix'),
            ('', '%2Eextra'),
            ('/unexpected', ''),
            ('other', ''),
            ('?', ''),
            ('\\', ''),
        ]
        for index, (prefix, suffix) in enumerate(variants):
            with self.subTest(prefix=prefix, suffix=suffix):
                self.out = self.tmp / f'out-{index}'
                junit = (
                    '<testsuites><testsuite>'
                    '<testcase classname="core.tests.test_alpha" name="test_one"/>'
                    '<testcase classname="" name="core.tests.test_missing"><skipped>'
                    f"('{prefix}{CONTAINER_TESTS}/test_missing.py{suffix}', 2, "
                    "'Skipped: synthetic')"
                    '</skipped></testcase></testsuite></testsuites>'
                )
                fake = FakeDocker(nodes={'test_alpha.py': ['test_one']}, junit=junit)
                self.assertEqual(self.run_main(fake), 1)
                summary = json.loads((self.out / 'summary.json').read_text())
                self.assertTrue(
                    any('collection diagnostic' in f for f in summary['failures'])
                )

    def test_normal_skip_cannot_hide_a_source_omission(self):
        """Mocked: another case's skip text is not a module diagnostic."""
        for index, suffix in enumerate(['', '.unexpected']):
            with self.subTest(suffix=suffix):
                self.out = self.tmp / f'out-{index}'
                junit = (
                    '<testsuites><testsuite>'
                    '<testcase classname="core.tests.test_alpha" name="test_one">'
                    f'<skipped>{CONTAINER_TESTS}/test_missing.py{suffix}</skipped>'
                    '</testcase></testsuite></testsuites>'
                )
                fake = FakeDocker(nodes={'test_alpha.py': ['test_one']}, junit=junit)
                self.assertEqual(self.run_main(fake), 1)
                summary = json.loads((self.out / 'summary.json').read_text())
                self.assertIn(
                    'test_missing.py', summary['source_accounting']['omitted_files']
                )
                self.assertEqual(
                    summary['source_accounting']['statuses']['test_missing.py'],
                    'omission',
                )

    def test_collected_skips_use_the_reconciled_summary_counts(self):
        """Mocked: skipped collected cases are normal rows, not collection rows."""
        junit = (
            '<testsuites><testsuite>'
            '<testcase classname="core.tests.test_alpha" name="test_one"><skipped/></testcase>'
            '<testcase classname="core.tests.test_missing" name="test_two"><skipped/></testcase>'
            '</testsuite></testsuites>'
        )
        self.assertEqual(self.run_main(FakeDocker(junit=junit)), 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(
            summary['junit_rows'],
            {
                'normal': 2,
                'subtest': 0,
                'skipped': 2,
                'error': 0,
                'failure': 0,
                'collection': 0,
            },
        )
        self.assertEqual(
            summary['junit_rows']['normal'],
            summary['junit_import']['reconciliation']['observed_cases'],
        )

    def test_subtest_rows_are_not_equated_with_node_totals(self):
        """Mocked: junit subtest rows are reported separately from nodes."""
        junit = (
            '<?xml version="1.0"?><testsuites><testsuite>'
            '<testcase classname="core.tests.test_alpha" name="test_one"/>'
            '<testcase classname="core.tests.test_alpha" '
            'name="test_one (Subtest: 0)"/>'
            '<testcase classname="core.tests.test_alpha" '
            'name="test_one (Subtest: 1)"/>'
            '<testcase classname="core.tests.test_missing" name="test_two"/>'
            '</testsuite></testsuites>'
        )
        fake = FakeDocker(junit=junit)
        rc = self.run_main(fake)
        self.assertEqual(rc, 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['junit_rows']['normal'], 2)
        self.assertEqual(summary['junit_rows']['subtest'], 2)
        self.assertEqual(summary['node_total'], 2)
        self.assertIn('not equal', summary['junit_note'])

    def test_collect_only_mode_is_explicitly_labelled(self):
        """Mocked: --collect-only is labelled and never called a full run."""
        fake = FakeDocker()
        rc = self.run_main(fake, '--collect-only')
        self.assertEqual(rc, 0)
        manifest = json.loads((self.out / 'manifest.json').read_text())
        self.assertEqual(manifest['mode'], 'collect-only')
        self.assertFalse(manifest['labels']['full_suite'])
        names = [p['name'] for p in manifest['phases']]
        self.assertNotIn('execute', names)

    def test_selected_mode_is_never_labelled_full(self):
        """Mocked: selective runs are labelled selected, not full."""
        rc = self.run_main(FakeDocker(), '--test', 'test_alpha.py')
        self.assertEqual(rc, 0)
        manifest = json.loads((self.out / 'manifest.json').read_text())
        self.assertEqual(manifest['mode'], 'selected')
        self.assertFalse(manifest['labels']['full_suite'])
        self.assertEqual(manifest['labels']['selected'], ['test_alpha.py'])

    def test_selected_run_does_not_require_unselected_sources(self):
        """Mocked: honest selected-node reports exclude unselected files."""
        fake = FakeDocker(nodes={'test_alpha.py': ['test_one']})
        rc = self.run_main(fake, '--test', 'test_alpha.py')
        self.assertEqual(rc, 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        accounting = summary['source_accounting']
        self.assertEqual(accounting['discovered'], 1)
        self.assertEqual(accounting['omitted_files'], [])
        self.assertEqual(
            accounting['not_selected_files'], ['test_empty.py', 'test_missing.py']
        )

    def test_source_file_list_is_recorded_sorted(self):
        """Mocked: the manifest records the sorted discovered source list."""
        self.run_main(FakeDocker())
        manifest = json.loads((self.out / 'manifest.json').read_text())
        self.assertEqual(
            manifest['source_files'],
            ['test_alpha.py', 'test_empty.py', 'test_missing.py'],
        )

    def test_reports_are_copied_from_allowlist_only(self):
        """Mocked: only the allowlisted junit.xml is copied out."""
        fake = FakeDocker()
        self.run_main(fake)
        copied = [Path(c[5]).name for c in fake.calls if c[3] == 'cp']
        self.assertEqual(copied, ['junit.xml'])
        self.assertEqual(fake.cp_targets, ['junit.xml'])
        self.assertTrue((self.out / 'junit.xml').exists())

    def test_collection_record_is_written_before_execution(self):
        """Mocked: host records collection state before candidate execution."""
        fake = FakeDocker()
        self.run_main(fake)
        evidence = json.loads((self.out / 'collection-evidence.json').read_text())
        self.assertTrue(evidence['recorded_before_execution'])
        self.assertEqual(evidence['recorded_by'], 'host-captured docker exec')
        exec_calls = [
            c for c in fake.calls if c[3] == 'exec' and '--junitxml=' in c[-1]
        ]
        self.assertEqual(len(exec_calls), 1)

    # --- follow-up audit defects: junit structure, node/execution
    #     reconciliation, exact owned id pinning, atomic output ownership
    #     and honest trust labels ---

    def test_empty_junit_report_is_rejected(self):
        """Mocked: an empty junit artifact is never accepted as clean."""
        fake = FakeDocker(junit='')
        rc = self.run_main(fake)
        self.assertNotEqual(rc, 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(
            any('junit' in failure.lower() for failure in summary['failures']),
            summary['failures'],
        )

    def test_junit_without_executed_cases_is_rejected(self):
        """Mocked: structurally valid junit with zero cases is not success."""
        junit = (
            '<?xml version="1.0"?><testsuites>'
            '<testsuite tests="0"></testsuite></testsuites>'
        )
        fake = FakeDocker(junit=junit)
        self.assertNotEqual(self.run_main(fake), 0)

    def test_junit_rows_must_reconcile_with_collected_nodes(self):
        """Mocked: one executed case cannot stand in for two claimed nodes."""
        junit = (
            '<?xml version="1.0"?><testsuites><testsuite>'
            '<testcase classname="core.tests.test_alpha" name="test_one"/>'
            '</testsuite></testsuites>'
        )
        fake = FakeDocker(junit=junit)
        rc = self.run_main(fake)
        self.assertNotEqual(rc, 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(
            any('reconcil' in failure.lower() for failure in summary['failures']),
            summary['failures'],
        )

    def test_same_count_wrong_identity_fails_reconciliation(self):
        """Mocked: equal totals with a wrong node identity must fail."""
        junit = (
            '<?xml version="1.0"?><testsuites><testsuite>'
            '<testcase classname="core.tests.test_alpha" name="test_one"/>'
            '<testcase classname="core.tests.test_alpha" name="test_two"/>'
            '</testsuite></testsuites>'
        )
        fake = FakeDocker(junit=junit)
        self.assertNotEqual(self.run_main(fake), 0)

    def test_parameter_suffix_with_colons_maps_verbatim(self):
        """Mocked: literal '::' inside parameter values is never split."""
        name = 'test_local_identity_endpoint_is_allowed_without_dns[[::1]]'
        junit = (
            '<?xml version="1.0"?><testsuites><testsuite>'
            f'<testcase classname="core.tests.test_alpha" name="{name}"/>'
            '<testcase classname="core.tests.test_missing" name="test_two"/>'
            '</testsuite></testsuites>'
        )
        fake = FakeDocker(
            nodes={'test_alpha.py': [name], 'test_missing.py': ['test_two']},
            junit=junit,
        )
        self.assertEqual(self.run_main(fake), 0)

    def test_collection_observation_is_never_trusted_as_verified(self):
        """Mocked: candidate observations keep honest, unauthenticated labels."""
        fake = FakeDocker()
        self.assertEqual(self.run_main(fake), 0)
        summary = json.loads((self.out / 'summary.json').read_text())
        verification = summary['collection_verification']
        self.assertFalse(verification.get('verified'))
        self.assertFalse(verification.get('authenticated'))
        self.assertTrue(verification.get('consistency_checked'))
        self.assertIn('candidate', verification.get('provenance', ''))
        self.assertIn('reviewed', (rt.__doc__ or '').lower())

    def test_create_and_inspect_id_mismatch_aborts_before_start_or_exec(self):
        """Mocked: create id c vs inspect id d aborts before start/exec."""
        fake = FakeDocker(create_id='c' * 64, doc_id='d' * 64)
        rc = self.run_main(fake)
        self.assertNotEqual(rc, 0)
        self.assertEqual([c for c in fake.calls if c[3] == 'start'], [])
        self.assertEqual([c for c in fake.calls if c[3] == 'exec'], [])
        self.assertEqual(fake.rm_calls, [])

    def test_cleanup_refuses_a_replacement_container_with_the_same_name(self):
        """Mocked: a swapped replacement id with same name/label is kept."""
        fake = FakeDocker(later_doc_id='d' * 64)
        rc = self.run_main(fake)
        self.assertNotEqual(rc, 0)
        self.assertEqual(fake.rm_calls, [])
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertTrue(
            any('cleanup refused' in failure for failure in summary['failures']),
            summary['failures'],
        )

    def test_output_directory_raced_into_existence_is_never_written(self):
        """Mocked race: a raced output directory stays byte-for-byte intact."""
        self.out.mkdir(parents=True)
        sentinel = '{"sentinel": true}\n'
        (self.out / 'summary.json').write_text(sentinel)
        with mock.patch.object(rt, 'validate_output_dir', lambda raw, repo: Path(raw)):
            rc = self.run_main(FakeDocker())
        self.assertNotEqual(rc, 0)
        self.assertEqual((self.out / 'summary.json').read_text(), sentinel)
        self.assertEqual(
            sorted(path.name for path in self.out.iterdir()), ['summary.json']
        )


class NodeIdentityMappingTests(unittest.TestCase):
    """Real mapping contract between recorded nodes and junit identities."""

    def test_parameter_suffix_with_colons_is_reattached_verbatim(self):
        """Real: partition at the first '[' before any '::' split."""
        self.assertEqual(
            rt.node_case_identity(
                'core/tests/test_egress.py::'
                'test_local_identity_endpoint_is_allowed_without_dns[[::1]]'
            ),
            (
                'core.tests.test_egress',
                'test_local_identity_endpoint_is_allowed_without_dns[[::1]]',
            ),
        )

    def test_class_and_parameter_identities_map_exactly(self):
        """Real: class components join the classname; params stay on name."""
        self.assertEqual(
            rt.node_case_identity(
                'core/tests/test_forms.py::TestWizard::test_submit[[a::b]]'
            ),
            ('core.tests.test_forms.TestWizard', 'test_submit[[a::b]]'),
        )
        self.assertEqual(
            rt.node_case_identity(
                '/repo/src/backend/InvenTree/ai/core/tests/test_x.py::test_plain'
            ),
            ('core.tests.test_x', 'test_plain'),
        )


if __name__ == '__main__':
    unittest.main()
