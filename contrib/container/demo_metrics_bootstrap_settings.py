"""Django settings shim for the whole-process bootstrap safety probe.

Harness-only (never used by any deployment). Imports the standard InvenTree
settings and fails closed unless the process is pointed at the DISPOSABLE
probe database ``inventree_dm_bootstrap_v1`` on the local dev PostgreSQL —
never the default database, never a cloud database. Enables the REAL
identity/scope mapping (explicit ``ClientScopeGrant`` rows via
``tasks.scope.granted_client_scope_resolver``), the same resolver the real
E2E harness uses: without it, scoped lookups are unresolved and every scoped
read denies.

External-transport instrumentation (``net_guard``) is installed here as a
fallback for entry paths that bypass ``sitecustomize``. It observes, blocks
and records outbound TCP connects only; it never suppresses database behavior.
Database writes are prevented solely by the server-side defense (the
``dm_bootstrap_ro`` role has SELECT-only grants and the probe database runs
``default_transaction_read_only=on``), so an attempted write fails loudly in
the server instead of being masked client-side.
"""

import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from InvenTree.settings import *  # noqa: F403

PROBE_DB_NAME = 'inventree_dm_bootstrap_v1'

_db = DATABASES['default']  # noqa: F405
if str(_db['NAME']) != PROBE_DB_NAME:
    raise ImproperlyConfigured(
        f'bootstrap probe refuses database {_db["NAME"]!r}; want {PROBE_DB_NAME!r}'
    )
_host = str(_db.get('HOST', ''))
if 'postgres.database.azure.com' in _host or _host.endswith('.database.windows.net'):
    raise ImproperlyConfigured(f'bootstrap probe refuses cloud database host {_host!r}')

AIMMS_MAINTENANCE_SCOPE_RESOLVER = 'tasks.scope.granted_client_scope_resolver'

# Fallback guard installation (sitecustomize is the primary entry point; both
# are idempotent and fully inert without DM_BOOTSTRAP_NETLOG).
_probe_dir = str(Path(__file__).resolve().parent / 'bootstrap_probe')
if _probe_dir not in sys.path:
    sys.path.insert(0, _probe_dir)
import net_guard

net_guard.install()
