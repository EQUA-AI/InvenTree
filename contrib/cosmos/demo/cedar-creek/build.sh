#!/usr/bin/env bash
# Build the portable Cedar Creek UI demo dataset (Import Sequence step 18).
#
# Starts a throwaway Postgres + app container on its own Docker network, migrates an
# empty database, populates it from the pinned workbook, replays the population to
# prove it is idempotent, runs the read-only QA gates, and writes a pg_dump plus a
# build report to data/demo/cedar-creek/<version>/. Nothing here touches Azure, Cosmos
# or the pop-test sandbox.
#
# Usage: build.sh <workbook.xlsx> [--keep] [--force]
#   --keep   leave the containers running and serve the UI on http://localhost:$DEMO_PORT
#   --force  overwrite an existing output folder for this version
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
[ -f "$REPO/tasks.py" ] && [ -d "$REPO/src/backend/InvenTree" ] || { echo "repo root not found from $HERE" >&2; exit 1; }
MANIFEST="$HERE/manifest.json"
NET=demonet
PG=demo-pg
APP=demo-app
IMAGE="${DEMO_APP_IMAGE:-aimms-merged-image}"
PG_IMAGE=pgvector/pgvector:pg16
DEMO_PORT="${DEMO_PORT:-8012}"
ACTOR=demo-admin
BACKEND=/home/inventree/src/backend/InvenTree

WORKBOOK="${1:-}"
shift || true
KEEP=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    --force) FORCE=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
[ -n "$WORKBOOK" ] && [ -f "$WORKBOOK" ] || { echo "usage: $0 <workbook.xlsx> [--keep] [--force]" >&2; exit 2; }

mf() { python3 -c "import json,sys; d=json.load(open('$MANIFEST')); print(eval('d' + sys.argv[1]))" "$1"; }
VERSION="$(mf "['version']")"
WANT_SHA="$(mf "['source_package']['workbook']['sha256']")"
CLIENT_NAME="$(mf "['population']['client_name']")"
CLIENT_CODE="$(mf "['population']['client_code']")"
STATION_UUID="$(mf "['population']['station_uuid']")"
STATION_NS="$(mf "['population']['station_source_namespace']")"
STATION_KEY="$(mf "['population']['station_source_key']")"
PER_PUMP="$(mf "['expected']['components_per_pump']")"

GOT_SHA="$(shasum -a 256 "$WORKBOOK" | cut -d' ' -f1)"
if [ "$GOT_SHA" != "$WANT_SHA" ]; then
  echo "workbook sha256 $GOT_SHA does not match manifest $WANT_SHA; refusing" >&2
  exit 1
fi

OUT_REL="data/demo/cedar-creek/$VERSION"
OUT="$REPO/$OUT_REL"
if [ -e "$OUT" ] && [ "$FORCE" -ne 1 ]; then
  echo "$OUT_REL exists; pass --force to rebuild" >&2
  exit 1
fi
rm -rf "$OUT"
mkdir -p "$OUT/input"
cp "$WORKBOOK" "$OUT/input/workbook.xlsx"
IN_CONTAINER_BOOK="/home/inventree/$OUT_REL/input/workbook.xlsx"

