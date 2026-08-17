-- Workspace isolation for the web/API layer.
-- Existing account ownership remains intact; this table allows explicit sharing
-- of a social account into a workspace without exposing credentials to clients.

CREATE TABLE IF NOT EXISTS workspace_accounts (
    workspace_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, account_id),
    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_content_items_workspace_updated
    ON content_items(workspace_id, updated_at);

CREATE INDEX IF NOT EXISTS idx_media_assets_workspace
    ON media_assets(workspace_id);

CREATE INDEX IF NOT EXISTS idx_workspace_accounts_account
    ON workspace_accounts(account_id, workspace_id);
