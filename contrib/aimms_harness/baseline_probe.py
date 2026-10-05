#!/usr/bin/env python3
"""AIMMS MAF migration baseline inventory probe (H0 setup tooling).

Credential-free, stdlib-only, static source inventory of the AIMMS/Microsoft
Agent Framework migration baseline. Parses source with ``ast`` only; never
imports or executes application modules. Emits a versioned, deterministic JSON
report. Apart from the explicit report output the probe is non-mutating.

Usage:
    python3 contrib/aimms_harness/baseline_probe.py --repo /path/to/worktree \
        --output /absolute/path/report.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import tomllib

SCHEMA_ID = 'aimms-maf-baseline-inventory'
SCHEMA_VERSION = 1
EVIDENCE_LEVEL = (
    'static/source-only; not live feature enablement or authorization proof'
)

BACKEND_ROOT = Path('src/backend/InvenTree')
SCOPED_DIRS = ('ai', 'aichat', 'voice', 'approvals', 'repair')
SCOPED_FILES = ('InvenTree/asgi.py',)
REQUIRED_INPUTS = (
    'src/backend/InvenTree/ai/core/agents/factory.py',
    'src/backend/InvenTree/ai/core/workflows/rbac_run.py',
    'src/backend/InvenTree/ai/core/tools/invocation_guard.py',
    'src/backend/InvenTree/ai/core/workflows/registry.py',
    'src/backend/InvenTree/ai/pyproject.toml',
    'src/backend/InvenTree/ai/requirements.txt',
    'src/backend/InvenTree/ai/requirements-maf-migration.txt',
    'src/backend/InvenTree/InvenTree/asgi.py',
    'src/backend/requirements.in',
    'src/backend/requirements.txt',
    'src/backend/requirements-3.14.txt',
    'contrib/container/Dockerfile',
)

HEX_SHA = re.compile(r'^[0-9a-f]{40,64}$')
BRANCH_NAME = re.compile(r'^\S+$')
REQ_LINE = re.compile(
    r'^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._\[\]+-]*)'
    r'(?:\s*(?P<spec>===|==|>=|<=|~=|!=|>|<)\s*(?P<ver>[A-Za-z0-9.*+!_-]+))?'
    r'\s*(?:\\|#|;|$)'
)
EXACT_SPECIFIERS = ('==', '===')

LEDGER_FILES = (
    ('src/backend/InvenTree/ai/pyproject.toml', 'pyproject'),
    ('src/backend/InvenTree/ai/requirements.txt', 'ai-requirements'),
    ('src/backend/InvenTree/ai/requirements-maf-migration.txt', 'migration-overlay'),
    ('src/backend/requirements.in', 'input'),
    ('src/backend/requirements.txt', 'hashed-lock'),
    ('src/backend/requirements-3.14.txt', 'hashed-lock'),
)
OVERLAY_PATH = 'src/backend/InvenTree/ai/requirements-maf-migration.txt'
REGISTRY_PATH = 'src/backend/InvenTree/ai/core/workflows/registry.py'
DOCKERFILE_PATH = 'contrib/container/Dockerfile'
LITERAL_SCALARS = (str, int, float, bool)
FROM_LINE = re.compile(
    r'^\s*FROM\s+(?:--\S+\s+)*(?P<ref>\S+)'
    r'(?:\s+[Aa][Ss]\s+(?P<stage>\S+))?(?:\s+#.*)?$',
    re.IGNORECASE,
)
IMAGE_REF = re.compile(r'^(?P<image>[^:@]+)(?::(?P<tag>[^@]+))?(?:@(?P<digest>\S+))?$')

CALL_SYMBOLS = (
    'AgentSpec',
    'bind_capability_run',
    'build_agent',
    'build_chat_client',
    'run',
    'run_stream',
    'run_with_rbac',
)
REVIEW_CANDIDATE_SYMBOLS = ('run', 'run_stream')
REVIEW_CANDIDATE_NOTE = (
    'static .run/.run_stream matches are review candidates, not a proven tool dispatch'
)


class ProbeError(Exception):
    """Sanitized inventory failure: safe to print, never carries source text."""


def parse_args(argv):
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(
        prog='baseline_probe.py',
        description='Emit a static AIMMS MAF migration baseline inventory report.',
    )
    parser.add_argument(
        '--repo', required=True, help='path to the git worktree to inventory'
    )
    parser.add_argument(
        '--output',
        default=None,
        help='absolute path for the JSON report (default: stdout)',
    )
    args = parser.parse_args(argv)
    return args


def resolve_repo(raw):
    """Resolve the repository path to an absolute directory."""
    path = Path(raw)
    if not path.is_dir():
        raise ProbeError(f'repo is not a directory: {raw}')
    return path.resolve()


def git_baseline(repo):
    """Read branch and full HEAD via bounded read-only git calls."""
    branch = _git(repo, ['rev-parse', '--abbrev-ref', 'HEAD'])
    head = _git(repo, ['rev-parse', 'HEAD'])
    if not BRANCH_NAME.match(branch):
        raise ProbeError('git metadata unavailable: unreadable branch name')
    if not HEX_SHA.match(head):
        raise ProbeError('git metadata unavailable: unreadable HEAD revision')
    return {'branch': branch, 'head': head}


def _git(repo, args):
    try:
        result = subprocess.run(
            ['git', '-C', str(repo), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeError(f'git metadata unavailable: {type(exc).__name__}') from None
    if result.returncode != 0:
        raise ProbeError('git metadata unavailable: not a git repository with commits')
    return result.stdout.strip()


def parse_requirement_strings(items, rel):
    """Parse requirement strings into exact pins and other requirements."""
    pins = []
    other = []
    for item in items:
        if not isinstance(item, str):
            raise ProbeError(f'invalid dependency structure: {rel}')
        match = REQ_LINE.match(item.strip())
        if not match:
            raise ProbeError(f'unparseable requirement entry: {rel}')
        entry = {
            'name': match.group('name'),
            'specifier': match.group('spec'),
            'version': match.group('ver'),
        }
        if entry['specifier'] in EXACT_SPECIFIERS:
            pins.append(entry)
        else:
            other.append(entry)
    return pins, other


def parse_requirements_text(text, rel):
    """Split requirement-file text into pins, other requirements and unparsed lines."""
    pins = []
    other = []
    unparsed = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(('#', '--')):
            continue
        match = REQ_LINE.match(line)
        if not match:
            unparsed += 1
            continue
        entry = {
            'name': match.group('name'),
            'specifier': match.group('spec'),
            'version': match.group('ver'),
        }
        if entry['specifier'] in EXACT_SPECIFIERS:
            pins.append(entry)
        else:
            other.append(entry)
    return pins, other, unparsed


def parse_pyproject_text(text, rel):
    """Extract requirement entries from a pyproject.toml dependency table."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ProbeError(f'invalid toml in dependency file: {rel}') from None
    project = data.get('project', {})
    if not isinstance(project, dict):
        raise ProbeError(f'invalid dependency structure: {rel}')
    dependencies = project.get('dependencies', [])
    if not isinstance(dependencies, list):
        raise ProbeError(f'invalid dependency structure: {rel}')
    items = list(dependencies)
    optional = project.get('optional-dependencies', {})
    if not isinstance(optional, dict):
        raise ProbeError(f'invalid dependency structure: {rel}')
    for group in optional.values():
        if not isinstance(group, list):
            raise ProbeError(f'invalid dependency structure: {rel}')
        items.extend(group)
    return parse_requirement_strings(items, rel)


