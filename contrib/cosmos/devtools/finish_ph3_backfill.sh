#!/bin/sh
# Finish the Parvathi PH 10-day backfill, then put throughput back.
#
# The throughput restore is in a trap, not at the end of the happy path. A
# raised shared throughput is a standing charge - 3000 RU/s is roughly $175 a
# month against $23 at the 400 minimum - so "we forgot to lower it" is the
# expensive failure here, not a slow migration. The trap fires on normal exit,
# on error, and on Ctrl-C alike.
#
# Streams are relaunched rather than assumed to survive: earlier runs died on an
# expired AAD token and on transient read timeouts. Each stream keeps its own
# progress file, so a relaunch skips completed hours and costs only the partial
# hour it was in.
#
# Run from the repository root.

set -u

ACCOUNT=epconchatcosmos9d6b
RG=EpconChat
DB=aimms
RAISED=${RAISED:-3000}
FLOOR=${FLOOR:-400}
ATTEMPTS=${ATTEMPTS:-4}
SP=${SP:?set SP to the scratch directory holding s1/s2/s3.json}
PY="$SP/migvenv/bin/python"

restore() {
    echo "--- restoring throughput to ${FLOOR} RU/s ---"
    az cosmosdb sql database throughput update \
        --account-name "$ACCOUNT" -g "$RG" --name "$DB" \
        --throughput "$FLOOR" --query "resource.throughput" -o tsv 2>&1 | tail -1
}
trap restore EXIT HUP INT TERM

echo "--- raising throughput to ${RAISED} RU/s ---"
az cosmosdb sql database throughput update \
    --account-name "$ACCOUNT" -g "$RG" --name "$DB" \
    --throughput "$RAISED" --query "resource.throughput" -o tsv 2>&1 | tail -1

stream() {   # stream <n> <from> <to>
    CASSANDRA_KEYSPACE=cass_business_data_klsw \
    CASSANDRA_CONTACT_POINTS=127.0.0.1 \
    "$PY" -u contrib/cosmos/migrate_cassandra.py \
        --station 'Parvathi PH' --from "$2" --to "$3" \
        --endpoint "https://${ACCOUNT}.documents.azure.com:443/" \
        --progress "$SP/s$1.json" \
        --write-concurrency 8 --ru-cap 900 --confirm \
        >> "$SP/s$1.log" 2>&1 &
}

hours_done() {
    "$PY" - <<'PYEOF'
import glob, json, os
sp = os.environ['SP']
print(sum(len(json.load(open(f))['completed_hours'])
          for f in glob.glob(f'{sp}/s[123].json')))
PYEOF
}

attempt=1
while [ "$attempt" -le "$ATTEMPTS" ]; do
    before=$(SP="$SP" hours_done)
    echo "--- attempt ${attempt}: ${before}/209 hours done, launching 3 streams ---"

    stream 1 2025-07-03T07:00:00 2025-07-06T05:00:00
    stream 2 2025-07-06T05:00:00 2025-07-09T03:00:00
    stream 3 2025-07-09T03:00:00 2025-07-12T00:00:00
    wait

    after=$(SP="$SP" hours_done)
    echo "--- attempt ${attempt} ended: ${before} -> ${after} hours ---"

    if [ "$after" -ge 209 ]; then
        echo "--- all hours complete ---"
        break
    fi
    if [ "$after" -le "$before" ]; then
        # No forward progress: relaunching will not help, so stop rather than
        # spin. The trap still restores throughput.
        echo "--- no progress this attempt; stopping so the cause is visible ---"
        tail -3 "$SP"/s1.log "$SP"/s2.log "$SP"/s3.log
        break
    fi
    attempt=$((attempt + 1))
done

echo "--- final: $(SP="$SP" hours_done)/209 hours ---"
