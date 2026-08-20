#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE=${ENV_FILE:-"$ROOT_DIR/.env"}
COMPOSE_FILE=${COMPOSE_FILE:-"$ROOT_DIR/deploy/docker-compose.prod.yml"}
BACKUP_ROOT=${BACKUP_ROOT:-"$ROOT_DIR/backups"}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$BACKUP_ROOT/$STAMP"

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

umask 077
mkdir -p "$TARGET"

printf '%s\n' "[backup] PostgreSQL"
compose exec -T postgres sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$TARGET/postgres.dump"

test -s "$TARGET/postgres.dump"

printf '%s\n' "[backup] local media/app_data volume"
# app_data may be empty when external S3 is used; preserving it is still safe.
API_CONTAINER=$(compose ps -a -q api)
if [ -z "$API_CONTAINER" ]; then
  echo "API container does not exist; start the production stack once before backup." >&2
  exit 1
fi
APP_DATA_SOURCE=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' "$API_CONTAINER")
if [ -z "$APP_DATA_SOURCE" ]; then
  echo "Unable to locate app_data mount from API container" >&2
  exit 1
fi
docker run --rm \
  -v "$APP_DATA_SOURCE:/backup-source:ro" \
  -v "$TARGET:/backup-target" \
  alpine:3.22 sh -lc 'cd /backup-source && tar -czf /backup-target/app_data.tar.gz .'

cat > "$TARGET/RECOVERY_KEYS.txt" <<'EOF'
Autoposter encrypted social credentials require the AUTOPOSTER_CREDENTIAL_KEYS key ring.
The key ring is intentionally NOT copied into this backup. Store it in a separate secrets vault.
EOF

(
  cd "$TARGET"
  sha256sum postgres.dump app_data.tar.gz RECOVERY_KEYS.txt > SHA256SUMS
)

printf '%s\n' "Backup created: $TARGET"
