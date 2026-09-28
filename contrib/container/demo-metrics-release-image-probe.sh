#!/usr/bin/env bash
# Whole-process release-image probe for the EQUA demo-metrics production-image
# candidate (UNCOMMITTED LOCAL CANDIDATE — not an approved release).
#
# What this proves (LOCAL, offline, image-contained source only):
#   * source/image hash parity: every backend file inside the image matches the
#     frozen input-sha manifest of the allowlisted build context;
#   * interpreter / Django / management-command / artifact-path inspection of
#     the REAL production target image;
#   * each read-only command (plan_demo_metrics, verify_demo_metrics,
#     cleanup_demo_metrics WITHOUT --apply) runs as a FRESH process whose
#     ENTIRE lifetime — interpreter startup, Django setup, plugin/app ready()
#     paths, command handle() — executes INSIDE THE IMAGE (image-contained
#     source; no source bind mounts) against a server-enforced read-only
#     connection to the DISPOSABLE database `inventree_dm_release_probe_v1`:
#       - `dm_release_ro` role has CONNECT + SELECT only (no INSERT/UPDATE/
#         DELETE/DDL), so any write anywhere in the process is rejected by the
#         SERVER, including auxiliary connections;
#       - `default_transaction_read_only = on` is set database-wide as a
#         second server-side layer;
#       - per-table row-COUNT fingerprints before/after the window must match
#         (row counts only — NOT a content checksum; in-place content updates
#         are prevented by the server-side read-only defense and their
#         attempted statements/refusals are evidenced in the server statement
#         log instead);
#       - an external-transport guard (bootstrap_probe/sitecustomize, reused
#         unchanged) records every outbound socket connect and blocks+records
#         anything not on the harness-local allowlist (the disposable probe DB
#         only);
#       - negative probes demonstrate both defenses actually CATCH an attempted
#         write and an attempted external connect (the write must be refused
#         by the SERVER with a read-only/privilege SQLSTATE, matched in the
#         statement log — a random client-side exception is not proof);
#       - the orchestration is FAIL-CLOSED: any nonzero probe exit aborts, each
#         command's transport log must show `guard_installed` and ZERO blocked
#         connect attempts, and artifacts must exist and be non-empty;
#       - the PostgreSQL statement log (`log_statement=all`) is captured for
#         the window as server-side evidence; ANY write-shaped statement fails
#         the probe even if the process swallowed it.
#
# What this does NOT prove: production/Cloud behavior, Azure Job execution,
# approval, or that this candidate is releasable. This candidate is an
# UNCOMMITTED LOCAL CANDIDATE (commit_hash build arg deliberately empty; HEAD
# is NOT the commit of this code). Human review, commit, publication and
# deployment approval remain REQUIRED and are NOT performed here.
#
# Databases: ONLY the dedicated disposable `inventree_dm_release_probe_v1` on
# the dedicated `inventree_dm_release_probe_db` container (created idempotently,
# never dropped — keepdb semantics). The default/shared `inventree` database,
# the dev bootstrap probe database and any cloud database are never contacted.
# No configured secret is consumed anywhere: the disposable probe DB runs
# trust auth on a dedicated docker network with no published ports.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HARNESS="$REPO/contrib/container"
VALIDATORS="$HARNESS/bootstrap_probe/harness_validators.py"
SELFTEST="$HARNESS/bootstrap_probe/test_harness_validators.py"
RELEASE_TESTS="$HARNESS/test_demo_metrics_release_guard.py"
RELEASE_CLI="$HARNESS/demo_metrics_release_image.py"

IMAGE_TAG="${1:-}"
RUN_DIR="${2:-}"
if [[ -z "$IMAGE_TAG" || -z "$RUN_DIR" ]]; then
  echo "usage: $0 IMAGE_TAG RUN_DIR" >&2
  exit 2
fi

