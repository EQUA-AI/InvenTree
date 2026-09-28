#!/usr/bin/env bash
# Whole-process read-only / bootstrap safety probe for the EQUA demo-metrics
# read-only commands (plan_demo_metrics, verify_demo_metrics,
# cleanup_demo_metrics WITHOUT --apply).
#
# What this proves (LOCAL dev runtime only — NOT the release image):
#   Each read-only command is run as a FRESH process whose ENTIRE lifetime —
#   interpreter startup, Django setup, plugin/app ready() paths, command
#   handle() — executes against a server-enforced read-only connection to the
#   DISPOSABLE database `inventree_dm_bootstrap_v1`:
#     * `dm_bootstrap_ro` role has CONNECT + SELECT only (no INSERT/UPDATE/
#       DELETE/DDL), so any write anywhere in the process is rejected by the
#       SERVER, including auxiliary connections;
#     * `default_transaction_read_only = on` is set database-wide as a second
#       server-side layer;
#     * per-table row-COUNT fingerprints before/after the window must match
#       (row counts only — this is NOT a content checksum; in-place content
#       updates are not detected by the fingerprint and are prevented by the
#       server-side read-only defense instead);
#     * an external-transport guard (sitecustomize) records every outbound
#     socket connect and blocks+records anything not on the harness-local
#     allowlist (disposable PostgreSQL + dev redis);
#     * negative probes demonstrate both defenses actually CATCH an attempted
#       write and an attempted external connect;
#     * the orchestration itself is FAIL-CLOSED: any nonzero probe exit code
#       aborts the run with that code, every command's transport log must show
#       `guard_installed` and zero blocked/forbidden connect attempts (an
#       attempted forbidden transport fails the harness even when the guard
#       caught it and the command survived), and expected artifacts/logs must
#       exist. The validators behind those checks have RED/GREEN self-tests
#       (bootstrap_probe/test_harness_validators.py; fabricated unit-test
#       fixtures, NOT acceptance evidence) which the harness runs first.
#     * the PostgreSQL statement log (`log_statement=all`) is captured for the
#       window as server-side evidence.
#
# What this does NOT prove: bootstrap safety of the reviewed RELEASE image.
# That gate stays PENDING — no immutable reviewed image containing this
# implementation exists yet (see the runbook section this probe feeds).
#
# Never touches the default, shared or any cloud database: the probe settings
# shim refuses any database name other than `inventree_dm_bootstrap_v1` and
# any cloud-shaped host. The disposable database is created idempotently and
# never dropped.
#
# Credentials: no database or user password appears anywhere in this script.
# The database steps authenticate with the db container's own configured
# environment ($POSTGRES_USER / $POSTGRES_PASSWORD), consumed inside the
# container and never printed. The read-only probe role reuses that same
# configured password value (set and consumed inside the db container only);
# the probe processes authenticate with the container's existing environment.
#
# Usage: contrib/container/demo-metrics-bootstrap-probe.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
VALIDATORS="$REPO/contrib/container/bootstrap_probe/harness_validators.py"
SELFTEST="$REPO/contrib/container/bootstrap_probe/test_harness_validators.py"
CTR=inventree_devcontainer-inventree-1
DBCTR=inventree_devcontainer-db-1
DB=inventree_dm_bootstrap_v1
ROUSER=dm_bootstrap_ro
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
STATE_DIR_HOST="${DM_BOOTSTRAP_STATE_DIR:-$HOME/.hermes/cache/scratch/dm_bootstrap_probe-$RUN_ID}"
STATE_DIR_CTR="/home/inventree/LocalTesting/dm_bootstrap_state-$RUN_ID"
STATE_DIR_CTR_HOST="$REPO/LocalTesting/dm_bootstrap_state-$RUN_ID"
PY=/home/inventree/dev/venv/bin/python
MANAGE="cd /home/inventree/src/backend/InvenTree && $PY manage.py"
SHIM_ENV="-e INVENTREE_DB_NAME=$DB -e INVENTREE_AUTO_UPDATE=False"
SHIM_ENV="$SHIM_ENV -e DJANGO_SETTINGS_MODULE=demo_metrics_bootstrap_settings"
SHIM_ENV="$SHIM_ENV -e PYTHONPATH=/home/inventree/contrib/container:/home/inventree/contrib/container/bootstrap_probe"
SHIM_ENV="$SHIM_ENV -e DM_BOOTSTRAP_STATE_DIR=$STATE_DIR_CTR"
NET_ALLOW="inventree_devcontainer-db-1:5432,127.0.0.1:5432,localhost:5432,inventree_devcontainer-redis-1:6379,127.0.0.1:6379,localhost:6379"
PROBE_FILES=(
  src/backend/InvenTree/InvenTree/ready.py
  src/backend/InvenTree/assets/management/commands/plan_demo_metrics.py
  src/backend/InvenTree/assets/management/commands/apply_demo_metrics.py
  src/backend/InvenTree/assets/management/commands/verify_demo_metrics.py
  src/backend/InvenTree/assets/management/commands/replay_demo_metrics.py
  src/backend/InvenTree/assets/management/commands/stop_demo_metrics.py
  src/backend/InvenTree/assets/management/commands/cleanup_demo_metrics.py
  src/backend/InvenTree/assets/demo_metrics/effects.py
  src/backend/InvenTree/assets/demo_metrics/planner.py
  src/backend/InvenTree/assets/demo_metrics/apply_service.py
  src/backend/InvenTree/assets/demo_metrics/cleanup.py
  contrib/container/demo_metrics_bootstrap_settings.py
  contrib/container/bootstrap_probe/net_guard.py
  contrib/container/bootstrap_probe/sitecustomize.py
  contrib/container/bootstrap_probe/harness_validators.py
  contrib/container/bootstrap_probe/test_harness_validators.py
  contrib/container/demo_metrics_bootstrap_setup.py
  contrib/container/demo_metrics_bootstrap_fingerprint.py
  contrib/container/demo_metrics_bootstrap_negative.py
  contrib/container/demo-metrics-bootstrap-probe.sh
)

