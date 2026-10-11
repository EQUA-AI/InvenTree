#!/usr/bin/env bash
# Local sandbox for workbook population: throwaway Postgres + app container on port 8010.
# Nothing here touches Azure. Usage: sandbox.sh up|down|status|shell|manage <args>
set -u
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
NET=popnet
PG=pop-pg
APP=pop-test
IMAGE=aimms-merged-image
PORT=8010

app_env=(
  -e INVENTREE_DB_ENGINE=postgresql -e INVENTREE_DB_HOST="$PG" -e INVENTREE_DB_PORT=5432
  -e INVENTREE_DB_NAME=inventree -e INVENTREE_DB_USER=inventree -e INVENTREE_DB_PASSWORD=inventree
  -e INVENTREE_DEBUG=True -e INVENTREE_PLUGINS_ENABLED=False -e INVENTREE_SECRET_KEY=test-only-not-secret
  -e INVENTREE_DATA_DIR=/tmp/popdata -e INVENTREE_STATIC_ROOT=/home/inventree/data/static
  -e INVENTREE_MEDIA_ROOT=/tmp/popdata/media -e INVENTREE_BACKUP_DIR=/tmp/popdata/backup
  -e INVENTREE_SITE_URL="http://localhost:$PORT" -e INVENTREE_WEB_ADDR=0.0.0.0 -e INVENTREE_WEB_PORT=8000
  -e INVENTREE_HOME=/home/inventree -e INVENTREE_BACKEND_DIR=/home/inventree/src/backend
  # Production-recommended scope mode: explicit grants win, ungranted users fall
  # back to the single-site client. Registered stations/pumps are only visible
  # to actors whose scope includes their client (assets/api.py registry_visibility).
  -e AIMMS_MAINTENANCE_SCOPE_RESOLVER=tasks.scope.granted_client_scope_resolver
  -e AIMMS_SINGLE_SITE_CLIENT_CODE=internal
)
BACKEND=/home/inventree/src/backend/InvenTree

case "${1:-status}" in
  up)
    docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
    if ! docker ps -a --format '{{.Names}}' | grep -qx "$PG"; then
      docker run -d --name "$PG" --network "$NET" \
        -e POSTGRES_DB=inventree -e POSTGRES_USER=inventree -e POSTGRES_PASSWORD=inventree \
        pgvector/pgvector:pg16 >/dev/null
      sleep 6
    else
      docker start "$PG" >/dev/null
    fi
    docker rm -f "$APP" >/dev/null 2>&1 || true
    docker run -d --name "$APP" --network "$NET" -p "$PORT:8000" \
      -v "$REPO:/home/inventree" "${app_env[@]}" \
      -w "$BACKEND" --entrypoint python \
      "$IMAGE" -m gunicorn InvenTree.wsgi:application --bind 0.0.0.0:8000 \
        --chdir "$BACKEND" --pythonpath "$BACKEND" \
        --worker-class gthread --workers 1 --threads 4 --timeout 300 >/dev/null
    echo "app starting on http://localhost:$PORT (db: $PG)"
    ;;
  down)
    docker rm -f "$APP" "$PG" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    echo "sandbox removed"
    ;;
  status)
    docker ps --filter name="$PG" --filter name="$APP" --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
    curl -s -o /dev/null -w "http://localhost:$PORT/api/ -> HTTP %{http_code}\n" "http://localhost:$PORT/api/" || true
    ;;
  manage)
    shift
    docker exec "$APP" sh -c "cd /home/inventree/src/backend/InvenTree && python manage.py $* 2>&1 | grep -vE 'WARNING|warning|Attachment RAG|objects imported'"
    ;;
  shell)
    docker exec -it "$APP" sh -c "cd /home/inventree/src/backend/InvenTree && python manage.py shell"
    ;;
  *)
    echo "usage: $0 up|down|status|shell|manage <args>" >&2
    exit 2
    ;;
esac
