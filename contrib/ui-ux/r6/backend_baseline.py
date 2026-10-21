"""Run the S0 API baseline in a disposable offline Docker environment."""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TESTS = [
    'tasks.tests.test_workorder_api',
    'tasks.tests.test_client_scope',
    'tasks.tests.test_closeout_security',
    'tasks.tests.test_closeout_completion_integration',
    'tasks.tests.test_closeout_capture',
    'tasks.tests.test_closeout_effects',
    'tasks.tests.test_workorder_complete_api',
    'tasks.tests.test_packet_finalization',
    'tasks.tests.test_workorder_rbac',
    'tasks.tests.test_granted_scope_resolver',
    'tasks.tests.test_s0_capability_contracts',
    'tasks.tests.test_legacy_scope_api',
    'repair.test_packet_closeout',
    'assets.test_barcode',
    'tasks.tests.test_kanban_card_api',
    'tasks.tests.test_kanban_card_endpoints',
    'tasks.tests.test_kanban_overview',
    'tasks.tests.test_kanban_parts_reconcile',
    'tasks.tests.test_kanban_schedule_surface',
    'tasks.tests.test_scheduling_api',
    'tasks.tests.test_schedule_planner_api',
    'tasks.tests.test_kanban_tool_scope',
    'repair.test_spine',
    'repair.test_safety_gates.SafetyGateAPITest',
    'repair.test_generation.VerifyDiagnosisTest',
    'repair.test_generation.RepairPacketNewEndpointsTest',
    'repair.test_investigation',
    'repair.test_work_packages',
    'repair.test_duplicate_repairs',
    'repair.test_start_repair',
    'common.test_api.AttachmentAPITests',
    'common.test_api.AttachmentThumbnailAPITests',
]


EXTRA_SOURCES = (
    ('contrib/cosmos', 'contrib/cosmos'),
    ('contrib/pump-cassandra', 'contrib/pump-cassandra'),
    (
        'contrib/container/dev-docker-compose.yml',
        'contrib/container/dev-docker-compose.yml',
    ),
    ('src/frontend/src/assets/mimic', 'src/frontend/src/assets/mimic'),
)


def run_inside(labels):
    """Copy code into temporary storage and run Django's isolated test runner."""
    scratch = Path(tempfile.mkdtemp(prefix='aimms-r6-s0-'))
    workspace = scratch / 'work'
    backend = workspace / 'src' / 'backend'
    shutil.copytree(
        '/source/backend',
        backend,
        ignore=shutil.ignore_patterns(
            '.env*',
            'config.yaml',
            'secret_key.txt',
            '__pycache__',
            '_testfolder',
            '.pytest_cache',
            'node_modules',
            '.venv',
            'media',
            'static',
            '*.sqlite*',
        ),
    )
    # Repository data some backend tests read by path (pump-station packs,
    # limits, samples, the dev compose file and the mimic SVGs). Mounted
    # read-only when present; secrets and env files are never copied.
    for _source, target in EXTRA_SOURCES:
        mounted = Path('/source/extra') / target
        if mounted.is_file():
            (workspace / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mounted, workspace / target)
        elif mounted.is_dir():
            shutil.copytree(
                mounted,
                workspace / target,
                ignore=shutil.ignore_patterns(
                    '.env*', '*.env', 'secret*', '__pycache__'
                ),
            )
    (workspace / 'VERSION').write_text('')
    (backend / 'InvenTree' / 's0_settings.py').write_text(
        'import InvenTree.settings as baseline\n'
        'for setting_name in dir(baseline):\n'
        '    if setting_name.isupper():\n'
        '        globals()[setting_name] = getattr(baseline, setting_name)\n'
        "DATABASES['default']['TEST']['NAME'] = '/evidence/test.sqlite3'\n"
    )
    config = workspace / 'config' / 'config.yaml'
    config.parent.mkdir()
    config.write_text('{}\n')
    os.environ.update({
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': os.pathsep.join((
            str(backend / 'InvenTree'),
            str(backend),
            '/s0',
        )),
        'INVENTREE_CONFIG_FILE': str(config),
        'INVENTREE_SECRET_KEY_FILE': str(config.parent / 'secret_key.txt'),
        'INVENTREE_DB_ENGINE': 'sqlite3',
        'INVENTREE_DB_NAME': str(scratch / 'runtime.sqlite3'),
        'INVENTREE_MEDIA_ROOT': str(scratch / 'media'),
        'INVENTREE_STATIC_ROOT': str(scratch / 'static'),
        'INVENTREE_BACKUP_DIR': str(scratch / 'backup'),
        'INVENTREE_PLUGIN_FILE': str(config.parent / 'plugins.yaml'),
        'INVENTREE_SITE_URL': 'http://localhost',
        'INVENTREE_DEBUG': 'true',
        'INVENTREE_LOG_LEVEL': 'WARNING',
        'INVENTREE_CONSOLE_LOG': 'false',
        'INVENTREE_PLUGINS_ENABLED': 'false',
        'INVENTREE_EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    })
    print(
        json.dumps({
            'synthetic': True,
            'network': 'none',
            'database': 'isolated SQLite test database',
            'source': str(backend),
            'test_labels': labels,
        }),
        flush=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            'manage.py',
            'test',
            *labels,
            '--keepdb',
            '--noinput',
            '--verbosity=2',
            '--testrunner=django.test.runner.DiscoverRunner',
            '--settings=s0_settings',
        ],
        cwd=backend / 'InvenTree',
        check=False,
    )
    return result.returncode