DB=inventree_dm_release_probe_v1
DBHOST=inventree_dm_release_probe_db
DBNET=inventree_dm_release_probe_net
# pgvector required: aichat migration 0018 runs CREATE EXTENSION vector
# (the migration's own error text names pgvector/pgvector:pg15 for local use).
DBIMG=pgvector/pgvector:pg15
ROUSER=dm_release_ro
RUNCTR=inventree_dm_release_probe_run
PY=/usr/local/bin/python3
MANAGE="$PY /home/inventree/src/backend/InvenTree/manage.py"
STATE_DIR_CTR=/tmp/dm_release_state
HARNESS_CTR=/opt/dm_release_probe

mkdir -p "$RUN_DIR" && chmod 700 "$RUN_DIR"
STATE_DIR_HOST="$RUN_DIR/state"
mkdir -p "$STATE_DIR_HOST"

# Exact-name guards (fail closed before anything is created).
[[ "$DB" == inventree_dm_release_probe_v1 ]] || { echo 'unsafe database name' >&2; exit 1; }
[[ "$DB" != inventree ]] || { echo 'refusing the shared database name' >&2; exit 1; }
[[ "$DBHOST" == inventree_dm_release_probe_db ]] || { echo 'unsafe database host' >&2; exit 1; }

SHIM_ENV="-e INVENTREE_DB_ENGINE=postgresql"
SHIM_ENV="$SHIM_ENV -e INVENTREE_DB_NAME=$DB"
SHIM_ENV="$SHIM_ENV -e INVENTREE_DB_HOST=$DBHOST"
SHIM_ENV="$SHIM_ENV -e INVENTREE_DB_PORT=5432"
SHIM_ENV="$SHIM_ENV -e INVENTREE_DB_PASSWORD="
SHIM_ENV="$SHIM_ENV -e INVENTREE_AUTO_UPDATE=False"
SHIM_ENV="$SHIM_ENV -e INVENTREE_SITE_URL=http://localhost:8000"
SHIM_ENV="$SHIM_ENV -e DJANGO_SETTINGS_MODULE=demo_metrics_release_settings"
SHIM_ENV="$SHIM_ENV -e PYTHONPATH=$HARNESS_CTR:$HARNESS_CTR/bootstrap_probe"
SHIM_ENV="$SHIM_ENV -e DM_RELEASE_STATE_DIR=$STATE_DIR_CTR"
NET_ALLOW="$DBHOST:5432,127.0.0.1:5432,localhost:5432"

echo "== 0/10 harness self-tests (fabricated unit-test fixtures; NOT acceptance evidence) =="
python3 "$SELFTEST" 2>&1 | tee "$STATE_DIR_HOST/selftest_validators.log"
(cd "$HARNESS" && TMPDIR="${TMPDIR:-$RUN_DIR}" python3 "$RELEASE_TESTS") \
  2>&1 | tee "$STATE_DIR_HOST/selftest_release_guard.log"

echo "== 1/10 image metadata + interpreter / Django / commands / artifact paths =="
docker image inspect "$IMAGE_TAG" > "$STATE_DIR_HOST/image_inspect.json"
# Pin EVERY later candidate use to the resolved image ID: the tag is a mutable
# name and must not be able to change the target mid-run.
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_TAG")"
case "$IMAGE_ID" in
  sha256:*) ;;
  *) echo "refusing to proceed: unresolved candidate image id '$IMAGE_ID'" >&2; exit 1 ;;
