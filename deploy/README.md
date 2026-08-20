# Autoposter production topology

This compose stack exposes only Caddy on the public network. PostgreSQL, FastAPI,
Next.js and both workers stay on the private Docker network.

## Public routes

The single public origin is `https://$AUTOPOSTER_DOMAIN`.

Caddy sends these provider-facing routes directly to FastAPI:

- `/health`
- `/auth/telegram/callback`
- `/api/v1/oauth/*/callback`

Everything else goes to Next.js. The browser therefore talks to the backend
through the server-side `/api/autoposter/*` BFF instead of receiving a service
API key or direct database/API access.

## Required production values

Create the root `.env` and keep it outside version control. At minimum configure:

```env
AUTOPOSTER_DOMAIN=autoposter.example.com
POSTGRES_PASSWORD=replace-with-long-random-password

AUTOPOSTER_WEB_AUTH_MODE=session
AUTOPOSTER_REQUIRE_API_AUTH=1
AUTOPOSTER_CREDENTIAL_KEYS=<fernet-key>
AUTOPOSTER_LOGIN_STATE_KEYS=<fernet-key-or-separate-key>
AUTOPOSTER_OAUTH_STATE_KEYS=<fernet-key-or-separate-key>

TELEGRAM_OIDC_CLIENT_ID=...
TELEGRAM_OIDC_CLIENT_SECRET=...
TELEGRAM_OIDC_REDIRECT_URI=https://autoposter.example.com/auth/telegram/callback

TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=https://autoposter.example.com/api/v1/oauth/tiktok/callback

INSTAGRAM_APP_ID=...
INSTAGRAM_APP_SECRET=...
INSTAGRAM_REDIRECT_URI=https://autoposter.example.com/api/v1/oauth/instagram/callback
```

Use the exact production domain in BotFather, TikTok and Instagram provider
configuration. Do not expose PostgreSQL or port 8000 directly from the host.

## Start

From `deploy/`:

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

Check:

```bash
curl -fsS https://$AUTOPOSTER_DOMAIN/health
```

Then open `https://$AUTOPOSTER_DOMAIN/login` and complete Telegram Web Login.

## Processes

- `api`: FastAPI, auth, workspace APIs, social connection callbacks.
- `web`: Next.js user interface and BFF.
- `publication-worker`: scheduled publishing + credential refresh + bounded retries + durable pre-network checkpoints.
- `reconciliation-worker`: async provider status resolution for `processing`/trackable ambiguous publications.
- `analytics-worker`: milestone performance snapshots; currently Instagram collector only.
- `health-worker`: deduplicated critical workspace health alerts to active Telegram admin/owner recipients.
- `account-health-worker`: read-only TikTok/Instagram/Telegram credential probes; VK stays explicitly unverified until a safe remote probe is enabled.
- `postgres`: production persistence and queue locking.
- `caddy`: TLS termination and minimal public routing.

## Backups

At minimum back up PostgreSQL and any local media volume. Social credentials are
encrypted at rest; the `AUTOPOSTER_CREDENTIAL_KEYS` key ring is therefore part
of the disaster-recovery material and must be stored separately from database
backups. Losing the key ring makes encrypted social credentials unrecoverable.

## Rollout order

1. Configure DNS and provider callback URLs.
2. Create credential/state key rings and PostgreSQL password.
3. Start PostgreSQL + API and verify `/health`.
4. Start web and verify Telegram login.
5. Start publication worker and create a dry-run/test publication.
6. Start reconciliation worker before enabling async providers such as TikTok.
7. Start analytics worker after at least one platform account is publishing with durable final remote IDs.
8. Check `/api/v1/operations/overview` as an admin and verify queue lag / unknown outcomes are zero.
9. Only then migrate existing legacy SQLite users/accounts into PostgreSQL.
