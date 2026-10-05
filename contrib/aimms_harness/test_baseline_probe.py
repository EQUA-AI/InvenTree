"""Tests for contrib/aimms_harness/baseline_probe.py (H0 static baseline inventory).

Each test builds a real disposable fixture tree under TMPDIR scratch and drives
the probe only through its documented CLI contract. No application code is
imported or executed.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROBE = Path(__file__).resolve().parent / 'baseline_probe.py'
SCRATCH = Path(os.environ.get('TMPDIR', '/home/lokesh/.hermes/cache/scratch'))

FIXTURE_FACTORY = '''"""Fixture agent factory."""
from agent_framework import ChatAgent
from ai.core.integrations.azure_openai_client import build_chat_client


class AgentSpec:
    pass


def build_agent(spec):
    client = build_chat_client("gpt-4o")
    return AgentSpec(workflow_id="wf1")
'''

FIXTURE_RBAC_RUN = '''"""Fixture rbac run."""
from ai.core.tools.invocation_guard import bind_capability_run


async def run_with_rbac(client, ctx):
    bind_capability_run(ctx)
    return await client.run("wf8")
'''

FIXTURE_GUARD = '''"""Fixture invocation guard."""


def bind_capability_run(ctx):
    return ctx


async def authorize_invocation(call):
    return True
'''

FIXTURE_REGISTRY = '''"""Fixture workflow registry."""
from enum import Enum


class WorkflowTier(Enum):
    T1_SINGLE_AGENT = "t1"
    T6_MAGENTIC = "t6"


WORKFLOW_ID_ALIASES: dict = {
    "wf1_diagnostics": "wf1",
}


class WorkflowDefinition:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def register(definition):
    return definition


def build_lookup(ctx):
    return ctx


def build_parts(ctx):
    return ctx


def _register_default_workflows():
    register(WorkflowDefinition(workflow_id="wf8", name="Lookup", description="Fast lookup", tier=WorkflowTier.T1_SINGLE_AGENT, builder=build_lookup, cacheable=True, requires_confirmation=False, tags=["read-only", "fast"]))
    register(WorkflowDefinition(workflow_id="wf2", name="Parts", description="Parts analysis", tier=WorkflowTier.T6_MAGENTIC, builder=build_parts, cacheable=False, requires_confirmation=True, tags=["analysis"]))
'''

FIXTURE_ASGI = '''"""Fixture ASGI entrypoint."""
application = None
'''

FIXTURE_PYPROJECT = """[project]
name = "aimms-ai-fixture"
dependencies = [
    "agent-framework-core==1.0.0b251120",
    "agent-framework-devui==1.0.0b251120",
]
"""

FIXTURE_OVERLAY = """# WARNING: isolated migration overlay - do not merge into base locks
agent-framework-core==1.17.0
agent-framework-openai==1.14.2
agent-framework-foundry==1.12.0
agent-framework-orchestrations==1.1.1
"""

FIXTURE_REQ_IN = """django>=4.2
agent-framework-core==1.0.0b251120
agent-framework-devui==1.0.0b251120
openai==3.18.0
"""

FIXTURE_REQ_LOCK = """agent-framework-core==1.0.0b251120 \\
    --hash=sha256:0000
django==4.2.0 \\
    --hash=sha256:1111
"""

FIXTURE_REQ_LOCK_314 = """agent-framework-core==1.0.0b251120 \\
    --hash=sha256:2222
django==4.2.0 \\
    --hash=sha256:3333
