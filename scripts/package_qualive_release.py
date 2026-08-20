#!/usr/bin/env python3
"""Build the immutable combined Qualive server release directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path


def write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--factory-sha", required=True)
    parser.add_argument("--autoposter-sha", required=True)
    parser.add_argument("--tested-merge-sha", default="")
    parser.add_argument("--ci-run-id", default="")
    parser.add_argument("--ci-run-number", default="")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    factory = root / "content-factory"
    autoposter = root / "autoposter"
    if not (factory / "scripts/server-preflight.py").is_file():
        raise SystemExit("Content Factory checkout is missing server-preflight.py")
    if not (autoposter / "scripts/server-preflight.py").is_file():
        raise SystemExit("Autoposter checkout is missing server-preflight.py")

    write(
        root / "deploy/factory.edge.override.yml",
        """
services:
  web:
    networks:
      app: {}
      qualive-edge:
        aliases: [content-factory-web]
  minio:
    networks:
      data: {}
      assets: {}
      qualive-edge:
        aliases: [content-factory-assets]
  caddy:
    profiles: [standalone-edge]
networks:
  qualive-edge:
    external: true
    name: qualive-edge
""",
    )
    write(
        root / "deploy/autoposter.edge.override.yml",
        """
services:
  web:
    networks:
      backend: {}
      qualive-edge:
        aliases: [autoposter-web]
  caddy:
    profiles: [standalone-edge]
networks:
  qualive-edge:
    external: true
    name: qualive-edge
""",
    )
    write(
        root / "deploy/edge-compose.yml",
        """
services:
  edge:
    image: caddy:2.10-alpine
    restart: unless-stopped
    environment:
      FACTORY_DOMAIN: ${FACTORY_DOMAIN:?Set FACTORY_DOMAIN}
      FACTORY_ASSETS_DOMAIN: ${FACTORY_ASSETS_DOMAIN:?Set FACTORY_ASSETS_DOMAIN}
      AUTOPOSTER_DOMAIN: ${AUTOPOSTER_DOMAIN:?Set AUTOPOSTER_DOMAIN}
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - edge_caddy_data:/data
      - edge_caddy_config:/config
    networks: [qualive-edge, qualive-backbone]
networks:
  qualive-edge:
    external: true
  qualive-backbone:
    external: true
volumes:
  edge_caddy_data: {}
  edge_caddy_config: {}
""",
    )
    write(
        root / "deploy/Caddyfile",
        """
{$FACTORY_DOMAIN} {
    encode zstd gzip
    header {
        -Server
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy no-referrer
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }
    reverse_proxy content-factory-web:3000
}