def _sort_entries(entries):
    unique = {json.dumps(entry, sort_keys=True): entry for entry in entries}
    return sorted(
        unique.values(),
        key=lambda e: (e['name'], e['specifier'] or '', e['version'] or ''),
    )


def build_dependency_ledger(repo):
    """Build the dependency pin ledger from the named dependency files."""
    files = []
    for rel, role in LEDGER_FILES:
        text = read_text(repo, rel)
        if rel.endswith('.toml'):
            pins, other = parse_pyproject_text(text, rel)
            unparsed = 0
        else:
            pins, other, unparsed = parse_requirements_text(text, rel)
        pins = _sort_entries(pins)
        files.append({
            'path': rel,
            'role': role,
            'hashed_lock': '--hash=' in text,
            'pins': pins,
            'other_requirements': _sort_entries(other),
            'unparsed_line_count': unparsed,
        })
    files.sort(key=lambda entry: entry['path'])
    exact_pin_count = sum(len(entry['pins']) for entry in files)
    unparsed_total = sum(entry['unparsed_line_count'] for entry in files)
    return {
        'files': files,
        'exact_pin_count': exact_pin_count,
        'unparsed_line_count': unparsed_total,
        'partial': unparsed_total > 0,
        'note': 'pins recorded as written; no version inference'
        + (
            '; unparsed dependency lines counted, not interpreted'
            if unparsed_total
            else ''
        ),
    }


