# Autoposter Content Distribution OS

This branch moves Autoposter from a Telegram-first bot to a web/API-first content distribution platform.

## Product model

```text
ContentItem (master idea)
  -> PlatformVariant (Telegram / VK / Instagram / TikTok specific version)
    -> Publication (one account, one schedule, one independent lifecycle)
      -> PublicationAttempt (retry/idempotency/audit)
```

Telegram remains a mobile control surface. Product logic belongs to the shared application layer used by web, API and workers.

## Current architecture

```text
Browser
  -> Next.js BFF (/api/autoposter/*)
       -> server-only workspace API key
          -> FastAPI
               -> AuthContext(workspace_id)
               -> WorkspaceContentStore
               -> ContentApplication / PublishingApplication
               -> PlatformRegistry
                    -> Telegram / VK / Instagram / TikTok adapters

Scheduled publications
  -> autoposter-worker
       -> global queue claim
       -> same PublishingApplication
```

The browser never receives platform credentials or the workspace API key.

## Workspace security

Every FastAPI `/api/v1/*` request requires a Bearer key by default. Keys are mapped to exactly one workspace:

```env
AUTOPOSTER_REQUIRE_API_AUTH=1
AUTOPOSTER_API_KEYS_JSON={"replace-with-a-long-random-key":1}
```

A client cannot choose `workspace_id` in request JSON. Content, variants, publications and social accounts are scoped in SQL by the workspace resolved from the credential.

For local-only development you can explicitly disable auth:

```env
AUTOPOSTER_REQUIRE_API_AUTH=0
AUTOPOSTER_DEV_WORKSPACE_ID=1
```

Do not use development mode on an Internet-facing server.

## Bootstrap a workspace

Workspaces currently reuse existing bot users and social accounts during staged migration.

```bash
autoposter-workspace list
autoposter-workspace create --name "My workspace" --owner-user-id 1
autoposter-workspace link-account --workspace-id 1 --account-id 7
```

Accounts owned by the workspace owner are visible automatically. `link-account` is for explicitly sharing an additional legacy social account into a workspace.

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
AUTOPOSTER_CORS_ORIGINS=http://localhost:3000
```

### Web

The web app uses a server-side BFF. The API key must never use a `NEXT_PUBLIC_` prefix.

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

`web/.env.local`:

```env
AUTOPOSTER_API_URL=http://127.0.0.1:8000
AUTOPOSTER_API_KEY=replace-with-the-key-mapped-to-your-workspace
```

Web: `http://localhost:3000`

## Implemented in this branch

- Master content and platform variants.
- Automatic master synchronization until a variant is manually overridden.
- Per-platform capabilities registry.
- Independent publications per social account.
- Publication attempts and retry metadata.
- FastAPI content/variant/account/publication endpoints.
- Workspace-bound API authentication.
- SQL tenant isolation for content, variants, publications and social accounts.
- Server-side Next.js API proxy so workspace keys stay out of browser JavaScript.
- Functional Next.js composer.
- Dry-run and publish-now flow.
- Scheduled publication worker with atomic SQLite claiming.
- Stale queued-publication recovery.
- CI for Python tests/compile and Next.js typecheck/build.

## Migration strategy

The existing `users`, `accounts`, billing and legacy jobs remain authoritative during staged migration. Content OS tables are additive. `workspace_accounts` provides an explicit bridge for sharing existing social accounts into the new workspace model without moving credentials into frontend storage.

SQLite remains a development bridge. The next persistence milestone is PostgreSQL with transaction-safe row locking / `SKIP LOCKED` queue claiming.

## Remaining blockers before public SaaS exposure

1. End-user login/session management and workspace membership roles (the current API-key boundary is suitable for internal/server-to-server deployment).
2. PostgreSQL persistence.
3. Encrypted credential storage / secrets boundary.
4. Object storage and media preprocessing service.
5. Native OAuth callback flows for each platform.
6. Platform-native adapter validation and structured external post IDs.
7. Rate-limit-aware retries and stronger idempotency policies.
8. Calendar/publication management and analytics screens.
