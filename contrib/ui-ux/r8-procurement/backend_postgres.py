"""Disposable internal-network PostgreSQL integration verification; no env files."""

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

ROOT = (
    Path('/source')
    if sys.argv[1:2] == ['--inside']
    else Path(__file__).resolve().parents[3]
)
OUT = ROOT / 'data/devscratch/r8-int/postgres'


def inside():
    """Copy clean source and run migration checks, Django suites and email policy."""
    root = Path(tempfile.mkdtemp(prefix='r8-int-pg-'))
    backend = root / 'src/backend'
    shutil.copytree(
        '/source/backend',
        backend,
        ignore=shutil.ignore_patterns(
            '.env*',
            '*.sqlite*',
            '__pycache__',
            'media',
            'static',
            'config.yaml',
            'secret_key.txt',
        ),
    )
    for source in Path('/source/extra').rglob('*'):
        target = root / source.relative_to('/source/extra')
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    (root / 'VERSION').write_text('')
    cfg = root / 'config'
    cfg.mkdir()
    (cfg / 'config.yaml').write_text('{}\n')
    os.environ.update(
        PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=f'{backend / "InvenTree"}:{backend}:/s0',
        INVENTREE_CONFIG_FILE=str(cfg / 'config.yaml'),
        INVENTREE_SECRET_KEY_FILE=str(cfg / 'secret_key.txt'),
        INVENTREE_DB_ENGINE='postgresql',
        INVENTREE_DB_HOST='r8-pg',
        INVENTREE_DB_USER='r8user',
        INVENTREE_DB_NAME='r8_integration',
        INVENTREE_MEDIA_ROOT=str(root / 'media'),
        INVENTREE_STATIC_ROOT=str(root / 'static'),
        INVENTREE_BACKUP_DIR=str(root / 'backup'),
        INVENTREE_PLUGIN_FILE=str(cfg / 'plugins.yaml'),
        INVENTREE_SITE_URL='http://localhost',
        INVENTREE_DEBUG='true',
        INVENTREE_LOG_LEVEL='WARNING',
        INVENTREE_CONSOLE_LOG='false',
        INVENTREE_PLUGINS_ENABLED='false',
        INVENTREE_AUTO_UPDATE='false',
        INVENTREE_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        AIMMS_SINGLE_SITE_POLICY_KEY='r8-fixture-policy',
    )
    cwd = backend / 'InvenTree'
    commands = [
        ['makemigrations', '--check', '--dry-run', '--noinput'],
        [
            'shell',
            '-c',
            "from django.db.migrations.loader import MigrationLoader; from django.db import connection; l=MigrationLoader(connection); print('LEAVES',l.graph.leaf_nodes()); print('CONFLICTS',l.detect_conflicts())",
        ],
        [
            'test',
            *sys.argv[2:],
            '--keepdb',
            '--noinput',
            '--verbosity=2',
            '--testrunner=django.test.runner.DiscoverRunner',
        ],
        [
            'shell',
            '-c',
            "from django.db import connection; assert connection.settings_dict['HOST'] == 'r8-pg' and connection.settings_dict['NAME'] == 'r8_integration'; connection.cursor().execute('DROP DATABASE IF EXISTS test_r8_integration WITH (FORCE)')",
        ],
        [
            'test',
            'tasks.tests.r8_postgres_concurrency',
            '--keepdb',
            '--noinput',
            '--verbosity=2',
            '--testrunner=django.test.runner.DiscoverRunner',
        ],
    ]
    results = []
    for index, args in enumerate(commands):
        with open(f'/evidence/check-{index}.log', 'w', encoding='utf-8') as stream:
            result = subprocess.run(
                [sys.executable, 'manage.py', *args],
                check=False,
                cwd=cwd,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        results.append({'command': args, 'exit_code': result.returncode})
        print(json.dumps(results[-1]), flush=True)
    with open('/evidence/email-policy.log', 'w', encoding='utf-8') as stream:
        policy = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                'ai/core/tests/test_email_recipient_policy.py',
                '-q',
            ],
            check=False,
            cwd=cwd,
            env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'ai.core.tests.settings'},
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    results.append({
        'command': ['pytest', 'ai/core/tests/test_email_recipient_policy.py'],
        'exit_code': policy.returncode,
    })
    Path('/evidence/results.json').write_text(
        json.dumps(results, indent=2) + '\n', encoding='utf-8'
    )
    return int(any(row['exit_code'] for row in results))


