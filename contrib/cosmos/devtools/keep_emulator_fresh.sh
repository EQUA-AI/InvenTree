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
# Run from the repository root. Local development only; it invents no values, it
# only moves the clock.
set -e
cd /home/inventree

SNAPSHOT="${SNAPSHOT:-contrib/pump-cassandra/PH_3.full-snapshot.json}"
FRESH="${FRESH:-data/ph3_snapshots.fresh.json}"

while true; do
    python contrib/cosmos/devtools/refresh_samples.py \
        --source "$SNAPSHOT" --target "$FRESH" 2>/dev/null
    if python contrib/cosmos/seed.py --emulator \
        --samples "$FRESH" >/dev/null 2>&1; then
        echo "$(date -u +%H:%M:%S) reseeded from $SNAPSHOT"
    else
        echo "$(date -u +%H:%M:%S) reseed FAILED from $SNAPSHOT"
    fi
    sleep 60
done
