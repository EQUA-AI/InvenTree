"""Django settings shim for the release-image whole-process probe.

Harness-only (never used by any deployment, never present in the production
image). Imports the standard InvenTree settings from the IMAGE-CONTAINED source
and fails closed unless the process is pointed at the DISPOSABLE release probe
database ``inventree_dm_release_probe_v1`` on the dedicated probe database host
``inventree_dm_release_probe_db`` — never the default/shared ``inventree``
database, never the dev bootstrap probe database, never a cloud database.

External-transport instrumentation (``net_guard``, reused unchanged from
``contrib/container/bootstrap_probe``) is installed here as a fallback for
entry paths that bypass ``sitecustomize``. It observes, blocks and records
outbound TCP connects only; it never suppresses database behavior. Database
writes are prevented solely by the server-side defense (the ``dm_release_ro``
role has SELECT-only grants and the probe database runs
``default_transaction_read_only=on``), so an attempted write fails loudly in
the server instead of being masked client-side.
"""

import sys
from pathlib import Path

from demo_metrics_release_guard import check_probe_db_host, check_probe_db_name

from InvenTree.settings import *  # noqa: F403

_db = DATABASES['default']  # noqa: F405
check_probe_db_name(_db['NAME'])
check_probe_db_host(_db.get('HOST', ''))

AIMMS_MAINTENANCE_SCOPE_RESOLVER = 'tasks.scope.granted_client_scope_resolver'

# Fallback guard installation (sitecustomize is the primary entry point; both
# are idempotent and fully inert without DM_BOOTSTRAP_NETLOG).
_probe_dir = str(Path(__file__).resolve().parent / 'bootstrap_probe')
if _probe_dir not in sys.path:
    sys.path.insert(0, _probe_dir)
import net_guard

net_guard.install()