esac
printf '%s\n' "$IMAGE_ID" > "$STATE_DIR_HOST/image_id.txt"
echo "candidate pinned for this run: $IMAGE_TAG -> $IMAGE_ID"
{
  echo "--- image identity ---"
  docker image inspect --format 'Id={{.Id}}' "$IMAGE_ID"
  docker image inspect --format 'Created={{.Created}}' "$IMAGE_ID"
  docker image inspect --format 'Entrypoint={{json .Config.Entrypoint}} Cmd={{json .Config.Cmd}}' "$IMAGE_ID"
  echo "--- interpreter ---"
  docker run --rm --entrypoint sh "$IMAGE_ID" -c \
    "set -e; $PY -V; command -v $PY; $PY -c 'import sys, django; print(\"django\", django.get_version()); print(\"prefix\", sys.prefix); print(\"user-site\", __import__(\"site\").getusersitepackages())'"
  echo "--- pinned dependency check ---"
  docker run --rm --entrypoint sh "$IMAGE_ID" -c \
    "set -e; $PY -m pip check; $PY -m pip show django | sed -n '1,3p'"
  # Management-command discovery runs after disposable DB setup and arming:
  # even `manage.py help` initializes apps that require a migrated database.
  echo "--- artifact paths (required; every check must propagate failure) ---"
  docker run --rm --entrypoint sh "$IMAGE_ID" -c \
    "set -e
     test -s /home/inventree/src/backend/InvenTree/InvenTree/licenses.txt
     echo licenses.txt: OK
     cat /home/inventree/src/backend/InvenTree/web/static/web/build-info.json
     echo
     test -d /root/.local/share/tiktoken
     ls /root/.local/share/tiktoken | head -3
     test -x /usr/local/bin/gcp-entra-token
     echo gcp-entra-token: executable
     test -f /home/inventree/init.sh
     echo init.sh: present
     test -f /home/inventree/gunicorn.conf.py
     echo gunicorn.conf.py: present
     test -f /home/inventree/src/backend/InvenTree/manage.py
     echo manage.py: present
     /root/.local/bin/gunicorn --version"
} 2>&1 | tee "$STATE_DIR_HOST/image_inspect.log"

echo "== 2/10 source/image hash parity against the frozen input manifest =="
python3 "$RELEASE_CLI" parity \
  --image "$IMAGE_ID" \
  --manifest "$RUN_DIR/input-sha-manifest.json" \
  --out "$STATE_DIR_HOST/parity.json" \
  2>&1 | tee "$STATE_DIR_HOST/parity.log"

echo "== 3/10 dedicated disposable probe database (created idempotently, never dropped) =="
if ! docker network inspect "$DBNET" >/dev/null 2>&1; then
  docker network create "$DBNET" >/dev/null
  echo "network $DBNET created"
else
  echo "network $DBNET already exists (idempotent)"
fi
if ! docker inspect "$DBHOST" >/dev/null 2>&1; then
  docker run -d --name "$DBHOST" --network "$DBNET" \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    -e POSTGRES_DB="$DB" \
    "$DBIMG" >/dev/null
  echo "database container $DBHOST created (image $DBIMG; trust auth, no published ports, no secrets)"
else
  echo "database container $DBHOST already exists (idempotent; never dropped)"
fi
for _ in $(seq 1 60); do
  if docker exec "$DBHOST" pg_isready -U postgres >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$DBHOST" pg_isready -U postgres
docker exec "$DBHOST" psql -U postgres -Atc "SELECT datname FROM pg_database" \
  | grep -Fxq "$DB" || { echo "probe database $DB missing" >&2; exit 1; }
# Re-run hygiene: arm the write window for the disposable DB only (this ALTER
# targets ONLY $DB; the connection is the server's own local default).
docker exec "$DBHOST" psql -U postgres -c \
  "ALTER DATABASE $DB SET default_transaction_read_only = off" >/dev/null
echo "probe database $DB ready (write window open for setup only)"

echo "== 4/10 probe container from the candidate image (image-contained source) =="
if docker inspect "$RUNCTR" >/dev/null 2>&1; then
  echo "removing stale probe container $RUNCTR" >&2
  docker rm -f "$RUNCTR" >/dev/null
fi
docker create --name "$RUNCTR" --network "$DBNET" \
  --entrypoint sleep "$IMAGE_ID" infinity >/dev/null
