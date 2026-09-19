"""Estimate a Cassandra -> Cosmos backfill from measured numbers, not guesses.

Every input here was measured against the live container on 2026-09-19 rather
than assumed:

  document size   97,243 B   (data1_raw 50,066 + dex 45,676 + keys)
  write cost      96.76 RU   (median of three upserts of a real document)
  db throughput   400 RU/s   shared across the whole `aimms` database

The two size figures in play differ by ~24x, and which one is right changes the
answer from under a day to nearly three weeks. So both are reported rather than
one being picked.

Run:  python contrib/cosmos/devtools/estimate_backfill.py
"""

DOC_BYTES = 97_243
RU_PER_DOC = 96.76
GIB = 1024**3

# Cosmos pricing, East US pay-as-you-go, for order of magnitude only.
USD_PER_100_RU_HOUR = 0.008
USD_PER_GB_MONTH = 0.25


def report(label: str, docs: float, note: str = '') -> None:
    """Print runtime and cost for a given document count."""
    total_ru = docs * RU_PER_DOC
    stored_gb = docs * DOC_BYTES / GIB
    print(f'\n=== {label} ===')
    if note:
        print(f'    {note}')
    print(f'    documents:       {docs:>15,.0f}')
    print(f'    stored in Cosmos:{stored_gb:>15,.0f} GB')
    print(f'    total write RU:  {total_ru:>15,.0f}')
    print(f'    storage cost:    ${stored_gb * USD_PER_GB_MONTH:>14,.2f} / month')
    print('    runtime at sustained throughput:')
    for rps in (400, 4_000, 10_000, 40_000, 100_000):
        secs = total_ru / rps
        ru_cost = (rps / 100) * USD_PER_100_RU_HOUR * (secs / 3600)
        unit = (
            f'{secs / 3600:,.1f} h' if secs < 172_800 else f'{secs / 86400:,.1f} days'
        )
        print(f'      {rps:>7,} RU/s -> {unit:>12}   (~${ru_cost:,.2f} in RU)')


print('measured inputs')
print(f'  document size: {DOC_BYTES:,} B')
print(f'  write cost:    {RU_PER_DOC} RU')
print('  current ceiling: 400 RU/s, shared with the rest of the database')

# Scenario A: the 25 GB is the size once landed in Cosmos.
report(
    'A. 25 GB measured as Cosmos documents',
    25 * GIB / DOC_BYTES,
    'Treats 25 GB as the destination size. Optimistic: it assumes the '
    'Cassandra bytes expand 1:1, which the schema says they do not.',
)

# Scenario B: 13 stations at the observed 5 s cadence for 30 days.
CADENCE_S = 5
STATIONS = 13
report(
    'B. 13 stations x 5 s cadence x 30 days',
    (30 * 86400 / CADENCE_S) * STATIONS,
    'Derived from the cadence measured in the PH_3 pilot excerpt. This is '
    'what the row count implies regardless of the source-side byte count.',
)

print('\n--- why the two disagree ---')
implied = 25 * GIB / ((30 * 86400 / CADENCE_S) * STATIONS)
print(
    f'  25 GB over {(30 * 86400 / CADENCE_S) * STATIONS:,.0f} rows = {implied:,.0f} B/row'
)
print(
    f'  but one Cosmos document measures {DOC_BYTES:,} B  ({DOC_BYTES / implied:.0f}x larger)'
)
print('  Cassandra compresses SSTables (LZ4) and stores data1 once as text;')
print('  the Cosmos document stores that text *and* the parsed 845-tag dex.')
print('  data1_raw 50,066 B + dex 45,676 B = near-exact duplication.')