def build_migration_overlay(ledger):
    """Extract the isolated migration overlay section from the ledger."""
    for entry in ledger['files']:
        if entry['path'] == OVERLAY_PATH:
            return {
                'path': entry['path'],
                'disposition': 'isolated-overlay',
                'pins': entry['pins'],
                'pin_count': len(entry['pins']),
            }
    raise ProbeError(f'missing required input: {OVERLAY_PATH}')


def _enum_literal_map(tree):
    """Static map of Enum-style member symbols to their literal values."""
    mapping = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {
            base.id if isinstance(base, ast.Name) else getattr(base, 'attr', '')
            for base in node.bases
        }
        if 'Enum' not in bases:
            continue
        for sub in node.body:
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Constant):
                for target in sub.targets:
                    if isinstance(target, ast.Name):
                        mapping[f'{node.name}.{target.id}'] = sub.value.value
    return mapping


def _tagged_value(node, enum_map):
    """Tag a static expression as literal, resolved-literal or unresolved."""
    if isinstance(node, ast.Constant) and (
        isinstance(node.value, LITERAL_SCALARS) or node.value is None
    ):
        return {'kind': 'literal', 'value': node.value}
    if isinstance(node, (ast.List, ast.Tuple)):
        values = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, LITERAL_SCALARS):
                values.append(elt.value)
            else:
                return {'kind': 'unresolved'}
        return {'kind': 'literal', 'value': values}
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        symbol = f'{node.value.id}.{node.attr}'
        if symbol in enum_map:
            return {
                'kind': 'resolved-literal',
                'value': enum_map[symbol],
                'via_symbol': symbol,
            }
    return {'kind': 'unresolved'}


def _count_unresolved(entries):
    total = 0
    for entry in entries:
        values = [entry['workflow_id'], *entry['metadata'].values()]
        total += sum(1 for value in values if value['kind'] == 'unresolved')
    return total


def build_workflows(repo):
    """Extract literal workflow registrations, metadata and aliases from the registry."""
    tree = parse_python(repo, REGISTRY_PATH)
    enum_map = _enum_literal_map(tree)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    registrations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', '')
        if func_name != 'WorkflowDefinition':
            continue
        tagged = {kw.arg: _tagged_value(kw.value, enum_map) for kw in node.keywords}
        tagged.pop(None, None)
        workflow_id = tagged.pop('workflow_id', {'kind': 'unresolved'})
        registered_via = 'unresolved'
        ancestor = parents.get(id(node))
        while ancestor is not None:
            if isinstance(ancestor, ast.Call):
                ancestor_func = ancestor.func
                ancestor_name = (
                    ancestor_func.id
                    if isinstance(ancestor_func, ast.Name)
                    else getattr(ancestor_func, 'attr', '')
                )
                if ancestor_name == 'register':
                    registered_via = 'register'
                    break
            ancestor = parents.get(id(ancestor))
        registrations.append({
            'line': node.lineno,
            'registered_via': registered_via,
            'workflow_id': workflow_id,
            'metadata': dict(sorted(tagged.items())),
        })
    registrations.sort(key=lambda entry: entry['line'])

    aliases = []
    alias_unresolved = 0
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if not isinstance(target, ast.Name) or 'ALIAS' not in target.id.upper():
            continue
        if not isinstance(value, ast.Dict):
            alias_unresolved += 1
            continue
        for key_node, value_node in zip(value.keys, value.values, strict=True):
            if (
                isinstance(key_node, ast.Constant)
                and isinstance(key_node.value, str)
                and isinstance(value_node, ast.Constant)
                and isinstance(value_node.value, str)
            ):
                aliases.append({
                    'from': key_node.value,
                    'to': value_node.value,
                    'line': key_node.lineno,
                })
            else:
                alias_unresolved += 1
    aliases.sort(key=lambda entry: (entry['from'], entry['to'], entry['line']))

    return {
        'source': REGISTRY_PATH,
        'registrations': registrations,
        'aliases': aliases,
        'unresolved_values': _count_unresolved(registrations) + alias_unresolved,
    }