docker start "$RUNCTR" >/dev/null
docker exec "$RUNCTR" mkdir -p "$HARNESS_CTR" "$STATE_DIR_CTR"
docker cp "$HARNESS/demo_metrics_release_guard.py" "$RUNCTR:$HARNESS_CTR/"
docker cp "$HARNESS/demo_metrics_release_settings.py" "$RUNCTR:$HARNESS_CTR/"
docker cp "$HARNESS/demo_metrics_release_setup.py" "$RUNCTR:$HARNESS_CTR/"
docker cp "$HARNESS/demo_metrics_release_fingerprint.py" "$RUNCTR:$HARNESS_CTR/"
docker cp "$HARNESS/demo_metrics_release_negative.py" "$RUNCTR:$HARNESS_CTR/"
docker cp "$HARNESS/bootstrap_probe" "$RUNCTR:$HARNESS_CTR/bootstrap_probe"
echo "probe container $RUNCTR running (entrypoint overridden; /home/inventree source untouched)"
# Fail closed: the image-contained source must still match the manifest inside
# the EXISTING probe container itself — the hashes are read from $RUNCTR via
# `docker exec` (no new container is launched), which guards against copy
# drift AND against a mutable tag retargeting the check.
python3 "$RELEASE_CLI" parity \
  --container "$RUNCTR" \
  --manifest "$RUN_DIR/input-sha-manifest.json" \
  --out "$STATE_DIR_HOST/parity_probe_container.json" >/dev/null
docker exec "$RUNCTR" sh -c \
  "cd /home/inventree && sha256sum \$(cd /home/inventree && find src/backend -type f -name '*.py' | sort | head -50)" \
  > "$STATE_DIR_HOST/probe_container_source_sample.txt"

echo "== 5/10 migrate (only $DB; this harness is the single migration owner here) =="
docker exec $SHIM_ENV -e INVENTREE_DB_USER=postgres "$RUNCTR" \
  $MANAGE migrate --noinput 2>&1 | tee "$STATE_DIR_HOST/migrate.log"

echo "== 6/10 fixture setup + GOVERNED apply (the probe's only write phase) =="
docker exec $SHIM_ENV -e INVENTREE_DB_USER=postgres "$RUNCTR" \
  $MANAGE flush --noinput >/dev/null
echo "disposable database $DB data reset (no other database touched)"
docker exec -i $SHIM_ENV -e INVENTREE_DB_USER=postgres "$RUNCTR" \
  $MANAGE shell < "$HARNESS/demo_metrics_release_setup.py" \
  2>&1 | tee "$STATE_DIR_HOST/setup.log"
docker exec "$RUNCTR" cat "$STATE_DIR_CTR/probe_env.sh" > "$STATE_DIR_HOST/probe_env.sh"
# shellcheck disable=SC1091
source "$STATE_DIR_HOST/probe_env.sh"
echo "probe target: session=$SESSION_KEY actor=$ACTOR"

echo "== 7/10 arm server-enforced read-only (role SELECT-only + tx_read_only=on) =="
docker exec "$DBHOST" psql -U postgres -Atc \
  "SELECT 1 FROM pg_roles WHERE rolname = '$ROUSER'" | grep -q 1 || \
docker exec "$DBHOST" psql -U postgres -c "CREATE ROLE $ROUSER LOGIN" >/dev/null
# NOTE: `docker exec -i` is REQUIRED for the heredoc to reach psql at all.
docker exec -i "$DBHOST" psql -v ON_ERROR_STOP=1 -U postgres -d "$DB" <<SQL
GRANT CONNECT ON DATABASE $DB TO $ROUSER;
GRANT USAGE ON SCHEMA public TO $ROUSER;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO $ROUSER;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO $ROUSER;
ALTER DATABASE $DB SET default_transaction_read_only = on;
ALTER DATABASE $DB SET log_statement TO \$\$all\$\$;
SQL
# Fail closed: the server must confirm the armed state before any probe runs.
armed="$(docker exec "$DBHOST" psql -U postgres -d "$DB" -Atc 'SHOW default_transaction_read_only')"
[[ "$armed" == on ]] || { echo "read-only arming FAILED (default_transaction_read_only=$armed)" >&2; exit 1; }
granted="$(docker exec "$DBHOST" psql -U postgres -d "$DB" -Atc \
  "SELECT has_table_privilege('$ROUSER', 'assets_client', 'SELECT')")"
