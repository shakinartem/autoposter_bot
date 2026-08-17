#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

exec docker compose --env-file ../.env -f docker-compose.prod.yml "$@"
