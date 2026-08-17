# Autoposter Content Distribution OS

This branch moves Autoposter from a Telegram-first bot to a web/API-first content distribution platform.

## Product model

```text
ContentItem (master idea)
  -> PlatformVariant (Telegram / VK / Instagram / TikTok specific version)
    -> Publication (one account, one schedule, one independent lifecycle)
      -> PublicationAttempt (retry/idempotency/audit)
```

Telegram bot is retained as a mobile control surface. It must call the same application layer as the web app and worker rather than owning product logic.

## Current architecture

```text
web/ (Next.js)
        |
        v
FastAPI: autoposter_bot.apps.api
        |
        +-- ContentApplication
        +-- PublishingApplication
        |
        +-- SQLiteContentStore (development bridge)
        +-- PlatformRegistry
        |     +-- Telegram legacy adapter
        |     +-- VK legacy adapter
        |     +-- Instagram legacy adapter
        |     +-- TikTok legacy adapter
        |
        v
autoposter-worker
```

## Implemented in this branch

- Master content and platform variants.
- Automatic master synchronization until a variant is manually overridden.
- Per-platform capabilities registry.
- Independent publications per social account.
- Publication attempts and retry metadata.
- FastAPI endpoints for content, variants, accounts and publications.
- Functional Next.js composer.
- Dry-run and publish-now flow.
- Scheduled publication worker with atomic SQLite claiming.
- Stale queued-publication recovery.
- CI for Python tests/compile and Next.js typecheck/build.

## Run locally

### Backend

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
autoposter-api
```

API: `http://localhost:8000`
OpenAPI: `http://localhost:8000/docs`

### Worker

In a second terminal:

```bash
autoposter-worker
```

Optional environment variables:

```env
AUTOPOSTER_API_HOST=0.0.0.0
AUTOPOSTER_API_PORT=8000
AUTOPOSTER_API_RELOAD=1
AUTOPOSTER_WORKER_INTERVAL_SECONDS=5
AUTOPOSTER_WORKER_BATCH_SIZE=25
```

### Web

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Web: `http://localhost:3000`

## Migration strategy

The existing `users`, `accounts`, billing and legacy jobs remain untouched during the migration. Content OS tables are added by `migrations/002_content_os.sql` and social account credentials are read through a compatibility bridge.

SQLite is intentionally retained only for this staged development phase. Before public multi-user deployment, persistence should move to PostgreSQL and the publication claim implementation should use row-level locks / `SKIP LOCKED` semantics.

## Deployment blockers before public exposure

1. Web authentication and workspace authorization.
2. PostgreSQL migration.
3. Encrypted credential storage / secrets boundary.
4. Object storage and media preprocessing service.
5. OAuth callback flows for each platform.
6. Platform-native adapter validation and structured external post IDs.
7. Rate-limit-aware retries and stronger idempotency policies.

Do not expose the current API directly to the public Internet until authentication and workspace authorization are implemented.
