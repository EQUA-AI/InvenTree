"""Read-only database fingerprint for the whole-process bootstrap probe.

Run via ``manage.py shell -c exec(open(...).read())`` under the probe settings
shim. Performs SELECT-only queries: per-table row counts over every table in
the connected schema, plus the session's read-only evidence
(``SHOW default_transaction_read_only``, ``SELECT current_user,
current_database()``). The orchestrator compares the ``before`` and ``after``
fingerprints across the read-only command window: any bootstrap write anywhere
in a probe process would change a count (and would also have been rejected by
the server-side read-only defense).
"""

import json
import os

from django.db import connection

state = os.environ['DM_BOOTSTRAP_STATE_DIR']
tag = os.environ['DM_BOOTSTRAP_FP_TAG']

with connection.cursor() as cur:
    cur.execute('SHOW default_transaction_read_only')
    tx_ro = cur.fetchone()[0]
    cur.execute('SELECT current_user, current_database()')
    user, database = cur.fetchone()
    tables = connection.introspection.table_names()
    counts = {}
    for table in sorted(tables):
        cur.execute(f'SELECT count(*) FROM "{table}"')
        counts[table] = cur.fetchone()[0]

out = {
    'tag': tag,
    'database': database,
    'user': user,
    'default_transaction_read_only': tx_ro,
    'table_count': len(counts),
    'total_rows': sum(counts.values()),
    'tables': counts,
}
path = os.path.join(state, f'fingerprint_{tag}.json')
with open(path, 'w', encoding='utf-8') as handle:
    json.dump(out, handle, indent=2, sort_keys=True)

print(
    'fingerprint',
    tag,
    '| database:',
    database,
    '| user:',
    user,
    '| default_transaction_read_only:',
    tx_ro,
    '| tables:',
    len(counts),
    '| rows:',
    out['total_rows'],
)
