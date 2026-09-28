#!/usr/bin/env bash
# Real demo-metrics acceptance: PostgreSQL -> Django -> browser.
#
# Owns the full lifecycle of the real E2E run on the DEDICATED database
# `inventree_dm_e2e_v3` and a dedicated loopback port (8127):
#   0. preflight: refuse to run alongside anything not started by this run
#   1. create the dedicated database (idempotent; never touches or drops any
#      other database)
#   2. migrate ONLY that database
#   3. fixture setup + GOVERNED apply (`manage.py apply_demo_metrics` with the
#      approved plan hash — a SHA-256 integrity check over the canonical plan
#      body, i.e. an approval/integrity hash, NOT a cryptographic signature)
#      via contrib/container/demo_metrics_e2e_setup.py
#   4. production frontend build (`yarn build` -> web/static/web)
#   5. real Django server (settings shim enables the real ClientScopeGrant
#      identity/scope mapping) + loopback port publication through a raw-TCP
#      helper container
#   6. Playwright acceptance (no API mocks anywhere): the Desktop Chrome
#      project asserts the desktop UI; the Pixel 7 project asserts the REAL
#      MOBILE SHELL plus wire-level scoped API data — it is not a desktop-UI
#      check
#   7. teardown: ONLY resources started by this invocation — the recorded
#      runserver PID (cmdline-checked; never a broad pkill), this run's
#      helper container (unique per-run name), this run's scratch dir, and
#      the runtime credentials file. The dedicated database and state.json
#      (ids only) deliberately remain.
#
# Credentials: no database or user password appears anywhere in this script.
# The database step authenticates with the db container's own configured
# environment ($POSTGRES_USER / $POSTGRES_PASSWORD, consumed inside the
# container and never printed). The browser users are created by the setup
# with runtime-generated passwords that live only in a 0600 credentials file
# which teardown deletes again.
#
# Scratch/state mapping (nothing under shared /tmp):
#   - container-side staging: $DM_E2E_CTR_SCRATCH/dm_e2e_state-<runid>
#     (default /home/inventree/LocalTesting/... — the repo's gitignored local
#     scratch directory via the documented devcontainer mapping
#     `../:/home/inventree:z`, see .devcontainer/docker-compose.yml)
#   - host-side run state: $DM_E2E_STATE_DIR (default
#     ~/.hermes/cache/scratch/dm_e2e_state). The directory is 0700 and the
#     credentials file is 0600 FROM CREATION (the setup materializes it 0600
#     and this script verifies the mode before use) — never chmod'ed only
#     after the secrets already existed.
#
# A crashed run (SIGKILL) may leave `dm_e2e_portproxy_<runid>` containers or
# `LocalTesting/dm_e2e_state-<runid>` dirs behind; this harness deliberately
# refuses to touch resources it did not create — remove such leftovers
# manually (`docker rm -f <name>`, `rm -rf <dir>`).
#
# Usage: contrib/container/demo-metrics-e2e.sh [--skip-build] [--fresh] [--db NAME]
#   --fresh : require a NOT-yet-existing disposable database (created here,
#             never dropped) AND a genuinely fresh governed apply — the setup
#             refuses to silently reuse previously applied sessions.
#   --db    : disposable database name (allowlist `inventree_dm_e2e_*` only;
#             default inventree_dm_e2e_v3).
set -euo pipefail

DB="${DM_E2E_DB:-inventree_dm_e2e_v3}"
FRESH=0
SKIP_BUILD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    --fresh) FRESH=1 ;;
    --db) shift; DB="${1:?--db needs a database name}" ;;
    --db=*) DB="${1#--db=}" ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CTR=inventree_devcontainer-inventree-1
DBCTR=inventree_devcontainer-db-1
PORT=8127
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
STATE_DIR_HOST="${DM_E2E_STATE_DIR:-$HOME/.hermes/cache/scratch/dm_e2e_state-$RUN_ID}"
CTR_SCRATCH="${DM_E2E_CTR_SCRATCH:-/home/inventree/LocalTesting}"
STATE_DIR_CTR="$CTR_SCRATCH/dm_e2e_state-$RUN_ID"
STATE_DIR_CTR_HOST="$REPO/LocalTesting/dm_e2e_state-$RUN_ID"
SERVER_PIDFILE="$STATE_DIR_CTR/server.pid"
SERVER_LOG="$STATE_DIR_CTR/server.log"
PROXY_NAME="dm_e2e_portproxy_$RUN_ID"
PROXY_CREATED=0
CREDENTIALS_CREATED=0

