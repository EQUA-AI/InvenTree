"""Django settings shim for the real demo-metrics E2E acceptance run.

Harness-only (never used by any deployment): imports the standard InvenTree
settings and enables the REAL identity/scope mapping — explicit
``ClientScopeGrant`` rows decide which maintenance client boundary an actor
holds (``tasks.scope.granted_client_scope_resolver``). Nothing here weakens
authentication, permissions or scope enforcement; without this resolver the
scope lookup would be unresolved and every scoped read would deny.

Used only by ``contrib/container/demo-metrics-e2e.sh`` for the dedicated
``inventree_dm_e2e_v3`` database run.
"""

import os as _os
import re as _re

from django.conf import settings as _django_settings

from InvenTree.settings import *  # noqa: F403

# Harness guardrails (never used by any deployment): only a disposable
# `inventree_dm_e2e_*` database on the dev container's own database host
# (`db`) is acceptable. The real `inventree` database, `test_*` databases,
# template databases, and any other host are refused before anything runs.
_DB_NAME = str(_django_settings.DATABASES['default']['NAME'])
if not _re.fullmatch(
    r'inventree_dm_e2e_[a-z0-9_]{1,40}', _DB_NAME
) or _DB_NAME.startswith('test_'):
    raise RuntimeError(f'refusing non-disposable database {_DB_NAME!r}')
_EXPECTED_HOST = _os.environ.get('DM_E2E_EXPECTED_DB_HOST', 'db')
_ACTUAL_HOST = str(_django_settings.DATABASES['default']['HOST'])
if _ACTUAL_HOST != _EXPECTED_HOST:
    raise RuntimeError(
        f'refusing database host {_ACTUAL_HOST!r}; want exactly {_EXPECTED_HOST!r}'
    )

AIMMS_MAINTENANCE_SCOPE_RESOLVER = 'tasks.scope.granted_client_scope_resolver'
