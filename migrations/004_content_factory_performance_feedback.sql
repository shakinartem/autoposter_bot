-- Durable Autoposter -> Content Factory performance feedback.

ALTER TABLE analytics_snapshots ADD COLUMN event_id TEXT;
ALTER TABLE analytics_snapshots ADD COLUMN platform TEXT;
ALTER TABLE analytics_snapshots ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';

CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_snapshots_event_id
    ON analytics_snapshots(event_id)
    WHERE event_id IS NOT NULL;

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
    FOREIGN KEY(analytics_snapshot_id) REFERENCES analytics_snapshots(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_content_factory_feedback_pending
    ON content_factory_feedback_outbox(status, available_at);
