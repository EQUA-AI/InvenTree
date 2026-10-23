"""Run backend tests in a disposable Docker environment (SQLite or PostgreSQL)."""

import argparse
import datetime
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
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


SOURCE_IGNORE = shutil.ignore_patterns(
    '.env*',
    'config.yaml',
    'secret_key.txt',
    '__pycache__',
    '_testfolder',
    '.pytest_cache',
    'node_modules',
    '.venv',
    '*.sqlite*',
)

# Runtime media/static output is skipped only where a local install would put
# it. Committed directories with those names (InvenTree/static icon metadata,
# plugin sample static files, AI eval media fixtures) are test inputs.
RUNTIME_DIRS = {'/source/backend', '/source/backend/InvenTree'}


def ignore_source(directory, names):
    """Return the names copytree skips in one source directory."""
    ignored = set(SOURCE_IGNORE(directory, names))
    if directory.rstrip('/') in RUNTIME_DIRS:
        ignored.update({'media', 'static'} & set(names))
    return ignored


def run_inside(labels, test_args):
    """Copy code into temporary storage and run Django's isolated test runner."""
    postgres = os.environ.get('INVENTREE_DB_ENGINE') == 'postgresql'
    scratch = Path(tempfile.mkdtemp(prefix='aimms-r6-s0-'))
    workspace = scratch / 'work'
    backend = workspace / 'src' / 'backend'
    shutil.copytree('/source/backend', backend, ignore=ignore_source)
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
    settings = ['--settings=InvenTree.settings']
    if not postgres:
        (backend / 'InvenTree' / 's0_settings.py').write_text(
            'import InvenTree.settings as baseline\n'
            'for setting_name in dir(baseline):\n'
            '    if setting_name.isupper():\n'
            '        globals()[setting_name] = getattr(baseline, setting_name)\n'
            "DATABASES['default']['TEST']['NAME'] = '/evidence/test.sqlite3'\n"
        )
        settings = ['--settings=s0_settings']
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
        'INVENTREE_MEDIA_ROOT': str(scratch / 'media'),
        'INVENTREE_STATIC_ROOT': str(scratch / 'static'),
        'INVENTREE_BACKUP_DIR': str(scratch / 'backup'),
        'INVENTREE_PLUGIN_FILE': str(config.parent / 'plugins.yaml'),
        'INVENTREE_SITE_URL': 'http://localhost',
        'INVENTREE_DEBUG': 'true',
        'INVENTREE_LOG_LEVEL': 'WARNING',
        'INVENTREE_CONSOLE_LOG': 'false',
        'INVENTREE_PLUGINS_ENABLED': os.environ.get(
            'INVENTREE_PLUGINS_ENABLED', 'false'
        ),
        'INVENTREE_EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    })
    if not postgres:
        os.environ.update({
            'INVENTREE_DB_ENGINE': 'sqlite3',
            'INVENTREE_DB_NAME': str(scratch / 'runtime.sqlite3'),
        })
    print(
        json.dumps({
            'synthetic': True,
            'network': 'internal only' if postgres else 'none',
            'database': 'disposable PostgreSQL container'
            if postgres
            else 'isolated SQLite test database',
            'plugins_enabled': os.environ['INVENTREE_PLUGINS_ENABLED'],
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
            *settings,
            *test_args,
        ],
        cwd=backend / 'InvenTree',
        check=False,
    )
    return result.returncode


def git_revision(root):
    """Return the checkout's HEAD, or the revision an exported tree records."""
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        marker = root / 'REVISION'
        return marker.read_text().split()[0] if marker.exists() else None


def git_metadata(root):
    """Return the checkout's real commit hash and date for the version tests."""
    try:
        full_hash, commit_date = subprocess.check_output(
            ['git', 'log', '-1', '--format=%H%n%cs'],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).split()
    except (OSError, subprocess.CalledProcessError, ValueError):
        # An exported tree (git archive) records "<hash> <YYYY-MM-DD>" instead.
        marker = root / 'REVISION'
        if not marker.exists():
            return []
        full_hash, commit_date = marker.read_text().split()[:2]
    # Same length as InvenTree's own commit-hash lookup.
    commit_hash = full_hash[:7]
    return [
        f'INVENTREE_COMMIT_HASH={commit_hash}',
        f'INVENTREE_COMMIT_DATE={commit_date}',
    ]


def start_postgres(pg_image):
    """Start a new uniquely named pgvector container on a new internal network."""
    name = f'backend-fixes-pg-{uuid.uuid4().hex[:10]}'
    network = f'{name}-net'
    password = secrets.token_urlsafe(24)
    image_id = subprocess.check_output(
        ['docker', 'image', 'inspect', pg_image, '--format', '{{.Id}}'], text=True
    ).strip()
    subprocess.run(
        ['docker', 'network', 'create', '--internal', network],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    resources = {'container': None, 'network': network, 'image_id': image_id}
    subprocess.run(
        [
            'docker',
            'run',
            '-d',
            '--pull',
            'never',
            '--name',
            name,
            '--network',
            network,
            '--network-alias',
            'backend-fixes-pg',
            # Data stays on the container's own disk (removed with it) rather
            # than a RAM tmpfs; durability is irrelevant for a throwaway run.
            '--memory',
            '1g',
            '-e',
            'POSTGRES_USER=fixuser',
            '-e',
            f'POSTGRES_PASSWORD={password}',
            '-e',
            'POSTGRES_DB=inventree',
            image_id,
            '-c',
            'fsync=off',
            '-c',
            'synchronous_commit=off',
            '-c',
            'full_page_writes=off',
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    resources['container'] = name
    for _attempt in range(60):
        ready = subprocess.run(
            ['docker', 'exec', name, 'pg_isready', '-U', 'fixuser', '-d', 'inventree'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if ready.returncode == 0:
            break
        time.sleep(1)
    # The entrypoint restarts the server once after init; wait for the final one.
    time.sleep(3)
    for database in ('template1', 'inventree'):
        subprocess.run(
            [
                'docker',
                'exec',
                name,
                'psql',
                '-U',
                'fixuser',
                '-d',
                database,
                '-c',
                'CREATE EXTENSION IF NOT EXISTS vector',
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    environment = [
        'INVENTREE_DB_ENGINE=postgresql',
        'INVENTREE_DB_HOST=backend-fixes-pg',
        'INVENTREE_DB_PORT=5432',
        'INVENTREE_DB_USER=fixuser',
        f'INVENTREE_DB_PASSWORD={password}',
        'INVENTREE_DB_NAME=inventree',
    ]
    return resources, environment


def stop_postgres(resources):
    """Remove only the container and network this run created."""
    cleanup = dict(resources)
    if resources.get('container'):
        cleanup['container_remove_exit'] = subprocess.run(
            ['docker', 'rm', '-f', '-v', resources['container']],
            check=False,
            stdout=subprocess.DEVNULL,
        ).returncode
    cleanup['network_remove_exit'] = subprocess.run(
        ['docker', 'network', 'rm', resources['network']],
        check=False,
        stdout=subprocess.DEVNULL,
    ).returncode
    return cleanup


def run_host(labels, image, output, test_args, plugins, pg_image, memory):
    """Launch only new test containers and retain their synthetic output."""
    root = Path(__file__).resolve().parents[3]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    image_id = subprocess.check_output(
        ['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], text=True
    ).strip()
    log = output / 'backend.log'
    if log.exists():
        history = (
            output
            / 'history'
            / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        )
        history.mkdir(parents=True)
        for name in ('backend.log', 'manifest.json', 'cleanup.json'):
            previous = output / name
            if previous.exists():
                shutil.copy2(previous, history / name)
    resources, database_env = None, []
    cleanup = None
    try:
        if pg_image:
            resources, database_env = start_postgres(pg_image)
        command = [
            'docker',
            'run',
            '--rm',
            '--pull',
            'never',
            '--network',
            resources['network'] if resources else 'none',
            '--read-only',
            '--memory',
            memory,
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
            f'INVENTREE_PLUGINS_ENABLED={"true" if plugins else "false"}',
            *git_metadata(root),
            *database_env,
            'python3',
            '/s0/backend_baseline.py',
            '--inside',
            *[f'--test-arg={arg}' for arg in test_args],
            *labels,
        ]
        print(f'Running {len(labels)} test labels in {image_id}', flush=True)
        with log.open('w') as stream:
            result = subprocess.run(
                command, stdout=stream, stderr=subprocess.STDOUT, check=False
            )
    finally:
        if resources:
            cleanup = stop_postgres(resources)
            (output / 'cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
    manifest = {
        'image_id': image_id,
        'base_revision': git_revision(root),
        'synthetic': True,
        'isolation': (
            'New container; internal-only network; new disposable PostgreSQL '
            f'container from {pg_image}, removed afterwards; clean environment; '
            'copied backend code; temporary config/media'
            if pg_image
            else 'New container; network none; clean environment; copied backend '
            'code; temporary SQLite/config/media'
        )
        + '; no existing DB or service lifecycle change',
        'plugins_enabled': plugins,
        'memory_limit': memory,
        'test_labels': labels,
        'test_args': test_args,
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
    parser.add_argument(
        '--postgres',
        nargs='?',
        const='pgvector/pgvector:pg15',
        help='Run against a new disposable PostgreSQL container (default image %(const)s)',
    )
    parser.add_argument(
        '--plugins',
        action='store_true',
        help='Enable plugins, as the upstream CI test jobs do',
    )
    parser.add_argument(
        '--test-arg',
        action='append',
        default=[],
        help='Extra argument for manage.py test, e.g. --test-arg=--exclude-tag=x',
    )
    parser.add_argument(
        '--memory',
        default='4g',
        help='Memory cap for the test container (default %(default)s)',
    )
    parser.add_argument('labels', nargs='*')
    arguments = parser.parse_args()
    labels = arguments.labels or DEFAULT_TESTS
    if arguments.inside:
        return run_inside(labels, arguments.test_arg)
    return run_host(
        labels,
        arguments.image,
        arguments.output,
        arguments.test_arg,
        arguments.plugins,
        arguments.postgres,
        arguments.memory,
    )


if __name__ == '__main__':
    raise SystemExit(main())
