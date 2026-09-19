#!/bin/sh
# Keep the live Cosmos container carrying a readable, recent window.
#
# push_to_live.py copies a point-in-time window. That is enough to prove a read
# path works, and not enough for anything you leave running: the source's
# freshness threshold is 300 s, so a copy stops looking live five minutes after
# it is made, and the trend chart's six-hour ceiling empties out six hours after
# that. This re-copies the tail every cycle so both stay true.
#
# It invents no values. It copies what the emulator's own freshness loop has
# already produced, so keep_emulator_fresh.sh must be running or there is
# nothing new to send.
#
# Why this runs on the host rather than in the container
# ------------------------------------------------------
#
# The Azure CLI is here; the Cosmos SDK is in the container. Neither side has
# both. So the token is minted here each cycle and handed to a container that
# does the work.
#
# Minting *each cycle* is the point. An access token lasts about an hour, so a
# loop that minted once would run fine through a demo and then fail silently
# overnight - and the symptom would be stale readings, which is exactly what
# this exists to prevent. Re-minting costs one CLI call a minute and removes a
# whole class of "it worked yesterday".
#
# There is deliberately no `set -e`. A single failed cycle - an expired login, a
# transient 429 - must not kill the loop, for the same reason recorded in
# keep_emulator_fresh.sh: a supervisor that dies on first error is worse than
# none, because it looks like one is running.
#
# Cost: the overlap window is re-upserted every cycle. Upserts are idempotent -
# the document id is the sample time - so this rewrites the same handful of
# documents rather than accumulating duplicates. At the emulator's one-a-minute
# cadence that is ~15 documents per cycle against a 400 RU/s shared throughput,
# which is why the window can afford to overlap instead of trying to track
# exactly what was sent last time.
#
# This writes to a real Azure account. Every document carries synthetic=true,
# and push_to_live.py --purge-synthetic removes exactly what this wrote.
#
# Run from the repository root.

ACCOUNT="${ACCOUNT:-epconchatcosmos9d6b}"
ENDPOINT="${ENDPOINT:-https://${ACCOUNT}.documents.azure.com:443/}"
CONTAINER_NAME="${CONTAINER_NAME:-inventree-inventree-dev-server-1}"
SOURCE_ENDPOINT="${SOURCE_ENDPOINT:-http://cosmos-emulator:8081}"
INTERVAL="${INTERVAL:-60}"
# Overlap deliberately: re-sending a few already-present documents is cheaper
# than tracking a cursor, and it self-heals a cycle that was missed.
WINDOW_HOURS="${WINDOW_HOURS:-0.25}"

if ! command -v az >/dev/null 2>&1; then
    echo "FATAL: az not found. This loop mints tokens on the host." >&2
    exit 1
fi

if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "FATAL: container not found: $CONTAINER_NAME" >&2
    exit 1
fi

echo "$(date -u +%H:%M:%S) starting; endpoint=$ENDPOINT interval=${INTERVAL}s window=${WINDOW_HOURS}h"

fails=0
while true; do
    token=$(az account get-access-token \
        --resource "https://${ACCOUNT}.documents.azure.com" \
        --query accessToken -o tsv 2>/dev/null)

    if [ -z "$token" ]; then
        fails=$((fails + 1))
        echo "$(date -u +%H:%M:%S) TOKEN FAILED ($fails in a row) - is 'az login' still valid?" >&2
    elif docker exec \
            -e COSMOS_ACCESS_TOKEN="$token" \
            -e COSMOS_ACCESS_TOKEN_EXPIRES="$(( $(date +%s) + 3000 ))" \
            "$CONTAINER_NAME" sh -c "cd /home/inventree && \
                python contrib/cosmos/devtools/push_to_live.py \
                --endpoint '$ENDPOINT' \
                --source-endpoint '$SOURCE_ENDPOINT' \
                --hours $WINDOW_HOURS --confirm" >/dev/null 2>&1; then
        if [ "$fails" -gt 0 ]; then
            echo "$(date -u +%H:%M:%S) recovered after $fails failed cycle(s)"
        fi
        fails=0
        echo "$(date -u +%H:%M:%S) pushed ${WINDOW_HOURS}h window to $ACCOUNT"
    else
        fails=$((fails + 1))
        echo "$(date -u +%H:%M:%S) PUSH FAILED ($fails in a row)" >&2
    fi

    # Derived from the real interval rather than hardcoded, so the warning stays
    # true if INTERVAL is changed.
    stale=$((fails * INTERVAL))
    if [ "$stale" -ge 300 ] && [ "$(((stale - INTERVAL)))" -lt 300 ]; then
        echo "$(date -u +%H:%M:%S) NOTE: the live window has been unrefreshed for" \
             "~${stale}s, past the 300 s freshness threshold; every reading there" \
             "now reads as stale" >&2
    fi

    sleep "$INTERVAL"
done
