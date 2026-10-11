#!/bin/sh
# Keep the local Cosmos emulator serving readings that look live.
#
# The committed pilot samples are fixed snapshots from July 2025. Seeding them
# once leaves every reading outside the 300 s freshness window within five
# minutes, which looks identical to a broken connector. This re-bases the same
# payloads onto the current clock every 60 s. It invents no values.
#
# SNAPSHOT selects which payload is rebased, and that decides how much of the UI
# has anything to show. The trimmed excerpt carries ~31 tags and populates only a
# few dozen of the 581 bindings, so the trend picker lists hundreds of
# parameters that never plot. The full snapshot carries all 845 tags and
# populates the lot, which is what the charts were built for.
#
# The default points at the *tracked, hash-pinned* snapshot under
# contrib/pump-cassandra rather than a working copy under data/, which is
# gitignored: two copies of the same artefact drift, and the dictionary records
# a provenance hash over this one.
#
# Override to go back to the smaller fixture:
#
#   SNAPSHOT=contrib/cosmos/samples/ph3_snapshots.json \
#       sh contrib/cosmos/devtools/keep_emulator_fresh.sh
#
# There is deliberately no `set -e` around the loop. An earlier version had one,
# and a single failed cycle - a transient error while the snapshot was being
# edited - killed the loop outright. The only symptom was a log that stopped, and
# five minutes later every reading was stale: precisely the "looks like a broken
# connector" state this script exists to prevent. A supervisor that dies on the
# first error is worse than no supervisor, because it looks like one is running.
# Each cycle is therefore independent and failures are announced, loudly and
# repeatedly, rather than being fatal.
#
# Run from the repository root. Local development only; it invents no values, it
# only moves the clock.

cd /home/inventree || exit 1

SNAPSHOT="${SNAPSHOT:-contrib/pump-cassandra/PH_3.full-snapshot.json}"
FRESH="${FRESH:-data/ph3_snapshots.fresh.json}"
INTERVAL="${INTERVAL:-60}"

if [ ! -f "$SNAPSHOT" ]; then
    echo "FATAL: snapshot not found: $SNAPSHOT" >&2
    exit 1
fi

echo "$(date -u +%H:%M:%S) starting; snapshot=$SNAPSHOT interval=${INTERVAL}s"

fails=0
while true; do
    if python contrib/cosmos/devtools/refresh_samples.py \
            --source "$SNAPSHOT" --target "$FRESH" 2>/dev/null \
        && python contrib/cosmos/seed.py --emulator \
            --samples "$FRESH" >/dev/null 2>&1; then
        if [ "$fails" -gt 0 ]; then
            echo "$(date -u +%H:%M:%S) recovered after $fails failed cycle(s)"
        fi
        fails=0
        echo "$(date -u +%H:%M:%S) reseeded from $SNAPSHOT"
    else
        fails=$((fails + 1))
        echo "$(date -u +%H:%M:%S) RESEED FAILED ($fails in a row) from $SNAPSHOT" >&2
        # Derived from the actual interval rather than hardcoded: a fixed
        # "~5 min" is simply untrue whenever INTERVAL is not 60.
        stale=$((fails * INTERVAL))
        if [ "$stale" -ge 300 ] && [ "$(((stale - INTERVAL)))" -lt 300 ]; then
            echo "$(date -u +%H:%M:%S) NOTE: readings have been stale for ~${stale}s," \
                 "past the 300 s freshness window;" \
                 "the UI is now indistinguishable from a dead connector" >&2
        fi
    fi
    sleep "$INTERVAL"
done
