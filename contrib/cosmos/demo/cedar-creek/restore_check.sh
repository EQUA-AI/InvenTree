#!/usr/bin/env bash
# Restore a built Cedar Creek demo dump into a throwaway Postgres and count key rows.
# Usage: restore_check.sh <output dir containing cedar-creek-demo.dump>
set -euo pipefail
DIR="${1:?usage: $0 <output dir>}"
PG=demo-restore-check
docker rm -f "$PG" >/dev/null 2>&1 || true
trap 'docker rm -f "$PG" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$PG" -e POSTGRES_DB=restored -e POSTGRES_USER=check -e POSTGRES_PASSWORD=check \
  pgvector/pgvector:pg16 >/dev/null
for _ in $(seq 1 60); do docker exec "$PG" pg_isready -U check -d restored >/dev/null 2>&1 && break; sleep 2; done
sleep 3
docker cp "$DIR/cedar-creek-demo.dump" "$PG:/tmp/demo.dump"
docker exec "$PG" pg_restore --no-owner --no-acl --exit-on-error -U check -d restored /tmp/demo.dump
docker exec "$PG" psql -U check -d restored -At -F ' ' -c "
select 'locations', count(*) from assets_assetlocation where code like 'cc-%'
union all select 'machines', count(*) from assets_assetmachine
union all select 'components', count(*) from assets_assetcomponent
union all select 'part_links', count(*) from assets_machinepart
union all select 'cc_parts', count(*) from part_part where \"IPN\" like 'CC-%'
union all select 'history', count(*) from assets_assetmaintenancerecord
union all select 'work_orders', count(*) from tasks_workorder
union all select 'stock_items', count(*) from stock_stockitem;"
