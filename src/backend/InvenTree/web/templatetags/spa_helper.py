"""Template tag to render SPA imports."""

import json
import json.decoder
from pathlib import Path
from typing import Optional

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

import structlog

logger = structlog.get_logger('inventree')
register = template.Library()

FRONTEND_SETTINGS = json.dumps(settings.FRONTEND_SETTINGS)


@register.simple_tag
def spa_bundle(manifest_path: str | Path = '', app: str = 'web'):
    """Render SPA bundle."""

    def get_url(file: str) -> str:
        """Get static url for file."""
        return f'{settings.STATIC_URL}{app}/{file}'

    def get_manifest() -> Optional[Path]:
        base_dir = Path(__file__).parent.parent

        # Caller provided an explicit manifest path
        if manifest_path:
            potential = Path(manifest_path)
            return potential if potential.exists() else None

        # Default location shipped with the backend package
        candidates = [
            base_dir / f'static/{app}/.vite/manifest.json',
            base_dir / f'static/{app}/manifest.json',
        ]

        # Fallback to STATIC_ROOT if the manifest was moved there (e.g. custom builds)
        if settings.STATIC_ROOT:
            candidates.append(
                Path(settings.STATIC_ROOT).joinpath(app, '.vite', 'manifest.json')
            )

        for candidate in candidates:
            if candidate.exists():
                return candidate

        return None

    manifest = get_manifest()

    if manifest is None:
        logger.error('Manifest file not found for app %s', app)
        return 'NOT_FOUND'

    try:
        manifest_data = json.load(manifest.open())
    except (TypeError, json.decoder.JSONDecodeError):
        logger.exception('Failed to parse manifest file')
        return ''

    return_string = ''
    # JS (based on index.html file as entrypoint)
    index = manifest_data.get('index.html')
    dynamic_files = index.get('dynamicImports', [])
    imports_files = ''.join([
        f'<script type="module" src="{get_url(manifest_data[file]["file"])}"></script>'
        for file in dynamic_files
    ])
    return_string += (
        f'<script type="module" src="{get_url(index["file"])}"></script>{imports_files}'
    )

    return mark_safe(return_string)


@register.simple_tag
def spa_settings():
    """Render settings for spa."""
    return mark_safe(
        f"""<script>window.INVENTREE_SETTINGS={FRONTEND_SETTINGS}</script>"""
    )