"""

FIXTURE_AI_REQ = """# fixture ai service lock
agent-framework-core==1.0.0b251120
agent-framework-devui==1.0.0b251120
openai==3.18.0
"""

FIXTURE_DOCKERFILE = """# fixture dockerfile
FROM python:3.14.7-slim-trixie@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS inventree_base
RUN true
FROM inventree_base AS production
"""

FIXTURE_ROOT = '''"""Fixture workflow root."""
import agent_framework.azure


async def start(client):
    await run_with_rbac(client, {})
    return client.run_stream("x")
'''

FIXTURE_TEST_FILE = '''"""Fixture test file."""
import agent_framework


def test_sites(client):
    build_agent(client)
    run()
    bind_capability_run(client)
'''

FIXTURE_FILES = {
    'src/backend/InvenTree/ai/core/agents/factory.py': FIXTURE_FACTORY,
    'src/backend/InvenTree/ai/core/workflows/rbac_run.py': FIXTURE_RBAC_RUN,
    'src/backend/InvenTree/ai/core/tools/invocation_guard.py': FIXTURE_GUARD,
    'src/backend/InvenTree/ai/core/workflows/registry.py': FIXTURE_REGISTRY,
    'src/backend/InvenTree/ai/core/workflows/root.py': FIXTURE_ROOT,
    'src/backend/InvenTree/ai/core/tests/test_fixture_runtime.py': FIXTURE_TEST_FILE,
    'src/backend/InvenTree/ai/pyproject.toml': FIXTURE_PYPROJECT,
    'src/backend/InvenTree/ai/requirements.txt': FIXTURE_AI_REQ,
    'src/backend/InvenTree/ai/requirements-maf-migration.txt': FIXTURE_OVERLAY,
    'src/backend/requirements.in': FIXTURE_REQ_IN,
    'src/backend/requirements.txt': FIXTURE_REQ_LOCK,
    'src/backend/requirements-3.14.txt': FIXTURE_REQ_LOCK_314,
    'src/backend/InvenTree/InvenTree/asgi.py': FIXTURE_ASGI,
    'src/backend/InvenTree/aichat/api.py': '"""Fixture aichat."""\n',
    'src/backend/InvenTree/voice/api.py': '"""Fixture voice."""\n',
    'src/backend/InvenTree/approvals/api.py': '"""Fixture approvals."""\n',
    'src/backend/InvenTree/repair/api.py': '"""Fixture repair."""\n',
    'contrib/container/Dockerfile': FIXTURE_DOCKERFILE,
}


def line_of(text, marker):
    """Return 1-based line number of an exact marker line in fixture text."""
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln == marker]
    if len(idx) != 1:
        raise AssertionError(f'marker not uniquely found: {marker!r}')
    return idx[0] + 1


class ProbeTestCase(unittest.TestCase):
    """Shared fixture builders that drive the probe only through its CLI."""

    def setUp(self):
        """Create a disposable fixture tree under the scratch TMPDIR."""
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.assertNotIn(str(SCRATCH), ('/tmp', '/tmp/'))
        self.tmp = Path(tempfile.mkdtemp(dir=SCRATCH, prefix='aimms_probe_test_'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / 'cwd').mkdir()

    def git(self, repo, *args):
        """Run a git command inside a fixture repository, returning trimmed stdout."""
        result = subprocess.run(
            [
                'git',
                '-c',
                'user.name=fixture',
                '-c',
                'user.email=fixture@example.invalid',
                *args,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def build_fixture(
        self,
        *,
        branch='h0-fixture-branch',
        omit=(),
        extra=None,
        override=None,
        commit=True,
    ):
        """Create a disposable fixture git repository with baseline inventory inputs."""
        repo = self.tmp / 'repo'
        contents = dict(FIXTURE_FILES)
        contents.update(override or {})
        for rel, content in contents.items():
            if rel in omit:
                continue
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        for rel, content in (extra or {}).items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        if commit:
            self.git(repo, 'init', '-b', branch)
            self.git(repo, 'add', '-A')
            self.git(repo, 'commit', '-m', 'fixture baseline')
        return repo

    def run_probe(self, repo, output=None, cwd=None):
        """Run the probe CLI as a subprocess and return the completed process."""
        args = [sys.executable, str(PROBE), '--repo', str(repo)]
        if output is not None:
            args += ['--output', str(output)]
        env = dict(os.environ)
        env['TMPDIR'] = str(SCRATCH)
        return subprocess.run(
            args,
            cwd=str(cwd or (self.tmp / 'cwd')),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )


class TestCliContractAndGitBaseline(ProbeTestCase):
    """Slice 1: CLI contract, versioned JSON, evidence level, git baseline."""

    def test_requires_repo_argument(self):
        """--repo is required and usage errors exit 2."""
        result = subprocess.run(
            [sys.executable, str(PROBE)],
            cwd=str(self.tmp / 'cwd'),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('usage', result.stderr.lower())
        self.assertEqual(result.stdout, '')

    def test_stdout_report_has_schema_evidence_and_git_baseline(self):
        """Stdout report carries schema, evidence level and real git baseline."""
        repo = self.build_fixture()
        expected_head = self.git(repo, 'rev-parse', 'HEAD')
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['schema_id'], 'aimms-maf-baseline-inventory')
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(
            report['evidence_level'],
            'static/source-only; not live feature enablement or authorization proof',
        )
        self.assertEqual(report['git']['branch'], 'h0-fixture-branch')
        self.assertEqual(report['git']['head'], expected_head)
        self.assertRegex(report['git']['head'], re.compile(r'^[0-9a-f]{40,64}$'))

    def test_output_file_receives_same_report_as_stdout(self):
        """--output receives the same canonical JSON bytes as stdout mode."""
        repo = self.build_fixture()
        out = self.tmp / 'report.json'
        result = self.run_probe(repo, output=out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        on_disk = out.read_text()
        self.assertEqual(
            on_disk, json.dumps(json.loads(on_disk), indent=2, sort_keys=True) + '\n'
        )

    def test_fails_sanitized_when_repo_is_not_git(self):
        """A non-git repo fails with a sanitized git error, not a traceback."""
        repo = self.build_fixture(commit=False)
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('git', result.stderr.lower())
        self.assertNotIn('Traceback', result.stderr)


class TestRequiredInputsAndSafeFailures(ProbeTestCase):
    """Slice 2: missing required inputs, malformed source, unsafe inputs."""

    def test_missing_required_input_fails_sanitized(self):
        """Missing required inputs fail with a sanitized path error and no report."""
        repo = self.build_fixture(omit=('src/backend/requirements.in',))
        out = self.tmp / 'report.json'
        result = self.run_probe(repo, output=out)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('missing', result.stderr.lower())
        self.assertIn('src/backend/requirements.in', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(out.exists())

    def test_malformed_required_source_fails_sanitized(self):
        """Invalid syntax in required source fails without dumping source text."""
        repo = self.build_fixture(
            override={
                'src/backend/InvenTree/ai/core/workflows/registry.py': (
                    'ZZUNIQUE_MARKER = (\ndef broken(:\n    pass\n'
                )
            }
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('syntax', result.stderr.lower())
        self.assertIn('ai/core/workflows/registry.py', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('ZZUNIQUE_MARKER', result.stderr)

    def test_malformed_scanned_source_fails_sanitized(self):
        """Invalid syntax in any scoped source fails safely without source text."""
        repo = self.build_fixture(
            override={
                'src/backend/InvenTree/ai/core/workflows/root.py': 'ZZUNIQUE_TOKEN = (\n'
            }
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('syntax', result.stderr.lower())
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('ZZUNIQUE_TOKEN', result.stderr)

    def test_symlinked_required_input_is_rejected(self):
        """Symlinked required inputs are rejected as unsafe."""
        repo = self.build_fixture()
        target = self.tmp / 'requirements.in.copy'
        shutil.copy2(repo / 'src/backend/requirements.in', target)
        link = repo / 'src/backend/requirements.in'
        link.unlink()
        link.symlink_to(target)
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertRegex(result.stderr.lower(), r'symlink|unsafe')
        self.assertNotIn('Traceback', result.stderr)

    def test_symlinked_required_input_ancestor_is_rejected(self):
        """Symlinked ancestors of required inputs fail sanitized with no report."""
        repo = self.build_fixture()
        original = repo / 'src/backend'
        outside = self.tmp / 'external-backend'
        shutil.move(original, outside)
        original.symlink_to(outside, target_is_directory=True)
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('Traceback', result.stderr)
        self.assertRegex(result.stderr.lower(), r'symlink|unsafe')


class TestDependencyPinLedger(ProbeTestCase):
    """Slice 3: actual dependency pins, hashed locks, isolated migration overlay."""

    EXPECTED_FILES = [
        'src/backend/InvenTree/ai/pyproject.toml',
        'src/backend/InvenTree/ai/requirements-maf-migration.txt',
        'src/backend/InvenTree/ai/requirements.txt',
        'src/backend/requirements-3.14.txt',
        'src/backend/requirements.in',
        'src/backend/requirements.txt',
    ]

    def test_pin_ledger_records_actual_pins_and_overlay(self):
        """Ledger records actual pins, hashed-lock flags and the isolated overlay."""
        repo = self.build_fixture()
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        ledger = report['dependency_ledger']
        self.assertEqual(
            sorted(entry['path'] for entry in ledger['files']), self.EXPECTED_FILES
        )
        by_path = {entry['path']: entry for entry in ledger['files']}

        pyproject = by_path['src/backend/InvenTree/ai/pyproject.toml']
        self.assertEqual(pyproject['role'], 'pyproject')
        self.assertFalse(pyproject['hashed_lock'])
        self.assertEqual(
            pyproject['pins'],
            [
                {
                    'name': 'agent-framework-core',
                    'specifier': '==',
                    'version': '1.0.0b251120',
                },
                {
                    'name': 'agent-framework-devui',
                    'specifier': '==',
                    'version': '1.0.0b251120',
                },
            ],
        )

        overlay = by_path['src/backend/InvenTree/ai/requirements-maf-migration.txt']
        self.assertEqual(overlay['role'], 'migration-overlay')
        self.assertEqual(
            [(p['name'], p['version']) for p in overlay['pins']],
            [
                ('agent-framework-core', '1.17.0'),
                ('agent-framework-foundry', '1.12.0'),
                ('agent-framework-openai', '1.14.2'),
                ('agent-framework-orchestrations', '1.1.1'),
            ],
        )

        req_in = by_path['src/backend/requirements.in']
        self.assertEqual(req_in['role'], 'input')
        self.assertFalse(req_in['hashed_lock'])
        self.assertEqual(
            req_in['other_requirements'],
            [{'name': 'django', 'specifier': '>=', 'version': '4.2'}],
        )

        for rel in (
            'src/backend/requirements.txt',
            'src/backend/requirements-3.14.txt',
        ):
            lock = by_path[rel]
            self.assertEqual(lock['role'], 'hashed-lock')
            self.assertTrue(lock['hashed_lock'])
        self.assertEqual(
            by_path['src/backend/requirements.txt']['pins'],
            [
                {
                    'name': 'agent-framework-core',
                    'specifier': '==',
                    'version': '1.0.0b251120',
                },
                {'name': 'django', 'specifier': '==', 'version': '4.2.0'},
            ],
        )

        overlay_section = report['migration_overlay']
        self.assertEqual(
            overlay_section['path'],
            'src/backend/InvenTree/ai/requirements-maf-migration.txt',
        )
        self.assertEqual(overlay_section['disposition'], 'isolated-overlay')
        self.assertEqual(overlay_section['pins'], overlay['pins'])

        total = sum(len(entry['pins']) for entry in ledger['files'])
        self.assertEqual(ledger['exact_pin_count'], total)
        self.assertEqual(report['counts']['dependency_pins']['exact'], total)
        self.assertEqual(
            report['counts']['dependency_pins']['files'], len(ledger['files'])
        )

    def test_invalid_toml_fails_sanitized(self):
        """Invalid TOML in a dependency file fails with a sanitized error."""
        repo = self.build_fixture(
            override={'src/backend/InvenTree/ai/pyproject.toml': 'ZZUNIQUE_TOML = [\n'}
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('ai/pyproject.toml', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('ZZUNIQUE_TOML', result.stderr)

    def test_malformed_dependency_shapes_fail_sanitized(self):
        """Wrong TOML dependency shapes fail bounded, without traceback or values."""
        cases = {
            'project-not-table': 'project = 424246\n',
            'deps-not-list': '[project]\ndependencies = 424242\n',
            'deps-item-not-str': '[project]\ndependencies = ["ok-pkg==1.0", 424243]\n',
            'optional-not-table': '[project]\noptional-dependencies = 424244\n',
            'optional-group-not-list': '[project.optional-dependencies]\ngrp = 424245\n',
        }
        for label, text in cases.items():
            with self.subTest(label):
                repo = self.build_fixture(
                    override={'src/backend/InvenTree/ai/pyproject.toml': text}
                )
                result = self.run_probe(repo)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('Traceback', result.stderr)
                self.assertNotIn('4242', result.stderr)


class TestWorkflowRegistryInventory(ProbeTestCase):
    """Slice 4: literal registrations/metadata, aliases, unresolved markers."""

    def test_registrations_metadata_and_aliases(self):
        """Literal registrations, metadata tagging and aliases match the registry."""
        repo = self.build_fixture()
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        workflows = report['workflows']
        self.assertEqual(
            workflows['source'], 'src/backend/InvenTree/ai/core/workflows/registry.py'
        )
        registrations = workflows['registrations']
        self.assertEqual(
            [entry['workflow_id']['value'] for entry in registrations], ['wf8', 'wf2']
        )
        first = registrations[0]
        self.assertEqual(
            first['line'],
            line_of(
                FIXTURE_REGISTRY,
                '    register(WorkflowDefinition(workflow_id="wf8", name="Lookup", description="Fast lookup", tier=WorkflowTier.T1_SINGLE_AGENT, builder=build_lookup, cacheable=True, requires_confirmation=False, tags=["read-only", "fast"]))',
            ),
        )
        self.assertEqual(first['registered_via'], 'register')
        self.assertEqual(first['workflow_id'], {'kind': 'literal', 'value': 'wf8'})
        metadata = first['metadata']
        self.assertEqual(metadata['name'], {'kind': 'literal', 'value': 'Lookup'})
        self.assertEqual(
            metadata['description'], {'kind': 'literal', 'value': 'Fast lookup'}
        )
        self.assertEqual(
            metadata['tier'],
            {
                'kind': 'resolved-literal',
                'value': 't1',
                'via_symbol': 'WorkflowTier.T1_SINGLE_AGENT',
            },
        )
        self.assertEqual(metadata['builder'], {'kind': 'unresolved'})
        self.assertEqual(metadata['cacheable'], {'kind': 'literal', 'value': True})
        self.assertEqual(
            metadata['requires_confirmation'], {'kind': 'literal', 'value': False}
        )
        self.assertEqual(
            metadata['tags'], {'kind': 'literal', 'value': ['read-only', 'fast']}
        )
        self.assertEqual(
            workflows['aliases'],
            [
                {
                    'from': 'wf1_diagnostics',
                    'to': 'wf1',
                    'line': line_of(FIXTURE_REGISTRY, '    "wf1_diagnostics": "wf1",'),
                }
            ],
        )
        self.assertEqual(workflows['unresolved_values'], 2)
        self.assertEqual(report['counts']['workflow_registrations'], 2)
        self.assertEqual(report['counts']['workflow_aliases'], 1)

    def test_dynamic_expressions_marked_unresolved_not_guessed(self):
        """Dynamic expressions are marked unresolved and never guessed or executed."""
        dynamic_registry = (
            '"""Fixture dynamic registry."""\n'
            'WORKFLOW_ID_ALIASES = {make_alias(): target}\n'
            '\n'
            '\n'
            'class WorkflowDefinition:\n'
            '    pass\n'
            '\n'
            '\n'
            'def register(definition):\n'
            '    return definition\n'
            '\n'
            '\n'
            'def _register_default_workflows():\n'
            '    register(WorkflowDefinition(workflow_id=make_id(), name=get_name(), tags=[make_tag()]))\n'
        )
        repo = self.build_fixture(
            override={
                'src/backend/InvenTree/ai/core/workflows/registry.py': dynamic_registry
            }
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        workflows = report['workflows']
        registrations = workflows['registrations']
        self.assertEqual(len(registrations), 1)
        entry = registrations[0]
        self.assertEqual(entry['workflow_id'], {'kind': 'unresolved'})
        self.assertEqual(entry['metadata']['name'], {'kind': 'unresolved'})
        self.assertEqual(entry['metadata']['tags'], {'kind': 'unresolved'})
        self.assertEqual(workflows['aliases'], [])
        self.assertEqual(workflows['unresolved_values'], 4)
        self.assertNotIn('make_id()', result.stdout)
        self.assertNotIn('make_alias()', result.stdout)


class TestDockerPythonBase(ProbeTestCase):
    """Slice 5: production Docker Python-base line metadata."""

    def test_python_base_line_metadata(self):
        """Python-base FROM line metadata matches the Dockerfile exactly."""
        repo = self.build_fixture()
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        base = report['docker_python_base']
        self.assertEqual(base['path'], 'contrib/container/Dockerfile')
        self.assertEqual(
            base['line'],
            line_of(
                FIXTURE_DOCKERFILE,
                'FROM python:3.14.7-slim-trixie@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS inventree_base',
            ),
        )
        self.assertEqual(base['image'], 'python')
        self.assertEqual(base['tag'], '3.14.7-slim-trixie')
        self.assertEqual(
            base['digest'],
            'sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6',
        )
        self.assertEqual(base['stage'], 'inventree_base')

    def test_dockerfile_without_python_base_fails_sanitized(self):
        """A Dockerfile without a python base line fails with a sanitized error."""
        repo = self.build_fixture(
            override={'contrib/container/Dockerfile': 'FROM alpine:3.20 AS base\n'}
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('contrib/container/Dockerfile', result.stderr)
        self.assertRegex(result.stderr.lower(), r'python')
        self.assertNotIn('Traceback', result.stderr)


class TestScopedSourceInventory(ProbeTestCase):
    """Slice 6: agent-framework imports, call sites, tests separate from runtime."""

    def test_call_sites_imports_and_test_separation(self):
        """Call sites and imports match source, tests excluded from runtime counts."""
        repo = self.build_fixture()
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)

        scope = report['runtime_scope']
        self.assertEqual(scope['backend_root'], 'src/backend/InvenTree')
        self.assertEqual(
            scope['roots'],
            ['ai', 'aichat', 'voice', 'approvals', 'repair', 'InvenTree/asgi.py'],
        )
        self.assertEqual(scope['roots_missing'], [])

        imports = report['agent_framework_imports']
        self.assertEqual(
            imports['runtime'],
            [
                {
                    'path': 'src/backend/InvenTree/ai/core/agents/factory.py',
                    'line': line_of(
                        FIXTURE_FACTORY, 'from agent_framework import ChatAgent'
                    ),
                    'module': 'agent_framework',
                },
                {
                    'path': 'src/backend/InvenTree/ai/core/workflows/root.py',
                    'line': line_of(FIXTURE_ROOT, 'import agent_framework.azure'),
                    'module': 'agent_framework.azure',
                },
            ],
        )
        self.assertEqual(
            imports['tests'],
            [
                {
                    'path': 'src/backend/InvenTree/ai/core/tests/test_fixture_runtime.py',
                    'line': line_of(FIXTURE_TEST_FILE, 'import agent_framework'),
                    'module': 'agent_framework',
                }
            ],
        )

        sites = report['call_sites']
        self.assertEqual(
            sites['runtime']['AgentSpec'],
            [
                {
                    'path': 'src/backend/InvenTree/ai/core/agents/factory.py',
                    'line': line_of(
                        FIXTURE_FACTORY, '    return AgentSpec(workflow_id="wf1")'
                    ),
                    'symbol': 'AgentSpec',
                }
            ],
        )
        self.assertEqual(
            sites['runtime']['build_chat_client'],
            [
                {
                    'path': 'src/backend/InvenTree/ai/core/agents/factory.py',
                    'line': line_of(
                        FIXTURE_FACTORY, '    client = build_chat_client("gpt-4o")'
                    ),
                    'symbol': 'build_chat_client',
                }
            ],
        )
        self.assertEqual(sites['runtime']['build_agent'], [])
        self.assertEqual(
            sites['runtime']['run_with_rbac'],
            [
                {
                    'path': 'src/backend/InvenTree/ai/core/workflows/root.py',
                    'line': line_of(
                        FIXTURE_ROOT, '    await run_with_rbac(client, {})'
                    ),
                    'symbol': 'run_with_rbac',
                }
            ],
        )
        self.assertEqual(
            sites['runtime']['bind_capability_run'],
            [
                {
                    'path': 'src/backend/InvenTree/ai/core/workflows/rbac_run.py',
                    'line': line_of(FIXTURE_RBAC_RUN, '    bind_capability_run(ctx)'),
                    'symbol': 'bind_capability_run',
                }
            ],
        )
        self.assertEqual(
            sites['runtime']['run'],
            [
                {
                    'path': 'src/backend/InvenTree/ai/core/workflows/rbac_run.py',
                    'line': line_of(
                        FIXTURE_RBAC_RUN, '    return await client.run("wf8")'
                    ),
                    'symbol': 'run',
                }
            ],
        )
        self.assertEqual(
            sites['runtime']['run_stream'],
            [
                {
                    'path': 'src/backend/InvenTree/ai/core/workflows/root.py',
                    'line': line_of(FIXTURE_ROOT, '    return client.run_stream("x")'),
                    'symbol': 'run_stream',
                }
            ],
        )

        tests = sites['tests']
        self.assertEqual(
            [
                (entry['path'], entry['line'], entry['symbol'])
                for entry in tests['build_agent']
            ],
            [
                (
                    'src/backend/InvenTree/ai/core/tests/test_fixture_runtime.py',
                    line_of(FIXTURE_TEST_FILE, '    build_agent(client)'),
                    'build_agent',
                )
            ],
        )
        self.assertEqual(len(tests['run']), 1)
        self.assertEqual(len(tests['bind_capability_run']), 1)
        for entry in tests['run'] + tests['bind_capability_run']:
            self.assertIn('/tests/', entry['path'])
        for symbol in sites['symbols']:
            runtime_paths = [entry['path'] for entry in sites['runtime'][symbol]]
            self.assertFalse(any('/tests/' in path for path in runtime_paths))

        review = sites['review_candidates']
        self.assertEqual(review['symbols'], ['run', 'run_stream'])
        self.assertIn('review candidate', review['note'].lower())
        self.assertIn('not a proven tool dispatch', review['note'].lower())

        self.assertEqual(
            report['test_paths'],
            ['src/backend/InvenTree/ai/core/tests/test_fixture_runtime.py'],
        )

        counts = report['counts']
        self.assertEqual(
            counts['agent_framework_imports']['runtime'], len(imports['runtime'])
        )
        self.assertEqual(
            counts['agent_framework_imports']['tests'], len(imports['tests'])
        )
        for bucket in ('runtime', 'tests'):
            per_symbol = counts['call_sites'][bucket]
            self.assertEqual(
                per_symbol['total'],
                sum(len(sites[bucket][symbol]) for symbol in sites['symbols']),
            )
            for symbol in sites['symbols']:
                self.assertEqual(per_symbol[symbol], len(sites[bucket][symbol]))
        self.assertEqual(counts['test_paths'], len(report['test_paths']))

    def test_duplicate_calls_and_imports_sorted_and_deduped(self):
        """Duplicate call/import tuples collapse to one entry in sorted order."""
        dupes = (
            '"""Fixture dupes."""\n'
            'import agent_framework\n'
            'import agent_framework\n'
            '\n'
            '\n'
            'def duo():\n'
            '    build_agent(); build_agent()\n'
            '    build_agent()\n'
        )
        repo = self.build_fixture(
            extra={'src/backend/InvenTree/ai/core/dupes.py': dupes}
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        sites = report['call_sites']['runtime']['build_agent']
        self.assertEqual(
            [(entry['path'], entry['line'], entry['symbol']) for entry in sites],
            [
                ('src/backend/InvenTree/ai/core/dupes.py', 7, 'build_agent'),
                ('src/backend/InvenTree/ai/core/dupes.py', 8, 'build_agent'),
            ],
        )
        imports = report['agent_framework_imports']['runtime']
        dup_imports = [entry for entry in imports if entry['path'].endswith('dupes.py')]
        self.assertEqual([entry['line'] for entry in dup_imports], [2, 3])
        keys = [(entry['path'], entry['line'], entry['symbol']) for entry in sites]
        self.assertEqual(keys, sorted(set(keys)))
        for bucket in (imports, report['agent_framework_imports']['tests']):
            bucket_keys = [
                (entry['path'], entry['line'], entry['module']) for entry in bucket
            ]
            self.assertEqual(bucket_keys, sorted(set(bucket_keys)))

    def test_symlinked_files_and_dirs_are_skipped(self):
        """Symlinked files and directories are skipped while real sources scan."""
        ghost_target = self.tmp / 'ghost_target.py'
        ghost_target.write_text('import agent_framework\nbuild_agent(x)\nrun()\n')
        linkdir_target = self.tmp / 'linkdir_target'
        linkdir_target.mkdir()
        (linkdir_target / 'inner.py').write_text(
            'import agent_framework\nrun_stream(x)\n'
        )
        repo = self.build_fixture(
            extra={
                'src/backend/InvenTree/voice/api.py': '"""Fixture voice."""\nrun_stream(control)\n'
            }
        )
        (repo / 'src/backend/InvenTree/ai/core/ghost.py').symlink_to(ghost_target)
        (repo / 'src/backend/InvenTree/voice/linkdir').symlink_to(linkdir_target)
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        blob = result.stdout
        report = json.loads(blob)
        control = [
            entry
            for entry in report['call_sites']['runtime']['run_stream']
            if entry['path'] == 'src/backend/InvenTree/voice/api.py'
        ]
        self.assertEqual(len(control), 1, 'non-symlink control source must be scanned')
        self.assertNotIn('ghost_target', blob)
        self.assertNotIn('inner.py', blob)
        self.assertNotIn('linkdir_target', blob)
        self.assertIn('src/backend/InvenTree/ai/core/ghost.py', blob)
        self.assertIn('src/backend/InvenTree/voice/linkdir', blob)

    def test_skipped_symlinked_sources_disclosed_and_incomplete(self):
        """Skipped symlink roots/files are disclosed and coverage stays incomplete."""
        ghost_target = self.tmp / 'ghost_target2.py'
        ghost_target.write_text('import agent_framework\n')
        nested_target = self.tmp / 'nested_target'
        nested_target.mkdir()
        (nested_target / 'inner2.py').write_text('import agent_framework\n')
        repo = self.build_fixture()
        (repo / 'src/backend/InvenTree/ai/core/ghost.py').symlink_to(ghost_target)
        (repo / 'src/backend/InvenTree/approvals/nested').symlink_to(nested_target)
        voice = repo / 'src/backend/InvenTree/voice'
        outside_voice = self.tmp / 'external-voice'
        shutil.move(voice, outside_voice)
        voice.symlink_to(outside_voice, target_is_directory=True)
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        blob = result.stdout
        report = json.loads(blob)
        expected = [
            'src/backend/InvenTree/ai/core/ghost.py',
            'src/backend/InvenTree/approvals/nested',
            'src/backend/InvenTree/voice',
        ]
        self.assertEqual(report['runtime_scope']['skipped_symlink_paths'], expected)
        self.assertEqual(report['completeness']['skipped_source_paths'], expected)
        self.assertFalse(report['completeness']['inventory_complete'])
        self.assertNotIn('external-voice', blob)
        self.assertNotIn('inner2', blob)
        self.assertNotIn('ghost_target2', blob)
        self.assertEqual(report['workflows']['unresolved_values'], 2)


class TestSafetyAndNoSideEffects(ProbeTestCase):
    """Slice 7: output safety, non-mutation, no app execution, no credentials read."""

    def snapshot(self, root):
        """Return a hash snapshot of all regular files under a directory."""
        state = {}
        for path in sorted(root.rglob('*')):
            if path.is_file() and not path.is_symlink():
                state[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return state

    def test_source_output_collision_refused_and_source_untouched(self):
        """Output colliding with source/input files is refused and nothing changes."""
        repo = self.build_fixture()
        before = self.snapshot(repo)
        for rel in (
            'src/backend/InvenTree/ai/core/agents/factory.py',
            'src/backend/requirements.in',
        ):
            result = self.run_probe(repo, output=repo / rel)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertRegex(result.stderr.lower(), r'refus|unsafe|overwrite')
            self.assertNotIn('Traceback', result.stderr)
        result = self.run_probe(repo, output=repo)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr.lower(), r'refus|unsafe|overwrite|regular file')
        self.assertEqual(self.snapshot(repo), before)

    def test_successful_run_is_nonmutating_inside_repo(self):
        """A successful run mutates nothing inside the repository."""
        repo = self.build_fixture()
        before = self.snapshot(repo)
        out = self.tmp / 'report.json'
        result = self.run_probe(repo, output=out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.exists())
        self.assertEqual(self.snapshot(repo), before)

    def test_symlink_output_refused_and_target_untouched(self):
        """Symlink output is refused and the symlink target is never written."""
        repo = self.build_fixture()
        target = self.tmp / 'target.txt'
        target.write_text('untouched')
        link = self.tmp / 'report.json'
        link.symlink_to(target)
        result = self.run_probe(repo, output=link)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertRegex(result.stderr.lower(), r'refus|unsafe|symlink')
        self.assertEqual(target.read_text(), 'untouched')

    def test_output_refuses_repo_files_and_hardlink_aliases(self):
        """Output refuses repo files and hardlink aliases; source bytes unchanged."""
        repo = self.build_fixture(
            extra={'contrib/fixture_tool.py': 'print("fixture source")\n'}
        )
        tool = repo / 'contrib/fixture_tool.py'
        tool_before = hashlib.sha256(tool.read_bytes()).hexdigest()
        result = self.run_probe(repo, output=tool)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(hashlib.sha256(tool.read_bytes()).hexdigest(), tool_before)

        factory = repo / 'src/backend/InvenTree/ai/core/agents/factory.py'
        factory_before = hashlib.sha256(factory.read_bytes()).hexdigest()
        alias = self.tmp / 'hardlinked-report.json'
        os.link(factory, alias)
        result = self.run_probe(repo, output=alias)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(
            hashlib.sha256(factory.read_bytes()).hexdigest(), factory_before
        )

        out = self.tmp / 'plain-report.json'
        result = self.run_probe(repo, output=out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.exists())

    def test_side_effect_sentinel_source_is_not_executed(self):
        """Fixture modules with side effects are parsed, never executed."""
        sentinel = self.tmp / 'SENTINEL_TRIGGERED'
        source = (
            '"""Sentinel module."""\n'
            'from pathlib import Path\n'
            f"Path({str(sentinel)!r}).write_text('boom')\n"
            "raise RuntimeError('module must not be executed')\n"
        )
        repo = self.build_fixture(
            extra={'src/backend/InvenTree/ai/core/sentinel.py': source}
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists(), 'fixture side effect must never run')

    def test_credentials_files_are_ignored(self):
        """Credential-shaped files are never read and secrets never reach output."""
        secrets = (
            'SUPERSECRET_ENV_VALUE',
            'SUPERSECRET_AI_VALUE',
            'SUPERSECRET_CONFIG_VALUE',
            'SUPERSECRET_JSON_VALUE',
        )
        repo = self.build_fixture(
            extra={
                '.env': 'DB_PASSWORD=SUPERSECRET_ENV_VALUE\n',
                'src/backend/InvenTree/ai/credentials.env': 'API_KEY=SUPERSECRET_AI_VALUE\n',
                'src/backend/InvenTree/ai/secret_config.yaml': 'password: SUPERSECRET_CONFIG_VALUE\n',
                'src/backend/config.json': '{"token": "SUPERSECRET_JSON_VALUE"}\n',
            }
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        for secret in secrets:
            self.assertNotIn(secret, result.stdout)
            self.assertNotIn(secret, result.stderr)


class TestDeterminismAndHonesty(ProbeTestCase):
    """Slice 8: deterministic output, cwd independence, honest completeness."""

    def test_report_deterministic_sorted_and_counts_consistent(self):
        """Reports are byte-identical across runs/cwd with sorted arrays and derived counts."""
        repo = self.build_fixture()
        out1 = self.tmp / 'r1.json'
        out2 = self.tmp / 'r2.json'
        result1 = self.run_probe(repo, output=out1, cwd=self.tmp / 'cwd')
        result2 = self.run_probe(repo, output=out2, cwd=Path('/'))
        self.assertEqual(result1.returncode, 0, result1.stderr)
        self.assertEqual(result2.returncode, 0, result2.stderr)
        self.assertEqual(out1.read_bytes(), out2.read_bytes())

        result3 = self.run_probe(
            Path('repo'), output=self.tmp / 'r3.json', cwd=self.tmp
        )
        self.assertEqual(result3.returncode, 0, result3.stderr)
        self.assertEqual((self.tmp / 'r3.json').read_bytes(), out1.read_bytes())

        report = json.loads(out1.read_text())
        call_sites = report['call_sites']
        for bucket in ('runtime', 'tests'):
            for symbol in call_sites['symbols']:
                keys = [
                    (entry['path'], entry['line'], entry['symbol'])
                    for entry in call_sites[bucket][symbol]
                ]
                self.assertEqual(keys, sorted(set(keys)))
        for bucket in ('runtime', 'tests'):
            keys = [
                (entry['path'], entry['line'], entry['module'])
                for entry in report['agent_framework_imports'][bucket]
            ]
            self.assertEqual(keys, sorted(set(keys)))
        self.assertEqual(report['test_paths'], sorted(set(report['test_paths'])))

        counts = report['counts']
        self.assertEqual(counts['test_paths'], len(report['test_paths']))
        self.assertEqual(
            counts['workflow_registrations'], len(report['workflows']['registrations'])
        )
        self.assertEqual(
            counts['workflow_aliases'], len(report['workflows']['aliases'])
        )
        self.assertEqual(
            counts['agent_framework_imports']['runtime'],
            len(report['agent_framework_imports']['runtime']),
        )
        for bucket in ('runtime', 'tests'):
            self.assertEqual(
                counts['call_sites'][bucket]['total'],
                sum(
                    len(call_sites[bucket][symbol]) for symbol in call_sites['symbols']
                ),
            )

    def test_completeness_reports_missing_scope_and_empty_sections(self):
        """Missing scope roots and empty sections are reported honestly as incomplete."""
        repo = self.build_fixture(
            omit=('src/backend/InvenTree/voice/api.py',),
            override={
                'src/backend/InvenTree/ai/core/workflows/registry.py': (
                    '"""Empty fixture registry."""\nWORKFLOW_ID_ALIASES = {}\n'
                )
            },
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        completeness = report['completeness']
        self.assertFalse(completeness['inventory_complete'])
        self.assertEqual(completeness['scoped_roots_missing'], ['voice'])
        self.assertEqual(
            completeness['empty_sections'],
            ['workflow_aliases', 'workflow_registrations'],
        )
        self.assertIn('not evidence of absence', completeness['note'])
        self.assertEqual(report['workflows']['registrations'], [])

    def test_unparsed_dependency_lines_mark_ledger_partial(self):
        """Unparsed dependency lines make the ledger partial, without echoing text."""
        repo = self.build_fixture(
            override={
                'src/backend/requirements.in': (
                    'django>=4.2\n-e git+ZZUNIQUE_REQ_TEXT#egg=local\n'
                )
            }
        )
        result = self.run_probe(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        blob = result.stdout
        report = json.loads(blob)
        self.assertFalse(report['completeness']['inventory_complete'])
        self.assertEqual(report['completeness']['unparsed_dependency_lines'], 1)
        ledger = report['dependency_ledger']
        self.assertTrue(ledger['partial'])
        self.assertEqual(ledger['unparsed_line_count'], 1)
        req_in = [
            entry
            for entry in ledger['files']
            if entry['path'] == 'src/backend/requirements.in'
        ][0]
        self.assertEqual(req_in['unparsed_line_count'], 1)
        self.assertNotIn('ZZUNIQUE_REQ_TEXT', blob)
        self.assertNotIn('egg=local', blob)


if __name__ == '__main__':
    unittest.main()