[[ "$granted" == t ]] || { echo "read-only role SELECT grant FAILED" >&2; exit 1; }
insertable="$(docker exec "$DBHOST" psql -U postgres -d "$DB" -Atc \
  "SELECT has_table_privilege('$ROUSER', 'assets_client', 'INSERT')")"
[[ "$insertable" == f ]] || { echo "read-only role still has INSERT privilege" >&2; exit 1; }
echo "read-only window armed and VERIFIED for $DB (tx_read_only=$armed, SELECT-only role $ROUSER)"

echo "--- management commands (image-contained source; required, fail closed) ---"
# This helper is outside the three-command zero-attempt SQL window, like the
# fingerprint helpers; do not mistake its startup for zero-attempt evidence.
docker exec $SHIM_ENV -e INVENTREE_DB_USER=$ROUSER \
  -e DM_BOOTSTRAP_NETLOG="$STATE_DIR_CTR/net_command_help.jsonl" \
  -e DM_BOOTSTRAP_NET_ALLOW="$NET_ALLOW" \
  "$RUNCTR" $MANAGE help | tee "$STATE_DIR_HOST/manage_help.txt"
for cmd in apply_demo_metrics plan_demo_metrics verify_demo_metrics \
           cleanup_demo_metrics stop_demo_metrics replay_demo_metrics \
           deploy_preflight; do
  grep -Eq "^[[:space:]]*$cmd[[:space:]]*$" "$STATE_DIR_HOST/manage_help.txt" || {
    echo "REQUIRED management command missing from the image: $cmd" >&2
    exit 1
  }
  echo "management command present: $cmd"
done
docker cp "$RUNCTR:$STATE_DIR_CTR/net_command_help.jsonl" \
  "$STATE_DIR_HOST/net_command_help.jsonl" >/dev/null
python3 "$VALIDATORS" netlog --path "$STATE_DIR_HOST/net_command_help.jsonl" \
  --label "command-discovery transport"

run_fp() {
  local tag="$1"
  local rc=0
  docker exec -i $SHIM_ENV -e INVENTREE_DB_USER=$ROUSER \
    -e DM_RELEASE_FP_TAG="$tag" \
    -e DM_BOOTSTRAP_NETLOG="$STATE_DIR_CTR/net_fingerprint_$tag.jsonl" \
    -e DM_BOOTSTRAP_NET_ALLOW="$NET_ALLOW" \
    "$RUNCTR" $MANAGE shell \
    < "$HARNESS/demo_metrics_release_fingerprint.py" \
    2>&1 | tee "$STATE_DIR_HOST/fingerprint_$tag.log" || rc=$?
  docker cp "$RUNCTR:$STATE_DIR_CTR/fingerprint_$tag.json" \
    "$STATE_DIR_HOST/fingerprint_$tag.json" >/dev/null 2>&1 || true
  docker cp "$RUNCTR:$STATE_DIR_CTR/net_fingerprint_$tag.jsonl" \
    "$STATE_DIR_HOST/net_fingerprint_$tag.jsonl" >/dev/null 2>&1 || true
  local evidence_rc=0
  python3 "$VALIDATORS" netlog \
    --path "$STATE_DIR_HOST/net_fingerprint_$tag.jsonl" \
    --label "net_fingerprint_$tag.jsonl" || evidence_rc=1
  python3 "$VALIDATORS" artifact \
    --path "$STATE_DIR_HOST/fingerprint_$tag.json" \
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
    "$RUNCTR" $MANAGE "$@" 2>&1 | tee "$STATE_DIR_HOST/probe_$tag.log" || rc=$?
  echo "-- probe [$tag] exit code: $rc"
  docker cp "$RUNCTR:$STATE_DIR_CTR/net_$tag.jsonl" \
    "$STATE_DIR_HOST/net_$tag.jsonl" >/dev/null 2>&1 || true
  local evidence_rc=0
  python3 "$VALIDATORS" artifact \
    --path "$STATE_DIR_HOST/probe_$tag.log" --label "probe_$tag.log" || evidence_rc=1
  python3 "$VALIDATORS" netlog \
    --path "$STATE_DIR_HOST/net_$tag.jsonl" --label "net_$tag.jsonl" || evidence_rc=1
  echo "$tag $rc" >> "$STATE_DIR_HOST/exit_codes.txt"
  if [[ $rc -ne 0 ]]; then
    echo "HARNESS VALIDATION FAILED: probe [$tag] exit code $rc; propagating" >&2
    return "$rc"
  fi
  [[ $evidence_rc -eq 0 ]] || exit 1
  python3 "$VALIDATORS" probe-exit --tag "$tag" --rc "$rc"
}