[[ "$DB" == inventree_dm_bootstrap_v1 ]] || { echo 'unsafe database name' >&2; exit 1; }
[[ "$DB" != inventree ]] || { echo 'refusing the shared database name' >&2; exit 1; }

mkdir -p "$STATE_DIR_HOST" && chmod 700 "$STATE_DIR_HOST"

echo "== 0/9 harness self-tests (fabricated unit-test fixtures; NOT acceptance evidence) =="
python3 "$SELFTEST"

echo "== 1/9 SHA256 parity: host tree under test vs container mount =="
host_hash="$(cd "$REPO" && sha256sum "${PROBE_FILES[@]}")"
ctr_hash="$(docker exec "$CTR" bash -lc "cd /home/inventree && sha256sum ${PROBE_FILES[*]}")"
if [[ "$host_hash" != "$ctr_hash" ]]; then
  echo 'refusing: container view of the files under test differs from the host tree' >&2
  diff <(printf '%s\n' "$host_hash") <(printf '%s\n' "$ctr_hash") >&2 || true
  exit 1
fi
printf '%s\n' "$host_hash" > "$STATE_DIR_HOST/sha256-parity.txt"
echo "PARITY OK (${#PROBE_FILES[@]} files)"

echo "== 2/9 dedicated disposable database (created idempotently, never dropped) =="
docker exec -e DM_BP_DB="$DB" "$DBCTR" bash -c '
  set -euo pipefail
  # Connections follow the db container'"'"'s own configured environment
  # ($POSTGRES_USER / $POSTGRES_PASSWORD, default local socket — no hardcoded
  # host/port/credentials). All writes below target ONLY the disposable
  # $DM_BP_DB; the default/shared database $POSTGRES_DB is queried read-only
  # for catalog checks and is never altered, reset or dropped.
  [[ "$DM_BP_DB" == inventree_dm_bootstrap_v1 ]] || { echo "refusing: unexpected disposable database name $DM_BP_DB" >&2; exit 1; }
  export PGPASSWORD="$POSTGRES_PASSWORD"
  if psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT datname FROM pg_database" | grep -Fxq "$DM_BP_DB"; then
    echo "database $DM_BP_DB already exists (idempotent; never dropped)"
  else
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE $DM_BP_DB"
    echo "database $DM_BP_DB created"
  fi
  # Write-phase window: the harness disarms its own read-only defense while the
  # disposable database is prepared (migrate + governed apply), then re-arms it
  # before any probe process runs. Re-runs are idempotent: nothing is dropped
  # or reset. The ALTER statement below targets ONLY the disposable $DM_BP_DB;
  # it is issued while connected to the configured default database $POSTGRES_DB
  # because a session connected to $DM_BP_DB left armed read-only cannot execute
  # ALTER DATABASE at all. The default/shared database is never altered, reset
  # or written — it is only the connection point (as for the catalog checks).
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER DATABASE $DM_BP_DB SET default_transaction_read_only = off"'

