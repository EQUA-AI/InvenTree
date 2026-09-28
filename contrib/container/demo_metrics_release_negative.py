"""Negative probes for the release-image whole-process probe.

Harness-only. Run via ``manage.py shell`` in a fresh process under the same
settings shim / guard / read-only connection as the real probe runs, INSIDE the
release candidate image.

1. An ORM write is attempted against the probe database. The server-side
   defense (SELECT-only ``dm_release_ro`` role +
   ``default_transaction_read_only=on``) must reject it, and the rejection
   must be a REAL server read-only/privilege refusal: the caught exception's
   chained graph (``__cause__``/``__context__``) must carry SQLSTATE 25006 or
   42501. ANY other exception (import error, timeout, unrelated database
   error) is NOT proof and FAILS the probe. If the write unexpectedly
   succeeds, the probe FAILS (and the disposable database would contain a
   ``dm-release-negative-probe`` row to prove it). The matching server-side
   refusal is validated SEPARATELY by the orchestrator against the captured
   PostgreSQL statement log.
2. An outbound TCP connect to a documentation-only address (203.0.113.1:9) is
   attempted. The transport guard must block and record it.

Exit code 0 means both attempts were caught. Nothing here is monkeypatched to
pass: the write defense is enforced by the server, the network defense records
every attempt in the process netlog.
"""

import json
import os
import socket
import sys

from django.db import connection

import demo_metrics_release_guard as guard

results = {}

# --- read-only evidence first (all SELECTs) --------------------------------
with connection.cursor() as cur:
    cur.execute('SHOW default_transaction_read_only')
    results['default_transaction_read_only'] = cur.fetchone()[0]
    cur.execute('SELECT current_user, current_database()')
    results['user'], results['database'] = cur.fetchone()
    cur.execute("SELECT has_table_privilege(current_user, 'assets_client', 'INSERT')")
    results['has_insert_privilege_on_assets_client'] = cur.fetchone()[0]

# --- negative 1: attempted database write must be caught server-side -------
caught = None
try:
    from assets.models import Client

    Client.objects.create(name='dm-release-negative-probe', code='dm-rp-neg')
except Exception as exc:
    # The catch is broad on purpose (nothing may escape the probe process),
    # but PROOF is strict: only a server read-only/privilege SQLSTATE counts.
    caught = exc
if caught is None:
    print('NEGATIVE_WRITE: NOT CAUGHT — server-side read-only defense FAILED')
    print(json.dumps(results, indent=2, sort_keys=True))
    sys.exit(1)
results['write_caught_as'] = type(caught).__name__
results['write_caught_message'] = str(caught)[:200]
try:
    refusal_sqlstate = guard.require_write_defense_refusal(caught)
except guard.ReleaseGuardError as exc:
    print('NEGATIVE_WRITE: CAUGHT but NOT a server read-only/privilege refusal')
    print(' ', exc)
    print(json.dumps(results, indent=2, sort_keys=True))
    sys.exit(1)
results['write_refusal_sqlstate'] = refusal_sqlstate
print('NEGATIVE_WRITE: CAUGHT as', type(caught).__name__)
print(f'write_refusal_sqlstate={refusal_sqlstate}')

# --- negative 2: attempted external connect must be blocked+recorded -------
netlog = os.environ.get('DM_BOOTSTRAP_NETLOG', '')
net_caught = None
try:
    sock = socket.create_connection(('203.0.113.1', 9), timeout=2)
    sock.close()
except OSError as exc:
    net_caught = exc
if net_caught is None:
    print('NEGATIVE_NET: NOT BLOCKED — transport guard FAILED')
    sys.exit(1)
results['net_blocked_as'] = type(net_caught).__name__
print('NEGATIVE_NET: BLOCKED as', type(net_caught).__name__)

if netlog and os.path.exists(netlog):
    with open(netlog, encoding='utf-8') as handle:
        content = handle.read()
    if 'guard_installed' not in content:
        print('GUARD: not installed in this process — probe INCONCLUSIVE')
        sys.exit(1)
    if '"blocked"' not in content or '203.0.113.1:9' not in content:
        print('NEGATIVE_NET: attempt was not recorded in the guard log')
        sys.exit(1)
    print('NEGATIVE_NET: attempt recorded in guard log')
else:
    print('GUARD: no netlog configured — probe INCONCLUSIVE')
    sys.exit(1)

print('NEGATIVE PROBES PASSED')
print(json.dumps(results, indent=2, sort_keys=True))