echo "== 8/10 fingerprint BEFORE (read-only role) =="
run_fp before

# Preserve subsecond boundaries: rounding the end down loses final statements.
T0="$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
echo "== 9/10 whole-process read-only probes (one fresh process per command, image-contained source) =="
run_probe plan    plan_demo_metrics --fixture "$FIXTURE_PATH" --mapping "$STATE_DIR_CTR/mapping.json" --out "$STATE_DIR_CTR/plan.json" --actor "$ACTOR"
run_probe verify  verify_demo_metrics --session "$SESSION_KEY" --actor "$ACTOR"
run_probe cleanup cleanup_demo_metrics --session "$SESSION_KEY" --actor "$ACTOR" --out "$STATE_DIR_CTR/cleanup-plan.json"
echo "-- expected command artifacts"
for f in plan.json cleanup-plan.json; do
  docker cp "$RUNCTR:$STATE_DIR_CTR/$f" "$STATE_DIR_HOST/$f" >/dev/null
  python3 "$VALIDATORS" artifact --path "$STATE_DIR_HOST/$f" --label "$f"
done
T1="$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"

echo "== 10/10 fingerprint AFTER + compare + negative probes + server-side scans =="
run_fp after
python3 "$RELEASE_CLI" compare-fingerprints \
  --before "$STATE_DIR_HOST/fingerprint_before.json" \
  --after "$STATE_DIR_HOST/fingerprint_after.json" \
  --expect-user "$ROUSER"

T2="$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
rc=0
docker exec -i $SHIM_ENV -e INVENTREE_DB_USER=$ROUSER \
  -e DM_BOOTSTRAP_NETLOG="$STATE_DIR_CTR/net_negative.jsonl" \
  -e DM_BOOTSTRAP_NET_ALLOW="$NET_ALLOW" \
  "$RUNCTR" $MANAGE shell \
  < "$HARNESS/demo_metrics_release_negative.py" \
  2>&1 | tee "$STATE_DIR_HOST/probe_negative.log" || rc=$?
T3="$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
docker cp "$RUNCTR:$STATE_DIR_CTR/net_negative.jsonl" \
  "$STATE_DIR_HOST/net_negative.jsonl" >/dev/null 2>&1 || true
echo "negative $rc" >> "$STATE_DIR_HOST/exit_codes.txt"
[[ $rc -eq 0 ]] || { echo "NEGATIVE PROBES FAILED (exit $rc)" >&2; exit 1; }
python3 "$VALIDATORS" artifact --path "$STATE_DIR_HOST/probe_negative.log" \
  --label "probe_negative.log"
python3 "$VALIDATORS" netlog \
  --path "$STATE_DIR_HOST/net_negative.jsonl" --label "net_negative.jsonl" \
  --expect-blocked