echo "== 3/9 migrate (only $DB; this harness is the single migration owner here) =="
docker exec $SHIM_ENV "$CTR" bash -lc "$MANAGE migrate --noinput"

echo "== 4/9 fixture setup + GOVERNED apply (the probe's only write phase) =="
# Re-run hygiene: the disposable database is never dropped, so earlier runs
# leave fixture data behind. The write phase therefore starts by resetting the
# data of ONLY the disposable database ($DB) via the probe settings shim, which
# refuses any other database name or a cloud-shaped host. The default/shared
# database is never part of this reset and is never written.
docker exec $SHIM_ENV "$CTR" bash -lc "$MANAGE flush --noinput" >/dev/null
echo "disposable database $DB data reset (default/shared database untouched)"
docker exec -i $SHIM_ENV "$CTR" bash -lc "$MANAGE shell" \
  < "$REPO/contrib/container/demo_metrics_bootstrap_setup.py"
[[ -f "$STATE_DIR_CTR_HOST/probe_env.sh" ]] || {
  echo 'refusing: setup did not produce probe_env.sh on the documented mount' >&2; exit 1; }
# shellcheck disable=SC1091
source "$STATE_DIR_CTR_HOST/probe_env.sh"
echo "probe target: session=$SESSION_KEY actor=$ACTOR"

echo "== 5/9 arm server-enforced read-only (role SELECT-only + tx_read_only=on) =="
docker exec -e DM_BP_DB="$DB" -e DM_BP_ROUSER="$ROUSER" "$DBCTR" bash -c '
  set -euo pipefail
  export PGPASSWORD="$POSTGRES_PASSWORD"
  if ! psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT 1 FROM pg_roles WHERE rolname = '"'"'$DM_BP_ROUSER'"'"'" | grep -q 1; then
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE ROLE $DM_BP_ROUSER LOGIN PASSWORD '"'"'$POSTGRES_PASSWORD'"'"'"
    echo "role $DM_BP_ROUSER created (password never printed)"
  fi
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$DM_BP_DB" <<SQL
GRANT CONNECT ON DATABASE $DM_BP_DB TO $DM_BP_ROUSER;
GRANT USAGE ON SCHEMA public TO $DM_BP_ROUSER;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO $DM_BP_ROUSER;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO $DM_BP_ROUSER;
ALTER DATABASE $DM_BP_DB SET default_transaction_read_only = on;
ALTER DATABASE $DM_BP_DB SET log_statement TO \$\$all\$\$;
SQL
  echo "read-only window armed for $DM_BP_DB"'

run_fp() {
  local tag="$1"
  local rc=0
  docker exec -i $SHIM_ENV -e INVENTREE_DB_USER=$ROUSER \
    -e DM_BOOTSTRAP_FP_TAG="$tag" \
    -e DM_BOOTSTRAP_NETLOG="$STATE_DIR_CTR/net_fingerprint_$tag.jsonl" \
    -e DM_BOOTSTRAP_NET_ALLOW="$NET_ALLOW" \
    "$CTR" bash -lc "$MANAGE shell" \
    < "$REPO/contrib/container/demo_metrics_bootstrap_fingerprint.py" \
    2>&1 | tee "$STATE_DIR_HOST/fingerprint_$tag.log" || rc=$?
  # Fail closed: require this process's own transport evidence and fingerprint
  # artifact, and propagate a nonzero exit code as this harness's own exit
  # code (a failed fingerprint run is an unverified window, never a pass).
  local evidence_rc=0
  python3 "$VALIDATORS" netlog \
    --path "$STATE_DIR_CTR_HOST/net_fingerprint_$tag.jsonl" \
    --label "net_fingerprint_$tag.jsonl" || evidence_rc=1
  python3 "$VALIDATORS" artifact \
    --path "$STATE_DIR_CTR_HOST/fingerprint_$tag.json" \
    --label "fingerprint_$tag.json" || evidence_rc=1
  echo "fingerprint-$tag $rc" >> "$STATE_DIR_HOST/exit_codes.txt"
  if [[ $rc -ne 0 ]]; then
    echo "HARNESS VALIDATION FAILED: fingerprint [$tag] exit code $rc; propagating" >&2
    return "$rc"
  fi
  [[ $evidence_rc -eq 0 ]] || exit 1
  python3 "$VALIDATORS" probe-exit --tag "fingerprint-$tag" --rc "$rc"
}

