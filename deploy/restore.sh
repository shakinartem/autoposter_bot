#!/usr/bin/env sh
set -eu

if [ "${CONFIRM_RESTORE:-}" != "YES" ]; then
  echo "Refusing destructive restore. Set CONFIRM_RESTORE=YES." >&2
  exit 2
fi
if [ $# -ne 1 ]; then
  echo "Usage: CONFIRM_RESTORE=YES $0 /path/to/backup" >&2
  exit 2
fi

BACKUP_DIR=$1
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
COMPOSE_FILE=${COMPOSE_FILE:-"$ROOT_DIR/deploy/docker-compose.prod.yml"}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

cd "$BACKUP_DIR"
sha256sum -c SHA256SUMS
cd - >/dev/null

printf '%s\n' "[restore] stopping writers"
compose stop api publication-worker analytics-worker reconciliation-worker web
compose up -d postgres

printf '%s\n' "[restore] PostgreSQL"
compose exec -T postgres sh -lc 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
cat "$BACKUP_DIR/postgres.dump" | compose exec -T postgres sh -lc 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges'

printf '%s\n' "[restore] app_data"
API_CONTAINER=$(compose ps -a -q api)
if [ -z "$API_CONTAINER" ]; then
  echo "API container does not exist; deploy the production stack once before restore." >&2
  exit 1
fi
APP_DATA_SOURCE=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' "$API_CONTAINER")
if [ -z "$APP_DATA_SOURCE" ]; then
  echo "Unable to locate app_data mount from API container" >&2
  exit 1
fi
docker run --rm \
  -v "$APP_DATA_SOURCE:/restore-target" \
  -v "$BACKUP_DIR:/backup-source:ro" \
  alpine:3.22 sh -lc 'find /restore-target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzf /backup-source/app_data.tar.gz -C /restore-target'

printf '%s\n' "[restore] restarting application"
compose up -d
printf '%s\n' "Restore completed. Verify /ready and encrypted account credentials before publishing."