log() { STEP="$*"; printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
STEP=start
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "FAILED at step '$STEP' (exit $status); logs in $OUT_REL" >&2
  fi
  if [ "$KEEP" -ne 1 ]; then
    docker rm -f "$APP" "$PG" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

app_env=(
  -e INVENTREE_DB_ENGINE=postgresql -e INVENTREE_DB_HOST="$PG" -e INVENTREE_DB_PORT=5432
  -e INVENTREE_DB_NAME=inventree -e INVENTREE_DB_USER=inventree -e INVENTREE_DB_PASSWORD=inventree
  -e INVENTREE_DEBUG=True -e INVENTREE_PLUGINS_ENABLED=False -e INVENTREE_SECRET_KEY=demo-build-not-secret
  -e INVENTREE_DATA_DIR=/tmp/demodata -e INVENTREE_STATIC_ROOT=/home/inventree/data/static
  -e INVENTREE_MEDIA_ROOT=/tmp/demodata/media -e INVENTREE_BACKUP_DIR=/tmp/demodata/backup
  -e INVENTREE_SITE_URL="http://localhost:$DEMO_PORT" -e INVENTREE_WEB_ADDR=0.0.0.0 -e INVENTREE_WEB_PORT=8000
  -e INVENTREE_HOME=/home/inventree -e INVENTREE_BACKEND_DIR=/home/inventree/src/backend
  -e AIMMS_MAINTENANCE_SCOPE_RESOLVER=tasks.scope.granted_client_scope_resolver
  -e AIMMS_SINGLE_SITE_CLIENT_CODE="$CLIENT_CODE"
)
manage() { docker exec "$APP" sh -c "cd $BACKEND && python manage.py $*"; }

log "fresh containers ($PG, $APP on $NET)"
docker rm -f "$APP" "$PG" >/dev/null 2>&1 || true
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_DB=inventree -e POSTGRES_USER=inventree -e POSTGRES_PASSWORD=inventree \
  "$PG_IMAGE" >/dev/null
docker run -d --name "$APP" --network "$NET" -p "$DEMO_PORT:8000" \
  -v "$REPO:/home/inventree" "${app_env[@]}" -w "$BACKEND" --entrypoint sleep \
  "$IMAGE" infinity >/dev/null
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -U inventree -d inventree >/dev/null 2>&1 && break
  sleep 2
done
sleep 3

log "migrate"
manage "migrate --noinput" > "$OUT/migrate.log" 2>&1

log "actor $ACTOR"
ADMIN_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
umask 077
printf '%s\n' "$ADMIN_PASSWORD" > "$OUT/admin-password.txt"
umask 022
docker exec -e DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASSWORD" "$APP" sh -c \
  "cd $BACKEND && python manage.py createsuperuser --noinput --username $ACTOR --email demo-admin@example.invalid" \
  > "$OUT/actor.log" 2>&1

POP_ARGS="'$IN_CONTAINER_BOOK' --actor $ACTOR --client-name '$CLIENT_NAME' --station-uuid $STATION_UUID --json"

log "plan"
manage "populate_from_workbook '$IN_CONTAINER_BOOK' --client-name '$CLIENT_NAME' --station-uuid $STATION_UUID --json" \
  > "$OUT/plan.json" 2> "$OUT/plan.stderr"

log "apply (several minutes)"
manage "populate_from_workbook $POP_ARGS --apply --backup-ref empty-database-$VERSION" \
  > "$OUT/apply.json" 2> "$OUT/apply.stderr"

log "replay (must create nothing)"
manage "populate_from_workbook $POP_ARGS --apply --backup-ref replay-$VERSION" \
  > "$OUT/replay.json" 2> "$OUT/replay.stderr"

log "QA gates"
docker exec -i -e QA_STATION_PK=uuid -e QA_STATION_UUID="$STATION_UUID" \
  -e QA_STATION_NS="$STATION_NS" -e QA_STATION_KEY="$STATION_KEY" \
  -e QA_COMPONENTS_PER_PUMP="$PER_PUMP" "$APP" sh -c "cd $BACKEND && python manage.py shell" \
  < "$REPO/contrib/cosmos/devtools/cedar_creek_qa_gates.py" > "$OUT/qa-gates.raw" 2>&1
grep -E '^(PASS|FAIL|summary:)' "$OUT/qa-gates.raw" > "$OUT/qa-gates.txt" || true

log "dump"
docker exec "$PG" pg_dump -U inventree -d inventree -Fc --no-owner --no-acl > "$OUT/cedar-creek-demo.dump"

log "verify"
python3 "$HERE/verify_build.py" "$MANIFEST" "$OUT" "$(git -C "$REPO" rev-parse HEAD)" "$IMAGE"

(cd "$OUT" && shasum -a 256 cedar-creek-demo.dump build-report.json apply.json replay.json qa-gates.txt input/workbook.xlsx > SHA256SUMS)

if [ "$KEEP" -eq 1 ]; then
  docker exec -d "$APP" sh -c "cd $BACKEND && python -m gunicorn InvenTree.wsgi:application --bind 0.0.0.0:8000 --worker-class gthread --workers 1 --threads 4 --timeout 300"
  log "kept: http://localhost:$DEMO_PORT  user $ACTOR, password in $OUT_REL/admin-password.txt"
fi
log "done: $OUT_REL"
