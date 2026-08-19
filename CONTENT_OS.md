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
               -> tenant-scoped persistence
               -> ContentApplication / PublishingApplication
               -> PlatformRegistry
                    -> Telegram / VK / Instagram / TikTok adapters

Scheduled publications
  -> autoposter-worker
       -> PostgreSQL SKIP LOCKED claim (production)
       -> SQLite atomic claim (development)
       -> durable pre-network publishing checkpoint
       -> same PublishingApplication

Asynchronous provider outcomes
  -> provider_tracking_id
       -> autoposter-reconciler
       -> provider status fetch
       -> processing | published(final external_post_id) | provider failed
```

Operations / recovery
  -> workspace health overview
  -> reconciliation queue
  -> admin manual resolution with immutable operations event
  -> retry allowed only when no durable remote identity exists

Health uses provider-processing start time rather than the last status poll, so repeated polling cannot hide a stuck remote publication.

The browser never receives platform credentials or the workspace API key.

## Workspace security

Every FastAPI `/api/v1/*` request requires a Bearer key by default. Keys are mapped to exactly one workspace:

```env
AUTOPOSTER_REQUIRE_API_AUTH=1
AUTOPOSTER_API_KEYS_JSON={"replace-with-a-long-random-key":1}
```

A client cannot choose `workspace_id` in request JSON. Content, variants, publications and social accounts are scoped by the workspace resolved from the credential.

For local-only development you can explicitly disable auth:

```env
AUTOPOSTER_REQUIRE_API_AUTH=0
AUTOPOSTER_DEV_WORKSPACE_ID=1
```

Do not use development mode on an Internet-facing server.

## Encrypted social credentials

Production PostgreSQL mode requires encrypted credentials. Configure one or more Fernet keys, newest first:

```env
AUTOPOSTER_CREDENTIAL_KEYS=<newest-key>,<previous-key>
```

Generate a key in Python:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Public account configuration remains in `accounts.options_json`. Secret fields such as access/refresh tokens, bot tokens, API keys, client secrets and passwords are stored only in `account_credentials.encrypted_payload`.

To move existing SQLite account secrets out of plaintext storage:

```bash
autoposter-workspace encrypt-credentials
```

To rotate encryption keys:

1. Put the new key first and keep the old key after it in `AUTOPOSTER_CREDENTIAL_KEYS`.
2. Run:

```bash
autoposter-workspace rotate-credentials
```

3. Verify API/worker access to connected accounts.
4. Remove the retired key from the environment.

## SQLite development mode

Without `AUTOPOSTER_DATABASE_URL`, API and worker use the existing SQLite database.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
autoposter-api
```

Use `AUTOPOSTER_REQUIRE_CREDENTIAL_ENCRYPTION=1` if you want local SQLite to refuse startup without credential keys.

## PostgreSQL production mode

Set a PostgreSQL connection URL and credential key ring:

```env
AUTOPOSTER_DATABASE_URL=postgresql://autoposter:password@postgres:5432/autoposter
AUTOPOSTER_DB_POOL_SIZE=10
AUTOPOSTER_CREDENTIAL_KEYS=<fernet-key>
```

Then both commands use PostgreSQL automatically:

```bash
autoposter-api
autoposter-worker
autoposter-reconciler
```

The PostgreSQL worker claims due publications with row locking and `SKIP LOCKED`, so multiple worker processes can consume different scheduled publications concurrently.

## Import the existing bot database into PostgreSQL

The import preserves existing user/account ids so connected social accounts do not need to be recreated. Secrets are split from public account options and encrypted before they are inserted into PostgreSQL.

```bash
AUTOPOSTER_DATABASE_URL=postgresql://... \
AUTOPOSTER_CREDENTIAL_KEYS=<fernet-key> \
autoposter-workspace import-legacy
```

Custom SQLite path:

```bash
autoposter-workspace import-legacy --sqlite-path /path/to/autoposter.sqlite3
```

The command currently imports users, social accounts, workspaces and explicit workspace-account links. Existing Content OS content can stay in SQLite until the final cutover or be regenerated during development.

## Workspace management

```bash
autoposter-workspace list
autoposter-workspace create --name "My workspace" --owner-user-id 1
autoposter-workspace link-account --workspace-id 1 --account-id 7
```

Accounts owned by the workspace owner are visible automatically. `link-account` is for explicitly sharing an additional social account into a workspace.

## Runtime settings

```env
AUTOPOSTER_API_HOST=0.0.0.0
AUTOPOSTER_API_PORT=8000
AUTOPOSTER_API_RELOAD=0
AUTOPOSTER_WORKER_INTERVAL_SECONDS=5
AUTOPOSTER_WORKER_BATCH_SIZE=25
AUTOPOSTER_CORS_ORIGINS=https://autoposter.example.com
```

API: `http://localhost:8000`
OpenAPI: `http://localhost:8000/docs`

## Web

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
- Capability-driven platform-native composer fields.
- Telegram formatting/silent options, VK author options, Instagram Reel options and TikTok privacy/interactions.
- Independent publications per social account.
- Publication attempts and retry metadata.
- FastAPI content/variant/account/publication endpoints.
- Workspace-bound API authentication and SQL tenant isolation.
- Server-side Next.js API proxy so workspace keys stay out of browser JavaScript.
- Functional Next.js composer and publication calendar.
- Dry-run, publish-now, scheduling and failed-publication retry flows.
- SQLite development persistence.
- PostgreSQL production persistence and connection pooling.
- PostgreSQL `SKIP LOCKED` scheduled publication queue.
- Encrypted social credentials with key rotation.
- Legacy SQLite -> PostgreSQL user/account migration.
- CI for Python, PostgreSQL integration tests and Next.js production build.

## Remaining blockers before public SaaS exposure

1. End-user login/session management and workspace membership roles. The current API-key boundary is suitable for internal/server-to-server deployment, not the final consumer login UX.
2. Object storage and media preprocessing service.
3. Native OAuth callback flows for each platform.
4. Platform-native structured external post IDs/URLs and stronger idempotency policies.
5. Rate-limit-aware retries.
6. Analytics ingestion and recommendation layer.
7. Full migration of historical Content OS data if production history must be preserved.


## Async publication reliability

`provider_tracking_id` and final `external_post_id` are intentionally separate identities. Async providers such as TikTok may accept a publish job before the final public post exists. The worker persists the tracking handle as soon as the provider init response is received, before the upload completes.

Once a tracking handle exists, generic publish retries are disabled. A later transport failure remains a reconciliation concern instead of creating a second remote publication. `processing` is therefore a first-class lifecycle state, not a synonym for `published`.

Operational health is available at `/api/v1/operations/overview` for `admin+` workspace roles and in the web Operations page.


## Health alert worker (0.11)

`autoposter-health` scans workspace operational health independently from the API and publish workers.
The default production threshold is `critical`; incident fingerprints include severity + reason codes but intentionally exclude changing counters, preventing one alert per poll.

Delivery rules:

- active `admin`/`owner` Telegram identities only;
- new fingerprint -> immediate notification;
- same incident -> reminder only after `AUTOPOSTER_HEALTH_ALERT_REMINDER_SECONDS`;
- recovery below the configured threshold -> one resolved message when the incident was previously delivered;
- failed/no-recipient delivery is persisted and shown in Operations; it is not marked notified, so a later scan can deliver it.

The alert lifecycle is stored in `operations_alert_state`, while opened/notified/resolved transitions are appended to immutable `operations_events`.