def build_docker_python_base(repo):
    """Extract production Docker Python-base line metadata from the Dockerfile."""
    text = read_text(repo, DOCKERFILE_PATH)
    for lineno, raw in enumerate(text.splitlines(), start=1):
        match = FROM_LINE.match(raw)
        if not match:
            continue
        ref = match.group('ref')
        image_match = IMAGE_REF.match(ref)
        if not image_match:
            continue
        image = image_match.group('image')
        if image != 'python' and not image.endswith('/python'):
            continue
        return {
            'path': DOCKERFILE_PATH,
            'line': lineno,
            'image': image,
            'tag': image_match.group('tag'),
            'digest': image_match.group('digest'),
            'stage': match.group('stage'),
            'image_ref': ref,
        }
    raise ProbeError(f'no python base image FROM line found in {DOCKERFILE_PATH}')


def is_test_path(rel):
    """Return True when a repo-relative path belongs to test code."""
    parts = Path(rel).parts
    name = Path(rel).name
    return 'tests' in parts or name.startswith('test_') or name.endswith('_test.py')


def _is_agent_framework_module(module):
    return module == 'agent_framework' or module.startswith((
        'agent_framework.',
        'agent_framework_',
    ))


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ''


def _finalize(entries, key_fields):
    unique = {tuple(entry[key] for key in key_fields): entry for entry in entries}
    return [unique[key] for key in sorted(unique)]


