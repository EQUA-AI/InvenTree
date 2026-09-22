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
# Wall-clock ceilings for the two calls that reach the network. Neither `az`
# nor `docker exec` imposes one of its own, and a call with no ceiling is how
# this loop dies: see run_limited below.
TOKEN_TIMEOUT="${TOKEN_TIMEOUT:-90}"
PUSH_TIMEOUT="${PUSH_TIMEOUT:-180}"

# Run a command under a wall-clock limit. Returns the command's own status, or
# 124 if it was killed for outliving the limit (matching timeout(1)).
#
# This exists because there is no timeout(1) to call. macOS ships none, and
# gtimeout arrives only with GNU coreutils, which is not a dependency this loop
# can assume - so the watchdog is written out by hand in POSIX sh.
#
# Why a watchdog at all: `set -e` is absent here deliberately, so a *failing*
# cycle cannot kill the loop. But a *hanging* cycle is the worse case and was
# unguarded. Observed 2026-09-21: `az account get-access-token` sat blocked for
# 10h11m after the host slept mid-call and left it holding a dead socket. The
# loop never reached `sleep`, never logged, and never refreshed the window, so
# `ps` showed a healthy supervisor while every reading aged out to stale. That
# is precisely the symptom this script exists to prevent, arriving through the
# one door it did not watch. A hang must be converted into a failed cycle,
# because a failed cycle is already handled and recovers on the next pass.
run_limited() {
    _limit=$1
    shift
    # A marker file, rather than inspecting whether the watchdog is still
    # alive: after the watchdog fires it stays alive through its TERM/KILL
    # grace period, so "watchdog running" and "watchdog fired" overlap and
    # cannot be told apart by liveness. The marker cannot be ambiguous.
    _marker="${TMPDIR:-/tmp}/keep_live_fresh.timeout.$$"
    rm -f "$_marker"

    "$@" &
    _cmd_pid=$!
    (
        _waited=0
        while [ "$_waited" -lt "$_limit" ]; do
            kill -0 "$_cmd_pid" 2>/dev/null || exit 0
            sleep 1
            _waited=$((_waited + 1))
        done
        : > "$_marker"
        # Ask, then insist. A wedged az holding a dead socket ignores TERM.
        kill -TERM "$_cmd_pid" 2>/dev/null
        sleep 2
        kill -KILL "$_cmd_pid" 2>/dev/null
    ) &
    _watchdog_pid=$!

    wait "$_cmd_pid" 2>/dev/null
    _status=$?

    kill "$_watchdog_pid" 2>/dev/null
    wait "$_watchdog_pid" 2>/dev/null

    if [ -f "$_marker" ]; then
        rm -f "$_marker"
        return 124
    fi

    return "$_status"
}

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
tokfile="${TMPDIR:-/tmp}/keep_live_fresh.token.$$"
trap 'rm -f "$tokfile"' EXIT HUP INT TERM

while true; do
    # Via a file rather than $(...): command substitution keeps the pipe open
    # until every process sharing it exits, which would couple the watchdog's
    # lifetime to the token read.
    run_limited "$TOKEN_TIMEOUT" az account get-access-token \
        --resource "https://${ACCOUNT}.documents.azure.com" \
        --query accessToken -o tsv >"$tokfile" 2>/dev/null
    token_status=$?
    if [ "$token_status" -eq 0 ]; then
        token=$(cat "$tokfile" 2>/dev/null)
    else
        token=''
    fi
    : > "$tokfile"

    if [ "$token_status" -eq 124 ]; then
        fails=$((fails + 1))
        echo "$(date -u +%H:%M:%S) TOKEN TIMED OUT after ${TOKEN_TIMEOUT}s ($fails in a row)" \
             "- killed and will retry; a token call that blocks forever is what" \
             "silently froze this loop before" >&2
    elif [ -z "$token" ]; then
        fails=$((fails + 1))
        echo "$(date -u +%H:%M:%S) TOKEN FAILED ($fails in a row) - is 'az login' still valid?" >&2
    else
        run_limited "$PUSH_TIMEOUT" docker exec \
            -e COSMOS_ACCESS_TOKEN="$token" \
            -e COSMOS_ACCESS_TOKEN_EXPIRES="$(( $(date +%s) + 3000 ))" \
            "$CONTAINER_NAME" sh -c "cd /home/inventree && \
                python contrib/cosmos/devtools/push_to_live.py \
                --endpoint '$ENDPOINT' \
                --source-endpoint '$SOURCE_ENDPOINT' \
                --hours $WINDOW_HOURS --confirm" >/dev/null 2>&1
        push_status=$?

        if [ "$push_status" -eq 0 ]; then
            if [ "$fails" -gt 0 ]; then
                echo "$(date -u +%H:%M:%S) recovered after $fails failed cycle(s)"
            fi
            fails=0
            echo "$(date -u +%H:%M:%S) pushed ${WINDOW_HOURS}h window to $ACCOUNT"
        elif [ "$push_status" -eq 124 ]; then
            fails=$((fails + 1))
            echo "$(date -u +%H:%M:%S) PUSH TIMED OUT after ${PUSH_TIMEOUT}s ($fails in a row)" >&2
        else
            fails=$((fails + 1))
            echo "$(date -u +%H:%M:%S) PUSH FAILED ($fails in a row)" >&2
        fi
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
