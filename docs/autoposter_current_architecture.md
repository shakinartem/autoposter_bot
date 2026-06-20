# Autoposter Bot — Current Architecture Audit

## 1. Project Structure

```
autoposter_bot/
├── __init__.py              # Package marker
├── config.py                # Settings dataclass, .env loader
├── models.py                # Data models: MediaItem, Target, PostJob, OAuthConnection
├── db.py                    # SQLite Database class (schema, CRUD, migrations)
├── service.py               # AutoposterService — orchestrates publishers, OAuth hydration
├── scheduler.py             # process_queue (file-based), process_due_db_jobs, run_polling_loop
├── admin_bot.py             # Telegram bot (long-polling) — menus, post creation, accounts, billing
├── cli.py                   # CLI entry point (argparse)
├── cloudinary_client.py     # Cloudinary media upload wrapper
├── env_store.py             # Save env values back to .env file
├── loader.py                # Load PostJob from JSON file (legacy queue)
├── notifier.py              # Telegram notification helper
├── spgutils_client.py       # API client for SPGUtils Worker (OAuth, tokens)
├── token_health.py          # VK and Instagram token expiration checks
└── publishers/
    ├── __init__.py           # Exports all publishers
    ├── base.py               # Publisher ABC + PublishResult dataclass
    ├── telegram.py           # TelegramPublisher
    ├── vk.py                 # VkPublisher (images + video)
    ├── instagram.py           # InstagramPublisher (feed, carousel, reels, stories)
    └── tiktok.py             # TikTokPublisher (direct_post + upload_draft, chunked upload)

tests/
├── test_database_product.py    # Unit tests for DB (users, notifications, publish_events)
├── test_instagram_publisher.py # Integration tests (requires credentials)
├── test_oauth_worker_flow.py   # OAuth flow tests
├── test_spgutils_integration.py# SPGUtils integration tests
└── test_tiktok_publisher.py    # TikTok publisher tests

queue/                         # Legacy JSON-based queue directory
examples/post.example.json     # Example JSON job file
.env.example                   # Template for environment variables
```

## 2. Current Publication Flow

### 2.1 Post creation (Telegram admin bot)
1. User selects platform and account via inline keyboards (`post|platform|x`, `post|account|y`)
2. User selects content type (`post|type|video`, `post|type|photo`, `post|type|text`)
3. Media is downloaded from Telegram to `telegram_uploads/` via `getFile`
4. If Instagram, media is uploaded to Cloudinary at creation time (`_media_options_for_platform`)
5. Draft is finalized (`_finalize_post_draft`) → `_publish_draft_now` or `_schedule_draft`

### 2.2 Immediate publication path
- `_publish_draft_now` → `PostJob` → `AutoposterService.publish_job()` → per-target publisher dispatch
- Result logged to `publish_events`

### 2.3 Scheduled publication path
- `_schedule_draft` → `db.create_job()` with `status="pending"` and `scheduled_at`
- Polling loop: `scheduler.run_polling_loop()` → `process_due_db_jobs()` → `db.get_due_jobs()` → `service.publish_job()`
- Status updated: `pending` → `published` / `processing` / `failed`

### 2.4 Legacy file queue
- `loader.load_job()` reads JSON files from `queue/`
- `scheduler.process_queue()` — moves processed files to `queue/processed/`
- Still supported but SQLite scheduling is primary path

## 3. Current Database Schema (SQLite)

Key tables for publication:
- **`jobs`** — `id, owner_user_id, external_post_id, content_type, text, scheduled_at, status, metadata_json`
  - Statuses used: `pending`, `published`, `processing`, `failed`
- **`job_media`** — `id, job_id, source, media_type, order_index, options_json`
- **`job_targets`** — `id, job_id, account_id`
- **`accounts`** — `id, owner_user_id, name, platform, destination, options_json`
- **`oauth_connections`** — OAuth state, tokens, scopes (synced from SPGUtils Worker)
- **`publish_events`** — audit log: `id, user_id, job_id, external_post_id, platform, destination, status, detail`

Other tables: `users`, `plans`, `subscriptions`, `credit_ledger`, `user_entitlements`, `referral_codes`, `referral_events`, `notifications`, `payments`.

## 4. OAuth Flow

1. User initiates OAuth via Telegram bot → `service.start_oauth_link()` → SPGUtils Worker
2. Worker returns a URL; user follows it in browser
3. Worker handles OAuth callback, stores tokens in Cloudflare KV + D1
4. Worker exposes `GET /api/connections/list`, `GET /api/connections/token`
5. Bot syncs connections via `service.sync_oauth_connections_for_user()` → `db.sync_oauth_connections()`
6. At publish time, `service._hydrate_publish_options()` fetches fresh tokens from Worker

Supports: TikTok, Instagram (Meta), VK (legacy direct token).

## 5. Cloudinary Integration

- `CloudinaryClient` wraps `cloudinary.uploader.upload`
- Used for:
  - Instagram media: local files → cloudinary URL → Instagram Graph API
  - TikTok fallback: if local upload fails and `allow_cloudinary_fallback` is set
- Not used for: VK (uploads directly), Telegram (uploads via Bot API)

## 6. Current Weak Points

1. **No `media_type = "video"` in `job_media` for video posts** — video posts are created but stored with mixed media types
2. **Job statuses are minimal** — only `pending`, `published`, `processing`, `failed` — no `draft`, `queued`, `cancelled`
3. **No retry logic for DB jobs** — `process_due_db_jobs` does not retry failed jobs
4. **No video metadata storage** — duration, resolution, aspect ratio, codec info not stored
5. **No platform-specific validation** — publisher level checks exist but no centralized validation
6. **Publisher interface is minimal** — `Publisher` ABC has only `publish()`, no `validate()`, no `validate_media()`
7. **No `publication_attempts` table** — failures are only in `publish_events` without structured retry tracking
8. **No dedicated `MediaAsset` entity** — media is embedded in `job_media` without independent lifecycle
9. **`admin_bot.py` is very large** (~4700 lines) — mixing UI, post creation, billing, OAuth, database admin
10. **TikTok publisher is tightly coupled** — knows about Cloudinary, SPGUtils, chunked upload logic
11. **Telegram video download** — large videos may hit timeouts; no streaming, no chunked download
12. **No integration tests running without secrets** — only `test_database_product.py` runs in isolation

## 7. What Must NOT Be Broken

- Telegram admin bot functionality (all menus, commands, post creation)
- OAuth link & sync flow (SPGUtils Worker integration)
- Existing publisher adapters (Telegram, VK, Instagram, TikTok)
- Database schema and existing migrations
- Cloudinary upload for Instagram media
- Legacy file queue compatibility
- Environment variable loading and `.env` management
- Token health checks and warnings
- Subscription / billing / YooKassa integration