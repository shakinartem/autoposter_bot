CREATE TABLE IF NOT EXISTS content_factory_receipts (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    source_content_id TEXT NOT NULL,
    source_project_id TEXT NOT NULL,
    local_content_id TEXT NOT NULL,
    response_json TEXT NOT NULL DEFAULT '{}',
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_factory_receipts_source ON content_factory_receipts(source_content_id, received_at);
CREATE TABLE IF NOT EXISTS content_factory_links (
    source_content_id TEXT PRIMARY KEY,
    source_project_id TEXT NOT NULL,
    local_content_id TEXT NOT NULL UNIQUE,
    latest_payload_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