run_probe() {
  local tag="$1"; shift
  echo "-- fresh-process probe [$tag]: manage.py $*"
  local rc=0
  docker exec $SHIM_ENV -e INVENTREE_DB_USER=$ROUSER \
    -e DM_BOOTSTRAP_NETLOG="$STATE_DIR_CTR/net_$tag.jsonl" \
    -e DM_BOOTSTRAP_NET_ALLOW="$NET_ALLOW" \
    "$CTR" bash -lc "$MANAGE $*" 2>&1 | tee "$STATE_DIR_HOST/probe_$tag.log" || rc=$?
  echo "-- probe [$tag] exit code: $rc"
  # Fail closed. Evidence is checked even when the command failed (its
  # failures are reported, never masked): the log must exist, the transport
  # log must show guard_installed and ZERO blocked/forbidden connect attempts
  # (an attempted forbidden transport fails the harness even when the guard
  # caught it and the command kept going). Then a nonzero probe exit code is
  # propagated as this harness's own exit code: a syntax error, auth failure,
  # command refusal or crash is a FAILED probe, never a silent success.
  local evidence_rc=0
  python3 "$VALIDATORS" artifact \
    --path "$STATE_DIR_HOST/probe_$tag.log" --label "probe_$tag.log" || evidence_rc=1
  python3 "$VALIDATORS" netlog \
    --path "$STATE_DIR_CTR_HOST/net_$tag.jsonl" --label "net_$tag.jsonl" || evidence_rc=1
  echo "$tag $rc" >> "$STATE_DIR_HOST/exit_codes.txt"
  if [[ $rc -ne 0 ]]; then
    echo "HARNESS VALIDATION FAILED: probe [$tag] exit code $rc; propagating" >&2
    return "$rc"
  fi
  [[ $evidence_rc -eq 0 ]] || exit 1
  python3 "$VALIDATORS" probe-exit --tag "$tag" --rc "$rc"
}

echo "== 6/9 fingerprint BEFORE (read-only role) =="
run_fp before

# RFC3339 with explicit Z so `docker logs --since/--until` cannot interpret the
# stamps in local time and shift the evidence window.
T0="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "== 7/9 whole-process read-only probes (one fresh process per command) =="
run_probe plan    "plan_demo_metrics --fixture $FIXTURE_PATH --mapping $STATE_DIR_CTR/mapping.json --out $STATE_DIR_CTR/plan.json --actor $ACTOR"
run_probe verify  "verify_demo_metrics --session $SESSION_KEY --actor $ACTOR"
run_probe cleanup "cleanup_demo_metrics --session $SESSION_KEY --actor $ACTOR --out $STATE_DIR_CTR/cleanup-plan.json"
echo "-- expected command artifacts"
python3 "$VALIDATORS" artifact --path "$STATE_DIR_CTR_HOST/plan.json" \
  --label "plan.json (plan_demo_metrics --out)"
python3 "$VALIDATORS" artifact --path "$STATE_DIR_CTR_HOST/cleanup-plan.json" \
  --label "cleanup-plan.json (cleanup_demo_metrics --out)"
