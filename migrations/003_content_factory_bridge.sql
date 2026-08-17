CREATE TABLE IF NOT EXISTS content_factory_project_links (
    source_project_id TEXT PRIMARY KEY,
    workspace_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS content_factory_receipts (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    source_content_id TEXT NOT NULL,
    source_project_id TEXT NOT NULL,
    local_content_id TEXT NOT NULL,
    workspace_id INTEGER NOT NULL,
    response_json TEXT NOT NULL DEFAULT '{}',
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_content_factory_receipts_source
    ON content_factory_receipts(source_content_id, received_at);

CREATE TABLE IF NOT EXISTS content_factory_links (
    source_content_id TEXT PRIMARY KEY,
    source_project_id TEXT NOT NULL,
    local_content_id TEXT NOT NULL UNIQUE,
    workspace_id INTEGER NOT NULL,
    latest_payload_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS content_factory_media_links (
    asset_id TEXT NOT NULL,
    workspace_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    media_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY(asset_id, workspace_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);