# Strict disposable-name allowlist: only purpose-made `inventree_dm_e2e_*`
# databases qualify. The real `inventree` database, `test_*` databases and
# template databases are refused up front; nothing is ever dropped.
[[ "$DB" =~ ^inventree_dm_e2e_[a-z0-9_]{1,40}$ ]] || {
  echo "refusing database name '$DB': not a disposable inventree_dm_e2e_* name" >&2; exit 1; }
case "$DB" in
  inventree|postgres|template*|test_*) echo "refusing reserved database name '$DB'" >&2; exit 1 ;;
esac

PY=/home/inventree/dev/venv/bin/python
MANAGE="cd /home/inventree/src/backend/InvenTree && $PY manage.py"
CTR_ENV="-e INVENTREE_DB_NAME=$DB -e DJANGO_SETTINGS_MODULE=demo_metrics_e2e_settings -e PYTHONPATH=/home/inventree/contrib/container"

cleanup() {
  set +e
  echo "== teardown (only resources started by run $RUN_ID) =="
  # 1. this run's Django server: the exact recorded PID, and only when its
  #    cmdline still is the exact runserver we started (never a broad pkill).
  docker exec -e DM_E2E_PIDFILE="$SERVER_PIDFILE" -e DM_E2E_PORT="$PORT" "$CTR" bash -c '
    if [ -f "$DM_E2E_PIDFILE" ]; then
      pid="$(cat "$DM_E2E_PIDFILE")"
      if [ -n "$pid" ] && grep -qa manage.py "/proc/$pid/cmdline" 2>/dev/null \
         && grep -qa "0.0.0.0:$DM_E2E_PORT" "/proc/$pid/cmdline" 2>/dev/null; then
        kill "$pid" && echo "stopped owned runserver pid $pid"
      else
        echo "recorded pid [$pid] is not our exact runserver command; leaving it alone"
      fi
      rm -f "$DM_E2E_PIDFILE"
    fi' || true
  # 2. this run's helper container (unique per-run name, created by us).
  if [[ "$PROXY_CREATED" == 1 ]]; then
    docker rm -f "$PROXY_NAME" >/dev/null 2>&1 && echo "removed helper container $PROXY_NAME"
  fi
  # 3. this run's scratch dir (container path and its mapped host copy are
  #    the same tree); keep the server log as a run artifact.
  if [[ -d "$STATE_DIR_CTR_HOST" ]]; then
    mkdir -p "$STATE_DIR_HOST" && chmod 700 "$STATE_DIR_HOST"
    [[ -f "$STATE_DIR_CTR_HOST/server.log" ]] && \
      mv -f "$STATE_DIR_CTR_HOST/server.log" "$STATE_DIR_HOST/server-$RUN_ID.log"
    rm -rf "$STATE_DIR_CTR_HOST" || true
    docker exec "$CTR" rm -rf "$STATE_DIR_CTR" >/dev/null 2>&1 || true
    [[ -d "$STATE_DIR_CTR_HOST" ]] || echo "removed container-side scratch $STATE_DIR_CTR"
  fi
  # 4. runtime credentials: disposable passwords must not outlive the run.
  if [[ "$CREDENTIALS_CREATED" == 1 && -f "$STATE_DIR_HOST/credentials.json" ]]; then
    rm -f "$STATE_DIR_HOST/credentials.json" && echo "removed runtime credentials file"
  fi
  echo "teardown complete (dedicated database $DB and state.json kept)"
}


echo "== 0/6 preflight: refuse to touch anything not started by this run =="
if [[ -e "$STATE_DIR_HOST/credentials.json" ]]; then
  echo "refusing: pre-existing credentials file; choose a fresh state directory" >&2
  exit 1