T1="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "== 8/9 fingerprint AFTER + compare =="
run_fp after
python3 - "$STATE_DIR_CTR_HOST" <<'PY'
import json, sys
state = sys.argv[1]
before = json.load(open(f'{state}/fingerprint_before.json'))
after = json.load(open(f'{state}/fingerprint_after.json'))
diffs = {
    t: (before['tables'].get(t), after['tables'].get(t))
    for t in sorted(set(before['tables']) | set(after['tables']))
    if before['tables'].get(t) != after['tables'].get(t)
}
print('before:', before['total_rows'], 'rows /', before['table_count'], 'tables',
      '| tx_read_only =', before['default_transaction_read_only'],
      '| user =', before['user'])
print('after :', after['total_rows'], 'rows /', after['table_count'], 'tables',
      '| tx_read_only =', after['default_transaction_read_only'],
      '| user =', after['user'])
if diffs:
    print('ROW-COUNT DIFFERENCES (a write happened during the read-only window):')
    for t, (b, a) in diffs.items():
        print(f'  {t}: {b} -> {a}')
    sys.exit(1)
if before.get('user') != 'dm_bootstrap_ro' or after.get('user') != 'dm_bootstrap_ro':
    print('FINGERPRINT INVALID: before/after did not run as dm_bootstrap_ro')
    sys.exit(1)
if before.get('default_transaction_read_only') != 'on' or after.get('default_transaction_read_only') != 'on':
    print('FINGERPRINT INVALID: default_transaction_read_only was not on')
    sys.exit(1)
print('FINGERPRINT UNCHANGED: zero row-count changes across the read-only window')
print('(row COUNTS only — NOT a content checksum; in-place content updates are')
print(' not detected here and are prevented by the server-side read-only defense)')
PY

echo "== 9/9 negative probes (defenses must CATCH the attempts) =="
T2="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rc=0
docker exec -i $SHIM_ENV -e INVENTREE_DB_USER=$ROUSER \
  -e DM_BOOTSTRAP_NETLOG="$STATE_DIR_CTR/net_negative.jsonl" \
  -e DM_BOOTSTRAP_NET_ALLOW="$NET_ALLOW" \
  "$CTR" bash -lc "$MANAGE shell" \
  < "$REPO/contrib/container/demo_metrics_bootstrap_negative.py" \
  2>&1 | tee "$STATE_DIR_HOST/probe_negative.log" || rc=$?
T3="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "negative $rc" >> "$STATE_DIR_HOST/exit_codes.txt"
[[ $rc -eq 0 ]] || { echo "NEGATIVE PROBES FAILED (exit $rc)" >&2; exit 1; }
# The negative probe's transport failure is EXPECTED here — separately from
# the read-only commands: its log must contain the blocked forbidden-transport
# attempt (and the guard marker), or the transport defense was never exercised.
python3 "$VALIDATORS" artifact --path "$STATE_DIR_HOST/probe_negative.log" \
  --label "probe_negative.log"
python3 "$VALIDATORS" netlog \
  --path "$STATE_DIR_CTR_HOST/net_negative.jsonl" --label "net_negative.jsonl" \
  --expect-blocked

