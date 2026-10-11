"""Estimate a Cassandra -> Cosmos backfill from measured numbers, not guesses.

Every input here was measured against the live container on 2026-09-19 rather
than assumed:

  document size   97,243 B   (data1_raw 50,066 + dex 45,676 + keys)
  write cost      96.76 RU   (median of three upserts of a real document)
  db throughput   400 RU/s   shared across the whole `aimms` database

The two size figures in play differ by ~24x, and which one is right changes the
answer from under a day to nearly three weeks. So both are reported rather than
one being picked.

A backfill costs about HALF that, corrected 2026-09-21
------------------------------------------------------

`96.76 RU` is the cost of **replacing a document that already exists**, which is
what "three upserts of a real document" measured. A backfill does not do that -
it inserts documents that are not there yet, and an insert is cheaper because
Cosmos is not also tearing down the old version's index entries.

Measured on 2026-09-21, one full previously-unmigrated hour of Parvathi PH
written to the live container:

  721 documents  ->  36,533 RU  ->  50.67 RU per insert

The replace figure was re-confirmed the same day and is equally real: 24 of 25
re-upserts of already-present documents charged **exactly** 96.76 RU. So both
numbers are correct and they measure different operations:

  first insert        50.67 RU   <- what a backfill pays, used below
  replace/re-run      96.76 RU   <- what re-running a completed range pays

That distinction is worth stating because it is easy to measure the wrong one.
Re-upserting existing documents to "check the cost" reports ~97 and makes a
backfill look twice as expensive as it is; both directions of that mistake were
made before this note was written.

Practically: a re-run over an already-migrated range costs ~1.9x the original
write. Re-runs are still safe and sometimes necessary - the document id is the
sample time, so they are idempotent - but they are not free, and resuming from a
progress file rather than restarting is the cheaper path by a wide margin.

Observed document size over 30 documents was 74,642-94,916 B (median 94,747), a
little under the 97,243 B above, so the storage figures here are slightly
conservative. Left as-is for that reason.

Run:  python contrib/cosmos/devtools/estimate_backfill.py
"""

DOC_BYTES = 97_243

# The cost of an INSERT, which is what a backfill issues. See the note above:
# replacing an existing document costs 96.76 RU, nearly twice as much.
RU_PER_DOC = 50.67
RU_PER_REPLACE = 96.76
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
print(
    f'  write cost:    {RU_PER_DOC} RU per insert '
    f'({RU_PER_REPLACE} RU to replace an existing document)'
)
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
