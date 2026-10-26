"""Run AI component/ORM tests using the disposable native test settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    """Bootstrap the native registry and run an explicit or saved selection."""
    settings = 'ai.core.tests.settings'
    if os.environ.get('DJANGO_SETTINGS_MODULE', settings) != settings:
        raise SystemExit(
            'This runner requires ai.core.tests.settings (disposable test state)'
        )
    os.environ['DJANGO_SETTINGS_MODULE'] = settings
    root = Path(__file__).resolve().parents[2]
    backend = root / 'src/backend'
    sys.path[:0] = [str(backend), str(backend / 'InvenTree')]
    os.chdir(backend / 'InvenTree/ai')
    arguments = (
        sys.argv[1:]
        or (root / 'contrib/testing/chat-voice-regression-modules.txt')
        .read_text()
        .splitlines()
    )
    # Native ready hooks recognize the test token before the app registry is
    # populated. The settings own scratch DB/config paths, not a business DB.
    sys.argv = ['pytest', 'test']
    import django

    django.setup()
    sys.argv = ['pytest']
    import pytest

    return pytest.main(['-c', 'pyproject.toml', *arguments])


if __name__ == '__main__':
    raise SystemExit(main())
