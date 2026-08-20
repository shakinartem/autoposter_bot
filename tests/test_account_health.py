from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoposter_bot.application.account_health import AccountHealthApplication
from autoposter_bot.infrastructure.account_health_store import AccountHealthStore
from autoposter_bot.infrastructure.operations_store import OperationsStore


class SQLiteHarness:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class FakeScoped:
    def __init__(self, accounts: list[dict]) -> None:
        self.accounts = {int(item["id"]): dict(item) for item in accounts}
        self.updated: list[tuple[int, dict]] = []

    def list_accounts(self):
        return [dict(item) for item in self.accounts.values()]

    def get_account(self, account_id: int):
        item = self.accounts.get(account_id)
        return dict(item) if item else None

    def update_account(self, account_id: int, *, options: dict):
        self.updated.append((account_id, dict(options)))
        self.accounts[account_id]["options"] = dict(options)
        return dict(self.accounts[account_id])


class FakeRefresh:
    def __init__(self, replacement: dict | None = None, error: Exception | None = None) -> None:
        self.replacement = replacement
        self.error = error

    def refresh_if_needed(self, platform: str, options: dict):
        if self.error:
            raise self.error
        if self.replacement is not None:
            return dict(self.replacement), True
        return dict(options), False


class Provider:
    USER_INFO_URL = "https://provider.test/me"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self):
        return self._payload


def _harness(tmp_path: Path):
    harness = SQLiteHarness(tmp_path / "account-health.sqlite3")
    with harness.connect() as db:
        db.executescript(
            """
            CREATE TABLE users(id INTEGER PRIMARY KEY, telegram_user_id INTEGER);
            CREATE TABLE workspaces(id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_user_id INTEGER NOT NULL);
            CREATE TABLE accounts(
                id INTEGER PRIMARY KEY, owner_user_id INTEGER, name TEXT, platform TEXT,
                destination TEXT, options_json TEXT, created_at TEXT
            );
            CREATE TABLE workspace_accounts(
                workspace_id INTEGER, account_id INTEGER, created_at TEXT,
                PRIMARY KEY(workspace_id, account_id)
            );
            CREATE TABLE publications_v2(
                id TEXT PRIMARY KEY, variant_id TEXT, platform TEXT, account_id INTEGER,
                destination TEXT, status TEXT, scheduled_at TEXT, next_attempt_at TEXT,
                provider_tracking_id TEXT, external_post_id TEXT, external_url TEXT,
                published_at TEXT, attempt_count INTEGER DEFAULT 0, last_error_code TEXT,
                last_error_message TEXT, metadata_json TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT
            );
            CREATE TABLE content_items(id TEXT PRIMARY KEY, workspace_id INTEGER, title TEXT);
            CREATE TABLE platform_variants(id TEXT PRIMARY KEY, content_id TEXT);
            CREATE TABLE publication_attempts(id TEXT PRIMARY KEY, publication_id TEXT, status TEXT, started_at TEXT);
            CREATE TABLE analytics_snapshots(id TEXT PRIMARY KEY, publication_id TEXT, captured_at TEXT);
            INSERT INTO users(id, telegram_user_id) VALUES (1, 101), (2, 202);
            INSERT INTO workspaces(id, name, owner_user_id) VALUES (1, 'One', 1), (2, 'Two', 2);
            INSERT INTO accounts(id, owner_user_id, name, platform, destination, options_json, created_at)
              VALUES (10, 1, 'TikTok A', 'tiktok', '@a', '{}', '2026-01-01T00:00:00'),
                     (20, 2, 'TikTok B', 'tiktok', '@b', '{}', '2026-01-01T00:00:00');
            """
        )
    store = AccountHealthStore(backend="sqlite", connect=harness.connect)
    store.init_schema()
    return harness, store


def _app(store: AccountHealthStore, scoped: FakeScoped, response: FakeResponse, refresh=None, telegram_token="tg-token"):
    return AccountHealthApplication(
        account_health=store,
        store_for_workspace=lambda _workspace_id: scoped,
        credential_refresh=refresh or FakeRefresh(),
        tiktok=Provider(),
        instagram=Provider(),
        telegram_bot_token=telegram_token,
        http_get=lambda *args, **kwargs: response,
    )


def test_tiktok_remote_failure_does_not_fall_back_to_stored_open_id(tmp_path: Path):
    _, store = _harness(tmp_path)
    scoped = FakeScoped([
        {"id": 10, "name": "TikTok A", "platform": "tiktok", "destination": "@a", "options": {"access_token": "bad", "open_id": "cached"}},
    ])
    app = _app(store, scoped, FakeResponse(401, {"error": {"code": "access_token_invalid"}}))

    result = app.probe_account(1, 10)

    assert result["status"] == "critical"
    assert result["code"] == "credential_rejected"
    assert result["reconnect_required"] is True
    assert result["identity"] == {}