def host():
    """Create only new disposable resources and always remove those resources."""
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'runner.log').exists():
        history = OUT / 'history' / str(time.time_ns())
        history.mkdir(parents=True)
        for old in OUT.glob('*'):
            if old.is_file():
                shutil.copy2(old, history / old.name)
    suffix = uuid.uuid4().hex[:10]
    name = 'r8-int-pg-' + suffix
    network = name + '-net'
    password = secrets.token_urlsafe(24)
    labels = sys.argv[1:] or [
        'tasks',
        'order',
        'approvals',
        'users',
        'aichat.tests.test_email_contracts',
        'aichat.tests.test_flag_bridge',
    ]
    app_image = subprocess.check_output(
        [
            'docker',
            'image',
            'inspect',
            os.environ.get('R8_TEST_IMAGE', 'inventree-dev-image:latest'),
            '--format',
            '{{.Id}}',
        ],
        text=True,
    ).strip()
    pg_image = subprocess.check_output(
        ['docker', 'image', 'inspect', 'pgvector/pgvector:pg16', '--format', '{{.Id}}'],
        text=True,
    ).strip()

    def run(args, **kwargs):
        return subprocess.run(args, check=True, **kwargs)

    run(
        ['docker', 'network', 'create', '--internal', network],
        stdout=subprocess.DEVNULL,
    )
    pg_created = False
    try:
        run(
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
                'r8-pg',
                '--tmpfs',
                '/var/lib/postgresql/data:rw',
                '-e',
                'POSTGRES_USER=r8user',
                '-e',
                'POSTGRES_PASSWORD=' + password,
                '-e',
                'POSTGRES_DB=r8_integration',
                pg_image,
            ],
            stdout=subprocess.DEVNULL,
        )
        pg_created = True
        for _ in range(60):
            if (
                subprocess.run(
                    ['docker', 'exec', name, 'pg_isready', '-U', 'r8user'],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            ):
                break
            time.sleep(1)
        for db in ('template1', 'r8_integration'):
            run(
                [
                    'docker',
                    'exec',
                    name,
                    'psql',
                    '-U',
                    'r8user',
                    '-d',
                    db,
                    '-c',
                    'CREATE EXTENSION IF NOT EXISTS vector',
                ],
                stdout=subprocess.DEVNULL,
            )
        args = [
            'docker',
            'run',
            '--rm',
            '--pull',
            'never',
            '--network',
            network,
            '--read-only',
            '--cap-drop',
            'ALL',
            '--security-opt',
            'no-new-privileges',
            '--tmpfs',
            '/tmp:rw,nosuid,size=3g',
            '--mount',
            f'type=bind,src={ROOT / "src/backend"},dst=/source/backend,readonly',
            '--mount',
            f'type=bind,src={Path(__file__).resolve()},dst=/s0/run_postgres.py,readonly',
            '--mount',
            f'type=bind,src={ROOT / "contrib/ui-ux/r6/s0_scope_probe.py"},dst=/s0/s0_scope_probe.py,readonly',
            '--mount',
            f'type=bind,src={OUT},dst=/evidence',
        ]
        for source in (
            'contrib/cosmos',
            'contrib/pump-cassandra',
            'contrib/container/dev-docker-compose.yml',
            'src/frontend/src/assets/mimic',
        ):
            args += [
                '--mount',
                f'type=bind,src={ROOT / source},dst=/source/extra/{source},readonly',
            ]
        args += [
            '--entrypoint',
            '/usr/bin/env',
            app_image,
            '-i',
            'PATH=/usr/local/bin:/usr/bin:/bin',
            'HOME=/tmp',
            'PYTHONUSERBASE=/root/.local',
            'LANG=C.UTF-8',
            'INVENTREE_DB_PASSWORD=' + password,
            'python3',
            '/s0/run_postgres.py',
            '--inside',
            *labels,
        ]
        with (OUT / 'runner.log').open('w') as stream:
            code = subprocess.run(
                args, stdout=stream, stderr=subprocess.STDOUT, check=False
            ).returncode
        (OUT / 'isolation.json').write_text(
            json.dumps(
                {
                    'container': name,
                    'network': network,
                    'image': 'pgvector/pgvector:pg16',
                    'pg_image_id': pg_image,
                    'app_image_id': app_image,
                    'external_network': False,
                    'exit_code': code,
                },
                indent=2,
            )
            + '\n'
        )
        return code
    finally:
        pg_code = None
        if pg_created:
            pg_code = subprocess.run(
                ['docker', 'rm', '-f', name], check=False, stdout=subprocess.DEVNULL
            ).returncode
        network_code = subprocess.run(
            ['docker', 'network', 'rm', network], check=False, stdout=subprocess.DEVNULL
        ).returncode
        (OUT / 'cleanup.json').write_text(
            json.dumps(
                {
                    'container': name,
                    'container_remove_exit': pg_code,
                    'network': network,
                    'network_remove_exit': network_code,
                },
                indent=2,
            )
            + '\n'
        )


if __name__ == '__main__':
    sys.exit(inside() if sys.argv[1:2] == ['--inside'] else host())
