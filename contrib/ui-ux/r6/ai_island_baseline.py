"""Run selected ai/core pytest-island files in a disposable offline container.

The island has its own settings module (``ai.core.tests.settings``) and its
own pytest runner, so ``backend_baseline.py``'s Django runner cannot run it.
This launcher mirrors that script's isolation: a new container from a locally
present image (never pulled), no network, read-only root, a clean environment,
backend source mounted read-only and copied to tmpfs, and a task-owned output
directory. It installs nothing; the image must already contain pytest.
"""

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


def run_inside(paths):
    """Copy the backend to temporary storage and run pytest on the given files."""
    scratch = Path(tempfile.mkdtemp(prefix='aimms-r6-ai-'))
    backend = scratch / 'src' / 'backend'
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
    os.environ.update({
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONPATH': os.pathsep.join((str(backend / 'InvenTree'), str(backend))),
        'DJANGO_SETTINGS_MODULE': 'ai.core.tests.settings',
    })
    print(
        json.dumps({
            'synthetic': True,
            'network': 'none',
            'settings': 'ai.core.tests.settings (file-backed temporary SQLite)',
            'source': str(backend),
            'paths': paths,
        }),
        flush=True,
    )
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *paths],
        cwd=backend / 'InvenTree',
        check=False,
    )
    return result.returncode


def run_host(paths, image, output):
    """Launch one new container and retain its log and manifest."""
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
        '--mount',
        f'type=bind,src={Path(__file__).resolve()},dst=/s0/ai_island_baseline.py,readonly',
        '--entrypoint',
        '/usr/bin/env',
        image_id,
        '-i',
        'PATH=/usr/local/bin:/usr/bin:/bin',
        'HOME=/tmp',
        'PYTHONUSERBASE=/root/.local',
        'LANG=C.UTF-8',
        'python3',
        '/s0/ai_island_baseline.py',
        '--inside',
        *paths,
    ]
    print(f'Running {len(paths)} ai/core island paths in {image_id}', flush=True)
    log = output / 'island.log'
    if log.exists():
        history = (
            output
            / 'history'
            / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        )
        history.mkdir(parents=True)
        for name in ('island.log', 'manifest.json'):
            previous = output / name
            if previous.exists():
                shutil.copy2(previous, history / name)
    with log.open('w') as stream:
        result = subprocess.run(
            command, stdout=stream, stderr=subprocess.STDOUT, check=False
        )
    manifest = {
        'image_id': image_id,
        'image': image,
        'base_revision': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=root, text=True
        ).strip(),
        'synthetic': True,
        'isolation': 'New container; network none; read-only root; clean environment; copied backend code; island-owned temporary SQLite; no existing DB or service',
        'paths': paths,
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
    parser.add_argument('--image', default='aimms-merged-image:latest')
    parser.add_argument('--output', type=Path, required=False)
    parser.add_argument('paths', nargs='+')
    arguments = parser.parse_args()
    if arguments.inside:
        return run_inside(arguments.paths)
    if arguments.output is None:
        parser.error('--output is required: use a new task-owned directory')
    return run_host(arguments.paths, arguments.image, arguments.output)


if __name__ == '__main__':
    raise SystemExit(main())