# Collect artifacts on the host scratch dir BEFORE any late evidence gate can
# fail the run, so the evidence survives a failure. Container-side scratch is
# removed at the very end (LocalTesting is gitignored but kept clean anyway).
cp -f "$STATE_DIR_CTR_HOST"/*.json "$STATE_DIR_CTR_HOST"/*.jsonl "$STATE_DIR_HOST"/ 2>/dev/null || true
cp -f "$STATE_DIR_CTR_HOST"/*.sh "$STATE_DIR_HOST"/ 2>/dev/null || true

echo "== server-side SQL statement log evidence (fail closed) =="
docker logs --since "$T0" --until "$T1" "$DBCTR" > "$STATE_DIR_HOST/db_statements_readonly_window.log" 2>&1 || true
docker logs --since "$T2" --until "$T3" "$DBCTR" > "$STATE_DIR_HOST/db_statements_negative_window.log" 2>&1 || true
# Evidence presence: a missing or empty capture is UNVERIFIED, never a pass.
python3 "$VALIDATORS" artifact \
  --path "$STATE_DIR_HOST/db_statements_readonly_window.log" \
  --label "db_statements_readonly_window.log (server-side evidence)"
python3 "$VALIDATORS" artifact \
  --path "$STATE_DIR_HOST/db_statements_negative_window.log" \
  --label "db_statements_negative_window.log (server-side evidence)"
# Read-only window: the server log is authoritative — it sees write ATTEMPTS
# that the process output can swallow (silently caught errors). `log_statement`
# is set per-database (only on $DB), so `LOG: statement|execute` lines in this
# capture are the probe processes' own. ANY write-shaped statement or
# write-refusal error means a write was attempted during the read-only
# commands; that fails the probe even when the server caught it and the
# process swallowed the failure — same standard as the transport gate.
RO="$STATE_DIR_HOST/db_statements_readonly_window.log"
WRITE_SHAPED='LOG:  (statement|execute [^:]*): *(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|COPY)'
WRITE_REFUSED='ERROR:  (cannot execute [A-Z]+ in a read-only transaction|permission denied)'
echo "read-only window write-shaped statements: $(grep -Ec "$WRITE_SHAPED" "$RO" || true)"
if grep -Eq "$WRITE_SHAPED|$WRITE_REFUSED" "$RO"; then
  echo 'SERVER-SIDE WRITE ATTEMPT in the read-only window — probe FAILED' >&2
  echo '(attempts the process output swallowed still count; the server log is authoritative)' >&2
  grep -E "$WRITE_SHAPED|$WRITE_REFUSED" "$RO" | head -20 >&2
  exit 1
fi
echo 'zero write attempts reached the server in the read-only window'
# Negative window (expected — separately): the attempted INSERT must reach the
# server and be refused THERE, i.e. the write defense must be server-visible.
NEG="$STATE_DIR_HOST/db_statements_negative_window.log"
echo "negative window (expected: the attempted INSERT and its server-side ERROR):"
grep -E "$WRITE_SHAPED|$WRITE_REFUSED" "$NEG" | head -5 || true
grep -Eq 'INSERT INTO .?assets_client' "$NEG" || {
  echo 'NEGATIVE PROBE: attempted INSERT not found in the server statement log — probe FAILED' >&2
  exit 1; }
grep -Eq 'ERROR:  cannot execute INSERT in a read-only transaction' "$NEG" || {
  echo 'NEGATIVE PROBE: server-side write refusal not found in the statement log — probe FAILED' >&2
  exit 1; }
echo 'negative probe write attempt and server-side refusal both recorded'

echo "== write-marker scan on probe logs =="
if grep -El 'read-only transaction|permission denied for (table|schema|sequence)' \
    "$STATE_DIR_HOST"/probe_plan.log "$STATE_DIR_HOST"/probe_verify.log \
    "$STATE_DIR_HOST"/probe_cleanup.log; then
  echo 'WRITE MARKER FOUND in read-only command output — probe FAILED' >&2
  exit 1
fi
echo 'no write markers in plan/verify/cleanup output'

echo "== transport guard logs (fail closed: guard marker required, zero blocked outside the negative probe) =="
for name in net_plan net_verify net_cleanup net_fingerprint_before net_fingerprint_after; do
  f="$STATE_DIR_CTR_HOST/$name.jsonl"
  python3 "$VALIDATORS" netlog --path "$f" --label "$name.jsonl"
  echo "-- $name.jsonl: allowed=$(grep -c '"result": "allowed"' "$f" || true) blocked=$(grep -c '"result": "blocked"' "$f" || true)"
done
f="$STATE_DIR_CTR_HOST/net_negative.jsonl"
python3 "$VALIDATORS" netlog --path "$f" --label "net_negative.jsonl" --expect-blocked
echo "-- net_negative.jsonl: allowed=$(grep -c '"result": "allowed"' "$f" || true) blocked=$(grep -c '"result": "blocked"' "$f" || true) (blocked attempt EXPECTED: negative probe)"

# Remove this run's container-side scratch.
rm -rf "$STATE_DIR_CTR_HOST"
docker exec "$CTR" rm -rf "$STATE_DIR_CTR" >/dev/null 2>&1 || true

echo "== PROBE COMPLETE =="
echo "artifacts: $STATE_DIR_HOST"
echo "NOTE: LOCAL dev-runtime evidence only. The reviewed-release-image"
echo "      bootstrap gate remains PENDING (no immutable reviewed image exists)."