{$FACTORY_ASSETS_DOMAIN} {
    encode zstd gzip
    header {
        -Server
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }
    @asset_get {
        method GET HEAD
        path /content-assets/*
    }
    handle @asset_get {
        reverse_proxy content-factory-assets:9000
    }
    respond 404
}

{$AUTOPOSTER_DOMAIN} {
    encode zstd gzip
    request_body {
        max_size 520MB
    }
    header {
        -Server
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy no-referrer
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }
    @api_public {
        path /health /ready /auth/telegram/callback /api/v1/oauth/*/callback
    }
    handle @api_public {
        reverse_proxy autoposter-api:8000
    }
    handle {
        reverse_proxy autoposter-web:3000
    }
}
""",
    )

    write(
        root / "deploy/up.sh",
        r'''#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FACTORY_ENV="$ROOT/content-factory/.env"
AUTOPOSTER_ENV_FILE="$ROOT/autoposter/.env"

test -f "$FACTORY_ENV" || { echo "Missing $FACTORY_ENV" >&2; exit 1; }
test -f "$AUTOPOSTER_ENV_FILE" || { echo "Missing $AUTOPOSTER_ENV_FILE" >&2; exit 1; }

env_value() {
  file=$1
  key=$2
  value=$(grep -m1 "^${key}=" "$file" | cut -d= -f2- || true)
  value=$(printf '%s' "$value" | sed 's/^"//; s/"$//; s/^'"'"'//; s/'"'"'$//')
  printf '%s' "$value"
}

FACTORY_DOMAIN=$(env_value "$FACTORY_ENV" FACTORY_DOMAIN)
FACTORY_ASSETS_DOMAIN=$(env_value "$FACTORY_ENV" FACTORY_ASSETS_DOMAIN)
AUTOPOSTER_DOMAIN=$(env_value "$AUTOPOSTER_ENV_FILE" AUTOPOSTER_DOMAIN)
for pair in "FACTORY_DOMAIN:$FACTORY_DOMAIN" "FACTORY_ASSETS_DOMAIN:$FACTORY_ASSETS_DOMAIN" "AUTOPOSTER_DOMAIN:$AUTOPOSTER_DOMAIN"; do
  key=${pair%%:*}; value=${pair#*:}
  test -n "$value" || { echo "$key is empty" >&2; exit 1; }
done
export FACTORY_DOMAIN FACTORY_ASSETS_DOMAIN AUTOPOSTER_DOMAIN

docker network inspect qualive-backbone >/dev/null 2>&1 || docker network create qualive-backbone >/dev/null
docker network inspect qualive-edge >/dev/null 2>&1 || docker network create qualive-edge >/dev/null

# Preflight uses only stdlib and runs in a disposable container, so the host needs Docker only.
docker run --rm --env-file "$FACTORY_ENV" -v "$ROOT/content-factory:/app:ro" -w /app python:3.12-slim \
  python scripts/server-preflight.py --env /dev/null
docker run --rm --env-file "$AUTOPOSTER_ENV_FILE" -v "$ROOT/autoposter:/app:ro" -w /app python:3.12-slim \
  python scripts/server-preflight.py --env /dev/null

docker compose --env-file "$FACTORY_ENV" \
  -f "$ROOT/content-factory/deploy/docker-compose.prod.yml" \
  -f "$ROOT/deploy/factory.edge.override.yml" up -d --build

docker compose --env-file "$AUTOPOSTER_ENV_FILE" \
  -f "$ROOT/autoposter/deploy/docker-compose.prod.yml" \
  -f "$ROOT/deploy/autoposter.edge.override.yml" up -d --build

FACTORY_DOMAIN="$FACTORY_DOMAIN" FACTORY_ASSETS_DOMAIN="$FACTORY_ASSETS_DOMAIN" AUTOPOSTER_DOMAIN="$AUTOPOSTER_DOMAIN" \
  docker compose -f "$ROOT/deploy/edge-compose.yml" up -d

echo "Qualive server stack started. Run ./deploy/status.sh for health."''',
        executable=True,
    )
    write(
        root / "deploy/down.sh",
        r'''#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FACTORY_ENV="$ROOT/content-factory/.env"
AUTOPOSTER_ENV_FILE="$ROOT/autoposter/.env"

# Edge first, then applications. Volumes are deliberately preserved.
FACTORY_DOMAIN=x FACTORY_ASSETS_DOMAIN=x AUTOPOSTER_DOMAIN=x docker compose -f "$ROOT/deploy/edge-compose.yml" down || true
docker compose --env-file "$AUTOPOSTER_ENV_FILE" -f "$ROOT/autoposter/deploy/docker-compose.prod.yml" -f "$ROOT/deploy/autoposter.edge.override.yml" down || true
docker compose --env-file "$FACTORY_ENV" -f "$ROOT/content-factory/deploy/docker-compose.prod.yml" -f "$ROOT/deploy/factory.edge.override.yml" down || true
''',
        executable=True,
    )
    write(
        root / "deploy/status.sh",
        r'''#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
echo "=== Docker containers ==="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
echo
echo "=== Shared networks ==="
docker network inspect qualive-backbone --format '{{.Name}}: {{len .Containers}} containers' 2>/dev/null || true
docker network inspect qualive-edge --format '{{.Name}}: {{len .Containers}} containers' 2>/dev/null || true
''',
        executable=True,
    )
    write(
        root / "deploy/verify.sh",
        r'''#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
sha256sum -c SHA256SUMS
''',
        executable=True,
    )

    write(
        root / "DEPLOY.md",
        """