def build_source_scan(repo):
    """Scan scoped Python sources for agent-framework imports and call sites."""
    imports = {'runtime': [], 'tests': []}
    sites = {symbol: {'runtime': [], 'tests': []} for symbol in CALL_SYMBOLS}
    test_paths = []

    scoped, skipped = collect_scoped_python(repo)
    for rel in sorted(set(scoped)):
        tree = parse_python(repo, rel)
        bucket = 'tests' if is_test_path(rel) else 'runtime'
        if bucket == 'tests':
            test_paths.append(rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_agent_framework_module(alias.name):
                        imports[bucket].append({
                            'path': rel,
                            'line': node.lineno,
                            'module': alias.name,
                        })
            elif isinstance(node, ast.ImportFrom):
                if node.module and _is_agent_framework_module(node.module):
                    imports[bucket].append({
                        'path': rel,
                        'line': node.lineno,
                        'module': node.module,
                    })
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in CALL_SYMBOLS:
                    sites[name][bucket].append({
                        'path': rel,
                        'line': node.lineno,
                        'symbol': name,
                    })

    for bucket in ('runtime', 'tests'):
        imports[bucket] = _finalize(imports[bucket], ('path', 'line', 'module'))
    for symbol in CALL_SYMBOLS:
        for bucket in ('runtime', 'tests'):
            sites[symbol][bucket] = _finalize(
                sites[symbol][bucket], ('path', 'line', 'symbol')
            )

    roots = [f'{rel_dir}' for rel_dir in SCOPED_DIRS] + list(SCOPED_FILES)
    roots_missing = []
    for rel_dir in SCOPED_DIRS:
        if (BACKEND_ROOT / rel_dir).as_posix() not in skipped and not (
            repo / BACKEND_ROOT / rel_dir
        ).is_dir():
            roots_missing.append(rel_dir)
    for rel_file in SCOPED_FILES:
        if (BACKEND_ROOT / rel_file).as_posix() not in skipped and not (
            repo / BACKEND_ROOT / rel_file
        ).is_file():
            roots_missing.append(rel_file)

    call_sites = {
        'review_candidates': {
            'symbols': sorted(REVIEW_CANDIDATE_SYMBOLS),
            'note': REVIEW_CANDIDATE_NOTE,
        },
        'symbols': sorted(CALL_SYMBOLS),
        'runtime': {
            symbol: sites[symbol]['runtime'] for symbol in sorted(CALL_SYMBOLS)
        },
        'tests': {symbol: sites[symbol]['tests'] for symbol in sorted(CALL_SYMBOLS)},
    }
    return {
        'runtime_scope': {
            'backend_root': BACKEND_ROOT.as_posix(),
            'roots': roots,
            'roots_missing': sorted(roots_missing),
            'skipped_symlink_paths': sorted(skipped),
        },
        'agent_framework_imports': imports,
        'call_sites': call_sites,
        'test_paths': sorted(set(test_paths)),
    }


EMPTY_SECTION_NOTE = (
    'static inventory only; missing or empty sections are reported as-is '
    'and are not evidence of absence'
)


def build_completeness(scan, ledger, workflows, call_sites):
    """Report honestly whether the inventory is complete or partial."""
    empty = []
    if not scan['test_paths']:
        empty.append('test_paths')
    if not workflows['registrations']:
        empty.append('workflow_registrations')
    if not workflows['aliases']:
        empty.append('workflow_aliases')
    if not ledger['exact_pin_count']:
        empty.append('dependency_pins')
    imports_total = sum(
        len(entries) for entries in scan['agent_framework_imports'].values()
    )
    if not imports_total:
        empty.append('agent_framework_imports')
    sites_total = sum(
        len(call_sites[bucket][symbol])
        for bucket in ('runtime', 'tests')
        for symbol in call_sites['symbols']
    )
    if not sites_total:
        empty.append('call_sites')
    missing = scan['runtime_scope']['roots_missing']
    skipped = scan['runtime_scope']['skipped_symlink_paths']
    unparsed = ledger['unparsed_line_count']
    return {
        'inventory_complete': not missing
        and not empty
        and not skipped
        and not unparsed,
        'scoped_roots_missing': sorted(missing),
        'skipped_source_paths': sorted(skipped),
        'empty_sections': sorted(empty),
        'unparsed_dependency_lines': unparsed,
        'note': EMPTY_SECTION_NOTE
        + (
            '; dependency ledger partial: unparsed dependency lines counted, '
            'not interpreted'
            if unparsed
            else ''
        ),
    }


def dump_report(report):
    """Serialize the report as deterministic pretty-printed JSON text."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + '\n'


def validate_output(repo, output_raw):
    """Refuse output that would overwrite repository files or shared inodes."""
    if output_raw is None:
        return None
    output = Path(output_raw)
    if not output.is_absolute():
        raise ProbeError('--output must be an absolute path')
    if output.is_symlink():
        raise ProbeError(f'refusing unsafe output (symlink): {output_raw}')
    if output.exists() and not output.is_file():
        raise ProbeError(f'refusing unsafe output (not a regular file): {output_raw}')
    parent = output.parent.resolve()
    if parent == repo or repo in parent.parents:
        raise ProbeError(
            f'refusing unsafe output (would overwrite repository files): {output_raw}'
        )
    if output.exists() and output.stat().st_nlink > 1:
        raise ProbeError(f'refusing unsafe output (hardlink alias): {output_raw}')
    return output


def _check_repo_input_path(repo, rel, kind):
    """Refuse inputs with symlinked components or resolution outside the repo."""
    current = repo
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            raise ProbeError(f'unsafe {kind} (symlink): {rel}')
    resolved = current.resolve()
    if resolved != repo and repo not in resolved.parents:
        raise ProbeError(f'unsafe {kind} (outside repository): {rel}')


def read_text(repo, rel):
    """Read a UTF-8 source input file relative to the repository."""
    _check_repo_input_path(repo, rel, 'input')
    path = repo / rel
    try:
        return path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        raise ProbeError(f'unreadable file (invalid utf-8): {rel}') from None
    except OSError as exc:
        raise ProbeError(f'unreadable file: {rel} ({type(exc).__name__})') from None


def _has_symlink_component(repo, rel):
    """Return True when any component of a repo-relative path is a symlink."""
    current = repo
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def collect_scoped_python(repo):
    """Return sorted scoped Python paths and sorted skipped symlink paths."""
    base = repo / BACKEND_ROOT
    found = []
    skipped = []
    for rel_dir in SCOPED_DIRS:
        root_rel = (BACKEND_ROOT / rel_dir).as_posix()
        if _has_symlink_component(repo, root_rel):
            skipped.append(root_rel)
            continue
        root = base / rel_dir
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            kept = []
            for name in sorted(dirnames):
                child = Path(dirpath) / name
                if child.is_symlink():
                    skipped.append(child.relative_to(repo).as_posix())
                else:
                    kept.append(name)
            dirnames[:] = kept
            for name in sorted(filenames):
                if not name.endswith('.py'):
                    continue
                path = Path(dirpath) / name
                if path.is_symlink():
                    skipped.append(path.relative_to(repo).as_posix())
                    continue
                found.append(path.relative_to(repo).as_posix())
    for rel_file in SCOPED_FILES:
        file_rel = (BACKEND_ROOT / rel_file).as_posix()
        if _has_symlink_component(repo, file_rel):
            skipped.append(file_rel)
        elif (base / rel_file).is_file():
            found.append(file_rel)
    return sorted(found), sorted(skipped)


def iter_scoped_python(repo):
    """Yield repo-relative paths of scoped Python sources, skipping symlinks."""
    paths, _skipped = collect_scoped_python(repo)
    return iter(paths)


def parse_python(repo, rel):
    """Parse a Python source file with ast only (never executed)."""
    text = read_text(repo, rel)
    try:
        return ast.parse(text, filename=rel)
    except SyntaxError as exc:
        raise ProbeError(
            f'invalid syntax in source: {rel} (line {exc.lineno})'
        ) from None


def check_required_inputs(repo):
    """Verify all required inputs exist as regular non-symlink files."""
    for rel in REQUIRED_INPUTS:
        _check_repo_input_path(repo, rel, 'required input')
        path = repo / rel
        if not path.is_file():
            raise ProbeError(f'missing required input: {rel}')


def validate_sources(repo):
    """Parse all required and scoped Python sources, failing safely on invalid syntax."""
    check_required_inputs(repo)
    for rel in REQUIRED_INPUTS:
        if rel.endswith('.py'):
            parse_python(repo, rel)
    for rel in iter_scoped_python(repo):
        parse_python(repo, rel)


def build_report(repo):
    """Build the full deterministic baseline inventory report."""
    validate_sources(repo)
    ledger = build_dependency_ledger(repo)
    workflows = build_workflows(repo)
    scan = build_source_scan(repo)
    call_sites = scan['call_sites']
    call_counts = {}
    for bucket in ('runtime', 'tests'):
        per_symbol = {
            symbol: len(call_sites[bucket][symbol]) for symbol in call_sites['symbols']
        }
        per_symbol['total'] = sum(per_symbol.values())
        call_counts[bucket] = per_symbol
    return {
        'schema_id': SCHEMA_ID,
        'schema_version': SCHEMA_VERSION,
        'evidence_level': EVIDENCE_LEVEL,
        'git': git_baseline(repo),
        'dependency_ledger': ledger,
        'migration_overlay': build_migration_overlay(ledger),
        'workflows': workflows,
        'docker_python_base': build_docker_python_base(repo),
        'runtime_scope': scan['runtime_scope'],
        'agent_framework_imports': scan['agent_framework_imports'],
        'call_sites': call_sites,
        'test_paths': scan['test_paths'],
        'completeness': build_completeness(scan, ledger, workflows, call_sites),
        'counts': {
            'agent_framework_imports': {
                'runtime': len(scan['agent_framework_imports']['runtime']),
                'tests': len(scan['agent_framework_imports']['tests']),
            },
            'call_sites': call_counts,
            'dependency_pins': {
                'exact': ledger['exact_pin_count'],
                'files': len(ledger['files']),
            },
            'test_paths': len(scan['test_paths']),
            'workflow_registrations': len(workflows['registrations']),
            'workflow_aliases': len(workflows['aliases']),
            'workflow_unresolved_values': workflows['unresolved_values'],
        },
    }


def emit(report, output):
    """Write the report to the output file or stdout."""
    text = dump_report(report)
    if output is None:
        sys.stdout.write(text)
        return
    try:
        output.write_text(text, encoding='utf-8')
    except OSError as exc:
        raise ProbeError(
            f'unable to write report output ({type(exc).__name__})'
        ) from None


def main(argv=None):
    """Run the probe CLI and return a process exit code."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        repo = resolve_repo(args.repo)
        output = validate_output(repo, args.output)
        report = build_report(repo)
        emit(report, output)
    except ProbeError as exc:
        sys.stderr.write(f'baseline_probe: error: {exc}\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