def run_host(labels, image, output):
    """Launch only a new test container and retain its synthetic output."""
    root = Path(__file__).resolve().parents[3]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    image_id = subprocess.check_output(
        ['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], text=True
    ).strip()
    command = [
        'docker',
        'run',
        '--rm',
        '--pull',
        'never',
        '--network',
        'none',
        '--read-only',
        '--cap-drop',
        'ALL',
        '--security-opt',
        'no-new-privileges',
        '--tmpfs',
        '/tmp:rw,nosuid,size=2g',
        '--mount',
        f'type=bind,src={root / "src/backend"},dst=/source/backend,readonly',
        *[
            arg
            for source, target in EXTRA_SOURCES
            if (root / source).exists()
            for arg in (
                '--mount',
                f'type=bind,src={root / source},dst=/source/extra/{target},readonly',
            )
        ],
        '--mount',
        f'type=bind,src={Path(__file__).resolve()},dst=/s0/backend_baseline.py,readonly',
        '--mount',
        f'type=bind,src={Path(__file__).with_name("s0_scope_probe.py").resolve()},dst=/s0/s0_scope_probe.py,readonly',
        '--mount',
        f'type=bind,src={output},dst=/evidence',
        '--entrypoint',
        '/usr/bin/env',
        image_id,
        '-i',
        'PATH=/usr/local/bin:/usr/bin:/bin',
        'HOME=/tmp',
        'PYTHONUSERBASE=/root/.local',
        'LANG=C.UTF-8',
        'python3',
        '/s0/backend_baseline.py',
        '--inside',
        *labels,
    ]
    print(f'Running {len(labels)} S0 test modules in {image_id}', flush=True)
    log = output / 'backend.log'
    if log.exists():
        history = (
            output
            / 'history'
            / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        )
        history.mkdir(parents=True)
        for name in ('backend.log', 'manifest.json'):
            previous = output / name
            if previous.exists():
                shutil.copy2(previous, history / name)
    with log.open('w') as stream:
        result = subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT, check=False
        )
    manifest = {
        'image_id': image_id,
        'base_revision': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=root, text=True
        ).strip(),
        'synthetic': True,
        'isolation': 'New container; network none; clean environment; copied backend code; temporary SQLite/config/media; no existing DB or service lifecycle change',
        'test_labels': labels,
        'exit_code': result.returncode,
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest(),
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Exit {result.returncode}; evidence: {output}', flush=True)
    return result.returncode


def main():
    """Select the host launcher or its container bootstrap."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--image', default='inventree-dev-image:latest')
    parser.add_argument(
        '--output', type=Path, default=Path('data/devscratch/r6-s0/2026-10-18/backend')
    )
    parser.add_argument('labels', nargs='*')
    arguments = parser.parse_args()
    labels = arguments.labels or DEFAULT_TESTS
    if arguments.inside:
        return run_inside(labels)
    return run_host(labels, arguments.image, arguments.output)


if __name__ == '__main__':
    raise SystemExit(main())