# Qualive server release

This archive contains two independently deployable applications connected by `content-package/1.1` plus one shared HTTPS edge for a single Linux server.

## Requirements

- Docker Engine and Docker Compose v2
- DNS A/AAAA records for three names: `FACTORY_DOMAIN`, `FACTORY_ASSETS_DOMAIN`, `AUTOPOSTER_DOMAIN`
- inbound TCP 80/443 (and UDP 443 if HTTP/3 is desired)

No host Python installation is required by `deploy/up.sh`.

## Configure

```bash
cp content-factory/.env.example content-factory/.env
cp autoposter/.env.example autoposter/.env
```

Replace every development/default secret. Required pairings:

- Factory `AUTOPOSTER_TOKEN` = Autoposter `CONTENT_FACTORY_INGEST_TOKEN`
- Factory `PERFORMANCE_INGEST_TOKEN` = Autoposter `CONTENT_FACTORY_PERFORMANCE_TOKEN`
- Autoposter `CONTENT_FACTORY_PERFORMANCE_URL=http://content-factory-api:8000/performance/ingest`
- Autoposter `CONTENT_FACTORY_MEDIA_ALLOWED_HOSTS=<FACTORY_ASSETS_DOMAIN value>`
- Autoposter `CONTENT_FACTORY_MEDIA_REQUIRE_ALLOWLIST=true`
- Autoposter `CONTENT_FACTORY_MEDIA_ALLOW_PRIVATE_HOSTS=false`
- Factory `MODEL_ROUTER_MODE=shadow` for the first production evidence period

Autoposter also requires a valid Fernet key ring in `AUTOPOSTER_CREDENTIAL_KEYS`, Telegram OIDC values, and a long PostgreSQL password. Factory preflight requires long secrets and explicit production hosts.

## Verify archive

```bash
./deploy/verify.sh
```

## Deploy

```bash
./deploy/up.sh
./deploy/status.sh
```

The script uses one shared Caddy on host ports 80/443. The Caddy services inside each application are disabled by the release overrides, preventing port collisions.

## Acceptance after DNS/credentials exist

1. Open both applications over HTTPS and check `/ready`.
2. Run `content-factory/scripts/bridge-smoke.py` against the internal Autoposter bridge.
3. Send one approved `content-package/1.1` item and verify replay/idempotency.
4. Verify an image is copied into Autoposter-owned storage.
5. Publish one real platform variant.
6. Confirm one analytics milestone returns exactly once with matching delivery/prompt/model lineage.

Steps 4–6 depend on real provider/social credentials and cannot be truthfully certified by offline CI.

## Backup / recovery

Both applications contain `deploy/backup.sh` and `deploy/restore.sh`. Copy backups off-server. Store the Autoposter credential encryption key ring separately from database/media backups.
""",
    )

    manifest = {
        "release_format": "qualive-server-release/1.0",
        "factory_sha": args.factory_sha,
        "autoposter_head_sha": args.autoposter_sha,
        "tested_merge_sha": args.tested_merge_sha or args.autoposter_sha,
        "content_contract": "content-package/1.1",
        "factory_migration_head": "0014_audit_events",
        "autoposter_content_os": "0.12",
        "ci_run_id": args.ci_run_id or None,
        "ci_run_number": args.ci_run_number or None,
        "external_e2e": "requires operator provider/social credentials",
    }
    write(root / "RELEASE_MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True))

    # Remove Git metadata if the caller used normal checkouts.
    for child in (factory / ".git", autoposter / ".git"):
        if child.exists():
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()

    checksum_path = root / "SHA256SUMS"
    rows: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p != checksum_path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(root).as_posix()}")
    write(checksum_path, "\n".join(rows))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"release_files={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
