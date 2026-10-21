"""Storage for retained capture originals; defaults to native server media.

Deployments may set AIMMS_CAPTURE_STORAGE_ALIAS to a configured Django
STORAGES alias before process startup. Originals use the native media volume;
changing the alias later requires migrating every existing capture-originals
path first (or mounting the new volume at the same native MEDIA_ROOT). It is
not a per-row storage router. Provisioning/moving storage is a deployment action.
"""

import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import storages


def original_storage():
    """Resolve the configured storage without any cloud-specific coupling."""
    return storages[getattr(settings, 'AIMMS_CAPTURE_STORAGE_ALIAS', 'default')]


def original_path(instance, filename):
    """Use an opaque server path; never accept a client directory."""
    return f'capture-originals/{uuid.uuid4().hex}{Path(filename).suffix.lower()[:12]}'