fi
if docker ps -a --format '{{.Names}}' | grep -q '^dm_e2e_portproxy'; then
  echo "refusing: existing dm_e2e_portproxy* container(s) not created by run $RUN_ID:" >&2
  docker ps -a --format '{{.Names}}' | grep '^dm_e2e_portproxy' >&2 || true
  exit 1
fi
probe=$(curl -s --noproxy '*' -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/" || true)
[[ "$probe" == "000" ]] || {
  echo "refusing: host port $PORT already answers (HTTP $probe); not owned by this run" >&2
  exit 1
}
if docker exec "$CTR" bash -c "exec 3<>/dev/tcp/127.0.0.1/$PORT" >/dev/null 2>&1; then
  echo "refusing: something already listens on port $PORT inside $CTR; not owned by this run" >&2
  exit 1
fi

trap cleanup EXIT

echo "== 0b/6 bind-mount parity for harness files (docker cp on mismatch) =="
for f in demo_metrics_e2e_setup.py demo_metrics_e2e_settings.py demo_metrics_e2e_proxy.py; do
  host_sum="$(sha256sum "$REPO/contrib/container/$f" | cut -d' ' -f1)"
  ctr_sum="$(docker exec "$CTR" sha256sum "/home/inventree/contrib/container/$f" | cut -d' ' -f1)"
  if [[ "$host_sum" != "$ctr_sum" ]]; then
    echo "bind mount stale for $f (host ${host_sum:0:12} != container ${ctr_sum:0:12}); docker cp exact file"
    docker cp "$REPO/contrib/container/$f" "$CTR:/home/inventree/contrib/container/$f"
    ctr_sum="$(docker exec "$CTR" sha256sum "/home/inventree/contrib/container/$f" | cut -d' ' -f1)"
    [[ "$host_sum" == "$ctr_sum" ]] || { echo "hash parity failed after docker cp for $f" >&2; exit 1; }
    echo "hash parity restored for $f"
  fi
done

echo "== 1/6 dedicated database =="
# Authenticated with the db container's own configured credentials only —
# nothing is hardcoded here and nothing is printed.
docker exec -e DM_E2E_DB="$DB" -e DM_E2E_FRESH="$FRESH" "$DBCTR" bash -c '
  set -euo pipefail
  export PGPASSWORD="$POSTGRES_PASSWORD"
  if psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT datname FROM pg_database" | grep -Fxq "$DM_E2E_DB"; then
    if [ "$DM_E2E_FRESH" = 1 ]; then
      echo "refusing: --fresh requested but database $DM_E2E_DB already exists (existing databases are never dropped)" >&2
      exit 1
    fi
    echo "database $DM_E2E_DB already exists (idempotent; never dropped)"
  else
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE $DM_E2E_DB"
    echo "database $DM_E2E_DB created"
  fi'

echo "== 2/6 migrate (only $DB) =="
docker exec $CTR_ENV "$CTR" bash -lc "$MANAGE migrate --noinput"

echo "== 3/6 fixture setup + governed apply =="
docker exec $CTR_ENV -e DM_E2E_STATE_DIR="$STATE_DIR_CTR" -e DM_E2E_DB="$DB" \
  -e DM_E2E_REQUIRE_FRESH_APPLY="$FRESH" "$CTR" bash -lc \
  "$MANAGE shell -c \"exec(open('/home/inventree/contrib/container/demo_metrics_e2e_setup.py').read())\""

# Materialize run state on the host under the scratch dir via the documented
# mapping (container /home/inventree <-> $REPO). The host dir is 0700 BEFORE
# anything lands in it, and credentials.json arrives already 0600 (created
# 0600 at birth by the setup) — then the modes are verified, not just hoped.
mkdir -p "$STATE_DIR_HOST"
chmod 700 "$STATE_DIR_HOST"
if compgen -G "$STATE_DIR_CTR_HOST/*.json" >/dev/null; then
  mv "$STATE_DIR_CTR_HOST"/*.json "$STATE_DIR_HOST"/
else
  # Mapping not visible on this host: materialize via docker cp instead.
  docker cp "$CTR:$STATE_DIR_CTR/." "$STATE_DIR_HOST/"
fi
CREDENTIALS_CREATED=1
[[ "$(stat -c '%a' "$STATE_DIR_HOST")" == "700" ]] || {
  echo "state dir is not 0700: $STATE_DIR_HOST" >&2; exit 1; }
[[ "$(stat -c '%a' "$STATE_DIR_HOST/credentials.json")" == "600" ]] || {
  echo "credentials.json is not 0600: $STATE_DIR_HOST/credentials.json" >&2; exit 1; }

echo "== verifying governed-apply evidence in run state =="
python3 - "$STATE_DIR_HOST/state.json" "$FRESH" <<'PYEOF'
import json, sys
state = json.load(open(sys.argv[1]))
fresh = sys.argv[2] == '1'
apply_ev = state.get('apply', {})
for tag, ev in sorted(apply_ev.items()):
    print(f"apply[{tag}]: executed={ev.get('executed')} mode={ev.get('mode')} receipts={ev.get('receipts')}")
if fresh and not all(ev.get('executed') for ev in apply_ev.values()):
    raise SystemExit('--fresh run requires the governed apply to execute; run state shows reuse')
if not all((ev.get('receipts') or 0) >= 1 for ev in apply_ev.values()):
    raise SystemExit('governed apply receipts missing from run state')
PYEOF

if [[ "$SKIP_BUILD" == 0 ]]; then
  echo "== 4/6 production frontend build =="
  (cd "$REPO/src/frontend" && corepack yarn build)
else
  echo "== 4/6 production frontend build SKIPPED =="
fi

echo "== 5/6 real backend on loopback :$PORT =="
# Start the server with its PID recorded (exec keeps $$ as the runserver PID)
# so teardown can stop exactly this process and nothing else.
docker exec -d $CTR_ENV -e DM_E2E_PIDFILE="$SERVER_PIDFILE" -e DM_E2E_SERVER_LOG="$SERVER_LOG" \
  "$CTR" bash -lc '
  echo $$ > "$DM_E2E_PIDFILE"
  cd /home/inventree/src/backend/InvenTree
  exec '"$PY"' manage.py runserver 0.0.0.0:'"$PORT"' --noreload > "$DM_E2E_SERVER_LOG" 2>&1'
# Host->bridge traffic is filtered here, so publish the real backend on the
# host loopback port through a helper container (raw TCP only — it cannot
# fabricate API responses). The container name is unique to this run.
docker run -d --name "$PROXY_NAME" --network inventree_devcontainer_default \
  -p 127.0.0.1:$PORT:$PORT -v "$REPO/contrib/container:/harness:ro" \
  --entrypoint python3 inventree_devcontainer-inventree:latest \
  /harness/demo_metrics_e2e_proxy.py inventree $PORT >/dev/null
PROXY_CREATED=1
code=000
for _ in $(seq 1 40); do
  code=$(curl -s --noproxy '*' -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/" || true)
  [[ "$code" == "200" ]] && break
  sleep 3
done
if [[ "$code" != "200" ]]; then
  echo "backend did not become ready; owned server log follows" >&2
  docker exec "$CTR" cat "$SERVER_LOG" >&2 || true
  exit 1
fi
# Warm the plugin-heavy dev server so the app's bounded startup session check
# has a fair chance on a cold route (the same routes the SPA checks at boot).
for _ in 1 2 3; do
  curl -s --noproxy '*' -m 15 -o /dev/null "http://127.0.0.1:$PORT/web/" || true
  curl -s --noproxy '*' -m 15 -o /dev/null "http://127.0.0.1:$PORT/api/" || true
  curl -s --noproxy '*' -m 15 -o /dev/null "http://127.0.0.1:$PORT/api/user/me/" || true
  curl -s --noproxy '*' -m 15 -o /dev/null "http://127.0.0.1:$PORT/api/assets/demo-metrics/" || true
done

echo "== 6/6 Playwright acceptance (Desktop Chrome UI + Pixel 7 mobile shell/wire-level) =="
(cd "$REPO/src/frontend" && \
  DM_E2E_STATE_DIR="$STATE_DIR_HOST" npx playwright test \
  --config=playwright/tests/playwright.demo-metrics-e2e.config.ts)

echo "run complete; teardown follows"