def test_transient_remote_failure_is_degraded_not_reconnect(tmp_path: Path):
    _, store = _harness(tmp_path)
    scoped = FakeScoped([
        {"id": 10, "name": "TikTok A", "platform": "tiktok", "destination": "@a", "options": {"access_token": "token"}},
    ])
    app = _app(store, scoped, FakeResponse(503, {"error": {"code": "internal_error"}}))

    result = app.probe_account(1, 10)

    assert result["status"] == "degraded"
    assert result["code"] == "provider_temporarily_unavailable"
    assert result["reconnect_required"] is False


def test_refresh_is_persisted_before_remote_probe(tmp_path: Path):
    _, store = _harness(tmp_path)
    expires = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    scoped = FakeScoped([
        {"id": 10, "name": "TikTok A", "platform": "tiktok", "destination": "@a", "options": {"access_token": "old"}},
    ])
    refresh = FakeRefresh({"access_token": "new", "refresh_token": "r", "token_expires_at": expires})
    response = FakeResponse(200, {"data": {"user": {"open_id": "open-1", "display_name": "Creator"}}, "error": {"code": "ok"}})
    app = _app(store, scoped, response, refresh=refresh)

    result = app.probe_account(1, 10)

    assert scoped.updated and scoped.updated[0][1]["access_token"] == "new"
    assert result["status"] == "healthy"
    assert result["identity"]["external_id"] == "open-1"
    assert result["token_expires_at"] is not None


def test_vk_is_explicitly_unverified_instead_of_false_healthy(tmp_path: Path):
    _, store = _harness(tmp_path)
    scoped = FakeScoped([
        {"id": 10, "name": "VK", "platform": "vk", "destination": "-1", "options": {"access_token": "vk-token"}},
    ])
    app = _app(store, scoped, FakeResponse(200, {}))

    result = app.probe_account(1, 10)

    assert result["status"] == "degraded"
    assert result["code"] == "probe_not_verified"


def test_telegram_getme_is_cached_for_multiple_workspace_accounts(tmp_path: Path):
    _, store = _harness(tmp_path)
    with store.connect() as db:
        db.execute("INSERT INTO accounts(id, owner_user_id, name, platform, destination, options_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (11, 1, 'TG2', 'telegram', '-1002', '{}', '2026-01-01T00:00:00'))
    scoped = FakeScoped([
        {"id": 10, "name": "TG1", "platform": "telegram", "destination": "-1001", "options": {}},
        {"id": 11, "name": "TG2", "platform": "telegram", "destination": "-1002", "options": {}},
    ])
    calls = []
    response = FakeResponse(200, {"ok": True, "result": {"id": 999, "username": "poster_bot", "first_name": "Poster"}})
    app = AccountHealthApplication(
        account_health=store,
        store_for_workspace=lambda _workspace_id: scoped,
        credential_refresh=FakeRefresh(),
        tiktok=Provider(),
        instagram=Provider(),
        telegram_bot_token="tg-token",
        http_get=lambda *args, **kwargs: (calls.append(args[0]) or response),
    )

    results = app.probe_workspace(1)

    assert [item["status"] for item in results] == ["healthy", "healthy"]
    assert len(calls) == 1


def test_account_health_is_workspace_scoped_and_operations_aggregates_reason(tmp_path: Path):
    harness, store = _harness(tmp_path)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    store.upsert(
        workspace_id=1,
        account_id=10,
        platform="tiktok",
        status="critical",
        code="credential_rejected",
        message="Reconnect",
        probe_method="tiktok.user.info",
        reconnect_required=True,
        checked_at=now,
    )
    store.upsert(
        workspace_id=2,
        account_id=20,
        platform="tiktok",
        status="healthy",
        code="remote_identity_verified",
        message="OK",
        probe_method="tiktok.user.info",
        checked_at=now,
    )

    assert [item["account_id"] for item in store.list_workspace(workspace_id=1)] == [10]
    assert [item["account_id"] for item in store.list_workspace(workspace_id=2)] == [20]

    operations = OperationsStore(backend="sqlite", connect=harness.connect)
    operations.init_schema()
    overview = operations.workspace_overview(workspace_id=1, now=now)
    assert overview["health"] == "critical"
    assert overview["social_connections"]["critical"] == 1
    assert overview["social_connections"]["reconnect_required"] == 1
    reason = next(item for item in overview["health_reasons"] if item["code"] == "social_connection_health")
    assert reason["severity"] == "critical"
