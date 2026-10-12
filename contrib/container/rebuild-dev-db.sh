#!/usr/bin/env bash
# Rebuild the local dev database (contrib/container/dev-docker-compose.yml) on the
# pinned pgvector/pgvector:pg15 image, keeping the data from an older data directory.
#
# Why: data/pgdb created by the earlier postgres:17 image cannot be opened by the
# pg15 image the compose file now pins, and plain postgres has no `vector`
# extension, so the merged migrations cannot run on it (HANDOVER open item 7).
#
# What it does (nothing is deleted):
#   1. stops the compose db/server/worker containers;
#   2. moves data/pgdb aside to data/pgdb-pg<old major>-<timestamp>;
#   3. starts a throwaway container of the OLD major on that copy and pg_dumps it;
#   4. starts the compose db service (pg15 + vector), which initialises a fresh data/pgdb;
#   5. restores the dump, creates the `vector` extension and reports row counts.
# Migrations are left to the caller: `docker compose -f contrib/container/dev-docker-compose.yml
# run --rm inventree-dev-server invoke migrate`.
#
# Usage (from the repo root): contrib/container/rebuild-dev-db.sh [--dry-run]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker compose --project-directory "$REPO" -f "$REPO/contrib/container/dev-docker-compose.yml")
DATA="$REPO/data"
PGDATA_DIR="$DATA/pgdb"
DB_SERVICE=inventree-dev-db
PGUSER_NAME=pguser
DBNAME=inventree
OLD=rebuild-dev-db-old
STAMP="$(date +%Y%m%d-%H%M%S)"
DUMP="$DATA/backup/dev-db-before-pg15-$STAMP.sql"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
run() { if [ "$DRY" -eq 1 ]; then echo "DRY: $*"; else "$@"; fi; }

[ -f "$PGDATA_DIR/PG_VERSION" ] || { echo "no data directory at $PGDATA_DIR" >&2; exit 1; }
OLD_MAJOR="$(cat "$PGDATA_DIR/PG_VERSION")"
NEW_IMAGE="$("${COMPOSE[@]}" config --format json | python3 -c "import json,sys; print(json.load(sys.stdin)['services']['$DB_SERVICE']['image'])")"
NEW_MAJOR="$(echo "$NEW_IMAGE" | sed -nE 's/.*:pg([0-9]+).*/\1/p')"
log "data directory is PostgreSQL $OLD_MAJOR; compose pins $NEW_IMAGE"
if [ "$OLD_MAJOR" = "$NEW_MAJOR" ]; then
  log "majors match; nothing to rebuild (run migrations instead)"
  exit 0
fi

KEEP="$DATA/pgdb-pg$OLD_MAJOR-$STAMP"
log "1. stop compose services"
run "${COMPOSE[@]}" stop inventree-dev-worker inventree-dev-server "$DB_SERVICE"

log "2. move $PGDATA_DIR aside -> ${KEEP#$REPO/}"
run mv "$PGDATA_DIR" "$KEEP"

log "3. dump with postgres:$OLD_MAJOR"
run docker rm -f "$OLD" >/dev/null 2>&1 || true
run docker run -d --name "$OLD" -e PGDATA=/var/lib/postgresql/data/pgdb \
  -e POSTGRES_PASSWORD=unused -v "$KEEP:/var/lib/postgresql/data/pgdb" "postgres:$OLD_MAJOR"
if [ "$DRY" -eq 0 ]; then
  for _ in $(seq 1 60); do docker exec "$OLD" pg_isready -U "$PGUSER_NAME" -d "$DBNAME" >/dev/null 2>&1 && break; sleep 2; done
  mkdir -p "$DATA/backup"
  docker exec "$OLD" pg_dump -U "$PGUSER_NAME" -d "$DBNAME" --no-owner --no-acl > "$DUMP"
  docker rm -f "$OLD" >/dev/null
  log "   dump: ${DUMP#$REPO/} ($(du -h "$DUMP" | cut -f1))"
fi

log "4. start $DB_SERVICE ($NEW_IMAGE) on a fresh data directory"
run "${COMPOSE[@]}" up -d "$DB_SERVICE"
DB_CTR="$("${COMPOSE[@]}" ps -q "$DB_SERVICE" 2>/dev/null || true)"
if [ "$DRY" -eq 0 ]; then
  for _ in $(seq 1 60); do docker exec "$DB_CTR" pg_isready -U "$PGUSER_NAME" -d "$DBNAME" >/dev/null 2>&1 && break; sleep 2; done
  sleep 3
  log "5. restore and enable vector"
  # A newer pg_dump can emit settings an older server does not know
  # (e.g. transaction_timeout); those lines fail harmlessly, so no ON_ERROR_STOP.
  docker exec -i "$DB_CTR" psql -q -U "$PGUSER_NAME" -d "$DBNAME" < "$DUMP" > "$DATA/backup/dev-db-restore-$STAMP.log" 2>&1 || true
  docker exec "$DB_CTR" psql -U "$PGUSER_NAME" -d "$DBNAME" -c 'CREATE EXTENSION IF NOT EXISTS vector;' >/dev/null
  log "   restore messages: $(grep -c ERROR "$DATA/backup/dev-db-restore-$STAMP.log" || true) errors (log: data/backup/dev-db-restore-$STAMP.log)"
  grep ERROR "$DATA/backup/dev-db-restore-$STAMP.log" | sort | uniq -c | head -10 || true
  docker exec "$DB_CTR" psql -U "$PGUSER_NAME" -d "$DBNAME" -At -c "
    select 'server', current_setting('server_version')
    union all select 'vector', extversion from pg_extension where extname = 'vector'
    union all select 'tables', count(*)::text from information_schema.tables where table_schema = 'public'
    union all select 'migrations', count(*)::text from django_migrations
    union all select 'machines', count(*)::text from assets_assetmachine
    union all select 'parts', count(*)::text from part_part
    union all select 'users', count(*)::text from auth_user;"
fi
log "done; old data kept at ${KEEP#$REPO/}. Next: run migrations (see header)."
