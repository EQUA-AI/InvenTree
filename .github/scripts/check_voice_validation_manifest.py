"""Phase V registry guard. Static registration is not a test or human pass."""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / '.github/voice_validation_manifest.json'
REQUIRED = [
    ['L1', 'L2', 'L4'],
    ['L1', 'L2', 'L4'],
    ['L1', 'L3', 'L4'],
    ['L1', 'L3', 'L4'],
    ['L1'],
    ['L1', 'L3'],
    ['L1', 'L2'],
    ['L1', 'L3'],
    ['L2'],
    ['L3', 'L4'],
    ['L2'],
    ['L2', 'L3'],
    ['L2', 'L3', 'L4'],
    ['L1', 'L2', 'L3'],
    ['L1', 'L4', 'L5'],
    ['L3', 'L4'],
    ['L2', 'L4'],
    ['L4', 'L5'],
    ['L3', 'L5'],
    ['L3', 'L5'],
    ['L1', 'L3'],
    ['L3', 'L5'],
]
STATUSES = {'PASS', 'FAIL', 'NOT RUN', 'BLOCKED', 'DEFERRED', 'N/A'}
RUNNERS = {'pytest', 'django', 'playwright', 'redis'}
LEGACY_ISLAND = {
    'test_capability_invocation_guard',
    'test_tool_events',
    'test_approval_reference',
    'test_approvals_sandbox_seams',
    'test_email_recipient_policy',
    'test_legacy_approval_removed',
    'test_normalized_turn_service',
    'test_question_answers',
    'test_rbac_voice_text_parity',
    'test_sandbox_seams',
    'test_wf8_voice_readonly',
}


def checked_path(root, value):
    """Reject missing and out-of-tree references."""
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.exists():
        raise ValueError(f'Missing or invalid path: {value}')
    return path


def registered(manifest, path):
    """A package registration includes its descendants, never sibling files."""
    return any(
        path == suite['path'] or path.startswith(suite['path'] + '/')
        for suite in manifest['suites']
    )


