CREATE TABLE IF NOT EXISTS content_factory_analytics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    platform TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(publication_id) REFERENCES publications_v2(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_content_factory_analytics_publication
    ON content_factory_analytics_snapshots(publication_id, captured_at);

CREATE TABLE IF NOT EXISTS content_factory_feedback_outbox (
    id TEXT PRIMARY KEY,
    analytics_snapshot_id INTEGER NOT NULL UNIQUE,
    event_id TEXT NOT NULL UNIQUE,
    source_content_id TEXT NOT NULL,
    external_publication_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    sent_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(analytics_snapshot_id) REFERENCES content_factory_analytics_snapshots(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_content_factory_feedback_pending
    ON content_factory_feedback_outbox(status, available_at);
