-- PostgreSQL production schema for Autoposter Content OS.
-- The legacy SQLite database remains supported for staged migration.

CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE,
    username TEXT,
    full_name TEXT,
    phone_number TEXT,
    role TEXT NOT NULL DEFAULT 'user',
    is_registered BOOLEAN NOT NULL DEFAULT FALSE,
    registered_at TIMESTAMP,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspaces (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    owner_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    destination TEXT,
    options_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_accounts_owner_platform ON accounts(owner_user_id, platform);

CREATE TABLE IF NOT EXISTS workspace_accounts (
    workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(workspace_id, account_id)
);

CREATE TABLE IF NOT EXISTS content_items (
    id TEXT PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    cta TEXT NOT NULL DEFAULT '',
    links_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    hashtags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_content_items_workspace_updated
    ON content_items(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS media_assets (
    id TEXT PRIMARY KEY,
    workspace_id BIGINT REFERENCES workspaces(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    media_type TEXT NOT NULL,
    alt_text TEXT NOT NULL DEFAULT '',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS content_media (
    content_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    order_index INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(content_id, media_id)
);

CREATE TABLE IF NOT EXISTS platform_variants (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    fields_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    sync_with_master BOOLEAN NOT NULL DEFAULT TRUE,
    revision INTEGER NOT NULL DEFAULT 1,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE(content_id, platform)
);

CREATE TABLE IF NOT EXISTS variant_media (
    variant_id TEXT NOT NULL REFERENCES platform_variants(id) ON DELETE CASCADE,
    media_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    order_index INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(variant_id, media_id)
);

CREATE TABLE IF NOT EXISTS publications_v2 (
    id TEXT PRIMARY KEY,
    variant_id TEXT NOT NULL REFERENCES platform_variants(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    destination TEXT,
    scheduled_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'draft',
    external_post_id TEXT,
    external_url TEXT,
    published_at TIMESTAMP,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    last_error_message TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_publications_v2_due
    ON publications_v2(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_publications_v2_account
    ON publications_v2(account_id, status);

CREATE TABLE IF NOT EXISTS publication_attempts (
    id BIGSERIAL PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publications_v2(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    request_fingerprint TEXT,
    external_post_id TEXT,
    error_code TEXT,
    error_message TEXT,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    raw_response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    UNIQUE(publication_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id BIGSERIAL PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publications_v2(id) ON DELETE CASCADE,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    captured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