def validate(manifest, root=ROOT):
    """Validate exact IDs, selected test definitions, layers, owners and exclusions."""
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported manifest version')
    suites = manifest['suites']
    for name in LEGACY_ISLAND:
        if not any(
            s['path'].endswith('/' + name + '.py') and s['runner'] == 'pytest'
            for s in suites
        ):
            raise ValueError(f'Unregistered original A10 test: {name}')
    if len({s['path'] for s in suites}) != len(suites):
        raise ValueError('Duplicate suite registration')
    for suite in suites:
        checked_path(root, suite['path'])
        if suite['runner'] not in RUNNERS or not suite['label']:
            raise ValueError('Invalid runner/label')
    for pattern in manifest['watch']:
        for path in root.glob(pattern):
            if not registered(manifest, path.relative_to(root).as_posix()):
                raise ValueError(f'Unregistered test: {path.relative_to(root)}')
    expected = [f'S{i:02}' for i in range(1, 23)]
    if [r['id'] for r in manifest['scenarios']] != expected:
        raise ValueError(
            'Scenarios must register S01-S22 exactly once, in source order'
        )
    for row, required in zip(manifest['scenarios'], REQUIRED, strict=True):
        if row['required_layers'] != required:
            raise ValueError(f'Required layers changed: {row["id"]}')
        if row['status'] not in STATUSES or not row['owner'] or not row['gap']:
            raise ValueError('Status, owner and limitations are mandatory')
        if row['status'] == 'PASS' and not row.get('evidence'):
            raise ValueError('PASS requires actual evidence, not file existence')
        for reference in row['tests']:
            path = checked_path(root, reference['path'])
            if not registered(manifest, reference['path']):
                raise ValueError(f'Unregistered scenario test: {reference["path"]}')
            name = reference['test']
            source = path.read_text()
            if path.suffix == '.py':
                names = {
                    n.name
                    for n in ast.walk(ast.parse(source))
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                exists = name in names and name.startswith('test_')
            else:
                exists = f"test('{name}'" in source or f'test("{name}"' in source
            if not exists:
                raise ValueError(
                    f'Missing test definition: {reference["path"]}::{name}'
                )
        external = {e['layer']: e for e in row['external_evidence']}
        for layer in set(required) & {'L4', 'L5'}:
            evidence = external.get(layer)
            if (
                not evidence
                or evidence['status'] not in STATUSES
                or not evidence['reason']
            ):
                raise ValueError('Missing live/human evidence disposition')
            checked_path(root, evidence['reference'])
            if evidence['status'] == 'PASS' and not all(
                evidence.get(k) for k in ('run', 'date', 'method', 'reviewer')
            ):
                raise ValueError(
                    'Live/human PASS requires a real signed evidence reference'
                )
    if {v['family'] for v in manifest['variants']} != {
        'work_orders',
        'approvals',
        'inventory',
        'closeout',
        'procedures',
        'draft_workflows',
    }:
        raise ValueError('Six workflow denominators must be declared')
    for variant in manifest['variants']:
        if not variant['variants'] or not variant['gap']:
            raise ValueError('Workflow variant/gap missing')
        for path in variant['tests']:
            checked_path(root, path)
            if not registered(manifest, path):
                raise ValueError(f'Unregistered variant: {path}')
    exclusions = manifest['exclusions']
    if len({e['id'] for e in exclusions}) != len(exclusions) or not exclusions:
        raise ValueError('Unique, explicit exclusions required')
    for item in exclusions:
        if item['status'] not in STATUSES or not item['reason']:
            raise ValueError('Exclusion requires status and reason')


def validate_ci(manifest, root=ROOT):
    """Preserve canonical services, Redis, no-egress and recording-only jobs."""
    qc = (root / '.github/workflows/qc_checks.yaml').read_text()
    job = qc.split('  voice-decision-safety:', 1)[1].split('  no-egress:', 1)[0]
    for token in (
        '--run pytest',
        '--collect django',
        'test_decision_redis.py',
        'generate_wire_contract --check',
        'yarn test:unit',
        'yarn tsc --noEmit',
        'postgres:',
        'redis:',
    ):
        if token not in job:
            raise ValueError(f'Blocking job lost required step: {token}')
    if 'continue-on-error: true' in job:
        raise ValueError('Voice safety cannot continue on error')
    for suite in manifest['suites']:
        if suite['runner'] == 'django' and suite['label'] not in job:
            raise ValueError(f'Canonical suite omitted from CI: {suite["label"]}')
    frontend = (root / '.github/workflows/frontend.yaml').read_text()
    for text in (qc, frontend):
        if (
            "'.github/voice_validation_manifest.json'" not in text
            or "'.github/scripts/**'" not in text
        ):
            raise ValueError('Registry/script changes must trigger safety jobs')
    if '--disable-socket' not in qc or 'playwright.voice.config.ts' not in frontend:
        raise ValueError('No-egress and recording-only lanes must remain')


def selected(manifest, runner):
    """Return the exact registered lane, preserving declaration order."""
    return [s for s in manifest['suites'] if s['runner'] == runner]


def collect_django(manifest):
    """Collect using Django's real runner, without running tests or touching DB."""
    import django
    from django.test.runner import DiscoverRunner

    django.setup()
    suite = DiscoverRunner(verbosity=0).build_suite([
        s['label'] for s in selected(manifest, 'django')
    ])

    def flatten(node):
        for item in node:
            if hasattr(item, 'id'):
                yield item.id()
            else:
                yield from flatten(item)

    ids = list(flatten(suite))
    if any('_FailedTest' in item for item in ids):
        raise ValueError('Django collection failed')
    for entry in selected(manifest, 'django'):
        if not any(i.startswith(entry['label'] + '.') for i in ids):
            raise ValueError(f'Empty Django suite: {entry["label"]}')
    for row in manifest['scenarios']:
        for ref in row['tests']:
            entries = [
                s
                for s in selected(manifest, 'django')
                if ref['path'] == s['path'] or ref['path'].startswith(s['path'] + '/')
            ]
            if entries and not any(
                i.startswith(entries[0]['label'] + '.')
                and i.endswith('.' + ref['test'])
                for i in ids
            ):
                raise ValueError(f'Uncollected scenario test: {ref}')
    print(f'Django collected {len(ids)} tests ({len(set(ids))} distinct IDs)')
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate Django collection')
    return 0


def pytest_suite(manifest, collect):
    """Run the registered list with an actual collection guard, not a shadow list."""
    import pytest

    entries = selected(manifest, 'pytest')

    class Guard:
        def pytest_collection_modifyitems(self, session, config, items):
            paths = {Path(item.path).resolve() for item in items}
            for entry in entries:
                if (ROOT / entry['path']).resolve() not in paths:
                    raise pytest.UsageError(f'Empty pytest suite: {entry["path"]}')
            for row in manifest['scenarios']:
                for ref in row['tests']:
                    if any(ref['path'] == e['path'] for e in entries) and not any(
                        Path(item.path).resolve() == (ROOT / ref['path']).resolve()
                        and getattr(item, 'originalname', item.name) == ref['test']
                        for item in items
                    ):
                        raise pytest.UsageError(f'Uncollected scenario test: {ref}')

    args = ['-q', '-p', 'no:cacheprovider', *[str(ROOT / s['path']) for s in entries]]
    if collect:
        args.append('--collect-only')
    return pytest.main(args, plugins=[Guard()])


def collect_playwright(manifest):
    """List real browser test cases without starting servers or logging in."""
    result = subprocess.run(
        [
            'yarn',
            '-s',
            'playwright',
            'test',
            '--list',
            '--reporter=json',
            *[s['label'] for s in selected(manifest, 'playwright')],
        ],
        cwd=ROOT / 'src/frontend',
        capture_output=True,
        text=True,
        check=True,
    )
    # The repository config prints a short human-readable banner before JSON.
    # Parse only the reporter object; a missing/invalid report still fails closed.
    start = result.stdout.find('\n{')
    tree = json.loads(result.stdout[start + 1 :] if start >= 0 else result.stdout)
    specs = []

    def walk(node):
        specs.extend(node.get('specs', []))
        for child in node.get('suites', []):
            walk(child)

    walk(tree)
    for entry in selected(manifest, 'playwright'):
        if not any(Path(s['file']).name == entry['label'] for s in specs):
            raise ValueError(f'Empty browser suite: {entry["label"]}')
    for row in manifest['scenarios']:
        for ref in row['tests']:
            if ref['path'].endswith('.spec.ts') and not any(
                Path(s['file']).name == Path(ref['path']).name
                and s['title'] == ref['test']
                for s in specs
            ):
                raise ValueError(f'Uncollected browser test: {ref}')
    print(f'Playwright collected {len(specs)} specs; no browser tests executed')
    return 0


def main():
    """Validate the registry and optionally invoke an actual test collector."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', choices=['pytest', 'django', 'playwright'])
    parser.add_argument('--run', choices=['pytest'])
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    validate(manifest)
    validate_ci(manifest)
    if args.run or args.collect == 'pytest':
        return pytest_suite(manifest, collect=not args.run)
    if args.collect == 'django':
        return collect_django(manifest)
    if args.collect == 'playwright':
        return collect_playwright(manifest)
    print(
        f'Registered 22 scenarios, six workflow families, {len(manifest["suites"])} suites; evidence is not a pass'
    )
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f'Voice registry validation failed: {exc}', file=sys.stderr)
        sys.exit(1)