echo "-- server-side SQL statement log evidence (fail closed)"
# Capture failures must propagate: a `docker logs` error message left in a
# nonempty file must never pass artifact validation or the SQL scan. The
# scans below additionally require real server statement evidence (LOG
# statement/execute lines) in BOTH captures before anything is believed.
docker logs --since "$T0" --until "$T1" "$DBHOST" > "$STATE_DIR_HOST/db_statements_readonly_window.log" 2>&1 || {
  echo 'SERVER SQL EVIDENCE CAPTURE FAILED (docker logs, read-only window)' >&2
  exit 1
}
docker logs --since "$T2" --until "$T3" "$DBHOST" > "$STATE_DIR_HOST/db_statements_negative_window.log" 2>&1 || {
  echo 'SERVER SQL EVIDENCE CAPTURE FAILED (docker logs, negative window)' >&2
  exit 1
}
python3 "$VALIDATORS" artifact \
  --path "$STATE_DIR_HOST/db_statements_readonly_window.log" \
  --label "db_statements_readonly_window.log (server-side evidence)"
python3 "$VALIDATORS" artifact \
  --path "$STATE_DIR_HOST/db_statements_negative_window.log" \
  --label "db_statements_negative_window.log (server-side evidence)"
python3 "$RELEASE_CLI" scan-log \
  --log "$STATE_DIR_HOST/db_statements_readonly_window.log" \
  --label "read-only window"
python3 "$RELEASE_CLI" scan-log \
  --log "$STATE_DIR_HOST/db_statements_negative_window.log" \
  --label "negative window" --expect-write
grep -Eq 'INSERT INTO .?assets_client' \
  "$STATE_DIR_HOST/db_statements_negative_window.log" || {
  echo 'NEGATIVE PROBE: attempted INSERT not found in the server statement log — probe FAILED' >&2
  exit 1; }
# Separately validated MATCHING server refusal: the SQLSTATE the negative
# probe classified (25006/42501) must have its matching refusal text in the
# server statement log — the client-side exception alone is never evidence.
neg_sqlstate="$(grep -Eo 'write_refusal_sqlstate=[0-9A-Z]{5}' \
  "$STATE_DIR_HOST/probe_negative.log" | tail -n1 | cut -d= -f2)" || neg_sqlstate=''
[[ -n "$neg_sqlstate" ]] || {
  echo 'NEGATIVE PROBE: classified refusal sqlstate missing from probe output — probe FAILED' >&2
  exit 1; }
refusal_re="$(python3 "$RELEASE_CLI" refusal-pattern --sqlstate "$neg_sqlstate")" || {
  echo "NEGATIVE PROBE: '$neg_sqlstate' is not a write-defense SQLSTATE — probe FAILED" >&2
  exit 1; }
grep -Eq "$refusal_re" "$STATE_DIR_HOST/db_statements_negative_window.log" || {
  echo "NEGATIVE PROBE: server refusal matching sqlstate $neg_sqlstate not found in the server statement log — probe FAILED" >&2
  exit 1; }
echo "negative probe write attempt and matching server-side refusal (sqlstate $neg_sqlstate) both recorded"

echo "-- write-marker scan on probe logs"
if grep -El 'read-only transaction|permission denied for (table|schema|sequence)' \
    "$STATE_DIR_HOST/probe_plan.log" "$STATE_DIR_HOST/probe_verify.log" \
    "$STATE_DIR_HOST/probe_cleanup.log"; then
  echo 'WRITE MARKER FOUND in read-only command output — probe FAILED' >&2
  exit 1
fi
echo 'no write markers in plan/verify/cleanup output'

echo "-- transport guard logs (fail closed: guard marker required, zero blocked outside the negative probe)"
for name in net_plan net_verify net_cleanup net_fingerprint_before net_fingerprint_after; do
  python3 "$VALIDATORS" netlog --path "$STATE_DIR_HOST/$name.jsonl" --label "$name.jsonl"
done
python3 "$VALIDATORS" netlog --path "$STATE_DIR_HOST/net_negative.jsonl" \
  --label "net_negative.jsonl" --expect-blocked

echo "== probe complete =="
echo "artifacts: $STATE_DIR_HOST"
echo "NOTE: LOCAL offline evidence for an UNCOMMITTED LOCAL CANDIDATE only."
echo "      Human review, commit, image publication and deployment approval"
echo "      remain REQUIRED and were NOT performed."
