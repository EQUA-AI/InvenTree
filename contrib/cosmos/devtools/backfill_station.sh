#!/bin/sh
# Backfill one or more stations over the same window, then restore throughput.
#
# Generalised from finish_ph3_backfill.sh, which was written for a single
# station. Same two hard-won properties:
#
# 1. The throughput restore is a trap, not the last line of the happy path. A
#    raised shared throughput is a standing charge (~$175/month at 3000 RU/s
#    against $23 at the 400 minimum), so "we forgot to lower it" is the
#    expensive failure, not a slow migration. The trap fires on success, on
#    error and on Ctrl-C alike.
# 2. Streams are relaunched rather than trusted to survive. Runs have died on
#    an expired AAD token and on transient read timeouts. Each stream keeps its
#    own progress file, so a relaunch skips completed hours and redoes only the
#    partial hour it was in.
#
# Stations are done one at a time. Their Cassandra partitions are shared, so
# reads are duplicated, but reads are not the bottleneck - a partition serves
# in ~0.2 s at 340 MB/s, while writes run at tens of documents a second.
#
# Usage, from the repository root:
#   SP=<scratch dir> sh contrib/cosmos/devtools/backfill_station.sh \
#      2025-07-02T00:00:00 2025-07-12T00:00:00 'Saraswati PH' 'Ranganayaka Sagar PH'

set -u

ACCOUNT=epconchatcosmos9d6b
RG=EpconChat
DB=aimms
RAISED=${RAISED:-3000}
FLOOR=${FLOOR:-400}
CAP=${CAP:-900}
ATTEMPTS=${ATTEMPTS:-4}
BACKOFF=${BACKOFF:-60}
CASS_WAIT=${CASS_WAIT:-900}
# Optional Cassandra partition override. Most stations' rows sit in the shared
# regional partition the estate names; Annapurna PH (Pine Hollow) has none
# there and is filed under its own uuid, so it needs PARENT=<its uuid>.
PARENT=${PARENT:-}
SP=${SP:?set SP to a scratch directory}
PY="$SP/migvenv/bin/python"

FROM=$1; shift
TO=$1; shift
[ "$#" -ge 1 ] || { echo "give at least one station name" >&2; exit 2; }

# Wait until Cassandra accepts connections on 9042, up to CASS_WAIT seconds.
# Without this, a dead node makes every attempt fail within seconds and the
# retry budget is spent before Docker has even restarted it (2026-10-12: all
# 4 x 3 stations "finished" in under a minute after an OOM kill).
wait_cassandra() {
    waited=0
    while ! "$PY" -c "import socket; socket.create_connection(('127.0.0.1', 9042), 3).close()" 2>/dev/null; do
        [ "$waited" -ge "$CASS_WAIT" ] && { echo "--- cassandra unreachable for ${CASS_WAIT}s ---"; return 1; }
        [ "$waited" -eq 0 ] && echo "--- waiting for cassandra on 127.0.0.1:9042 ---"
        sleep 15; waited=$((waited + 15))
    done
    [ "$waited" -gt 0 ] && { echo "--- cassandra back after ~${waited}s, settling ---"; sleep 30; }
    return 0
}

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

# Three streams over disjoint thirds of the window. Splitting by time rather
# than by station keeps each stream's progress file independent, which is what
# makes a relaunch cheap.
thirds() {
    "$PY" - "$FROM" "$TO" <<'PYEOF'
import sys, datetime as dt
f = dt.datetime.fromisoformat(sys.argv[1]).replace(tzinfo=dt.timezone.utc)
t = dt.datetime.fromisoformat(sys.argv[2]).replace(tzinfo=dt.timezone.utc)
hours = int((t - f).total_seconds() // 3600)
step = -(-hours // 3)                      # ceil, so the last third is short
for i in range(3):
    a = f + dt.timedelta(hours=i * step)
    b = min(f + dt.timedelta(hours=(i + 1) * step), t)
    if a < b:
        print(f'{a:%Y-%m-%dT%H:%M:%S} {b:%Y-%m-%dT%H:%M:%S}')
PYEOF
}

for station in "$@"; do
    slug=$(printf '%s' "$station" | tr -cd '[:alnum:]' | cut -c1-16)
    echo "=========================================================="
    echo "=== $station"
    echo "=========================================================="

    # Materialise the windows to a file first. `thirds | while read` would run
    # the loop in a SUBSHELL, so the background jobs it starts are not children
    # of this shell and `wait` returns immediately - the retry loop then fires
    # again at once. That bug launched 24 concurrent streams instead of 3
    # before it was caught.
    plan="$SP/${slug}.windows"
    thirds > "$plan"

    attempt=1
    while [ "$attempt" -le "$ATTEMPTS" ]; do
        # Defined above but was never called; without it a dead node still
        # spends the whole retry budget in seconds.
        wait_cassandra || exit 1
        : > "$SP/${slug}_1.log"; : > "$SP/${slug}_2.log"; : > "$SP/${slug}_3.log"
        n=0
        while read -r a b; do
            n=$((n + 1))
            CASSANDRA_KEYSPACE=cass_business_data_klsw \
            CASSANDRA_CONTACT_POINTS=127.0.0.1 \
            "$PY" -u contrib/cosmos/migrate_cassandra.py \
                --station "$station" --from "$a" --to "$b" \
                ${PARENT:+--parent-entity-uuid "$PARENT"} \
                --endpoint "https://${ACCOUNT}.documents.azure.com:443/" \
                --progress "$SP/${slug}_$n.json" \
                --write-concurrency 8 --ru-cap "$CAP" --confirm \
                >> "$SP/${slug}_$n.log" 2>&1 &
        done < "$plan"
        echo "--- $station attempt ${attempt}: launched $n stream(s), waiting ---"
        wait

        # `cat | grep -c` rather than `grep -c file...`: the latter prints
        # "file:count" per file, which is not a number and breaks the test.
        # A stream that crashed (Traceback: lost Cassandra, expired token)
        # failed just as much as one that printed "Refusing"; count both, or
        # the station is declared done with hours missing.
        # Only the stream logs `_<digit>.log`: a broad `_*.log` also matched an
        # archived manual-relaunch log and failed 20 attempts of a station that
        # had in fact finished (2026-10-13).
        left=$(cat "$SP/${slug}_"[0-9].log 2>/dev/null | grep -cE "Refusing|Traceback" || true)
        echo "--- $station attempt ${attempt}: ${left} stream(s) ended on an error ---"
        [ "${left:-0}" -eq 0 ] && break
        attempt=$((attempt + 1))
        [ "$attempt" -le "$ATTEMPTS" ] && sleep "$BACKOFF"
    done
    # Never move on to the next station with hours missing: a later "all
    # stations processed" would then be a lie, which is how two runs were
    # wrongly reported complete. Stop non-zero; the trap still restores RU/s,
    # and a rerun resumes from the progress files.
    if [ "${left:-0}" -ne 0 ]; then
        echo "--- $station FAILED: streams still erroring after ${ATTEMPTS} attempt(s) ---"
        exit 1
    fi
    echo "--- $station done after ${attempt} attempt(s) ---"
done

echo "--- all stations processed ---"
