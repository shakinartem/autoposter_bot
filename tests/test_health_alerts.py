from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoposter_bot.application.health_alerts import HealthAlertApplication
from autoposter_bot.infrastructure.health_alert_store import HealthAlertStore


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


class FakeOperations:
    def __init__(self, overview: dict) -> None:
        self.overview = overview

    def workspace_overview(self, *, workspace_id: int, now=None):
        return dict(self.overview)


class FakeAuth:
    def __init__(self, members: list[dict]) -> None:
        self.members = members

    def list_members(self, workspace_id: int):
        return list(self.members)


class FakeNotifier:
    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.messages: list[tuple[int | str, str]] = []

    def is_bot_configured(self) -> bool:
        return self.configured

    def send_to(self, chat_id: int | str, text: str) -> None:
        self.messages.append((chat_id, text))


def _critical(*, unknown: int = 1, queue_lag: int = 0) -> dict:
    reasons = []
    if unknown:
        reasons.append({"code": "unknown_publish_outcome", "severity": "critical", "count": unknown})
    if queue_lag:
        reasons.append({"code": "queue_lag", "severity": "critical", "value": queue_lag})
    return {
        "health": "critical",
        "health_reasons": reasons,
        "queue": {"due": 0, "lag_seconds": queue_lag, "retry_scheduled": 0},
        "reconciliation": {"unknown_outcomes": unknown, "processing": 0, "stale_processing": 0},
    }


def _healthy() -> dict:
    return {"health": "healthy", "health_reasons": []}


def _store(tmp_path: Path) -> HealthAlertStore:
    harness = SQLiteHarness(tmp_path / "alerts.sqlite3")
    with harness.connect() as db:
        db.executescript(
            """
            CREATE TABLE users(id INTEGER PRIMARY KEY, telegram_user_id INTEGER, is_active INTEGER DEFAULT 1);
            CREATE TABLE workspaces(id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner_user_id INTEGER NOT NULL);
            CREATE TABLE workspace_members(workspace_id INTEGER, user_id INTEGER, role TEXT, created_at TEXT, PRIMARY KEY(workspace_id, user_id));
            CREATE TABLE operations_events(
                id TEXT PRIMARY KEY,
                workspace_id INTEGER NOT NULL,
                actor_user_id INTEGER,
                event_type TEXT NOT NULL,
                publication_id TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            INSERT INTO users(id, telegram_user_id) VALUES (1, 101);
            INSERT INTO workspaces(id, name, owner_user_id) VALUES (1, 'Studio', 1);
            INSERT INTO workspace_members(workspace_id, user_id, role, created_at) VALUES (1, 1, 'owner', '2026-01-01T00:00:00');
            """
        )
    store = HealthAlertStore(backend="sqlite", connect=harness.connect)
    store.init_schema()
    return store


def test_alert_fingerprint_ignores_changing_counts_and_respects_cooldown(tmp_path: Path):
    store = _store(tmp_path)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    first = store.evaluate(
        workspace_id=1,
        workspace_name="Studio",
        overview=_critical(unknown=1),
        now=now,
        reminder_seconds=3600,
    )
    assert first.action == "notify"
    store.mark_notified(workspace_id=1, recipient_count=1, now=now)

    changed_count = store.evaluate(
        workspace_id=1,
        workspace_name="Studio",
        overview=_critical(unknown=9),
        now=now + timedelta(minutes=5),
        reminder_seconds=3600,
    )
    assert changed_count.action == "none"
    assert changed_count.fingerprint == first.fingerprint

    reminder = store.evaluate(
        workspace_id=1,
        workspace_name="Studio",
        overview=_critical(unknown=12),
        now=now + timedelta(hours=2),
        reminder_seconds=3600,
    )
    assert reminder.action == "notify"
    assert reminder.previously_notified is True


def test_changed_reason_opens_new_incident_and_healthy_resolves(tmp_path: Path):
    store = _store(tmp_path)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    first = store.evaluate(
        workspace_id=1,
        workspace_name="Studio",
        overview=_critical(unknown=1),
        now=now,
    )
    store.mark_notified(workspace_id=1, recipient_count=1, now=now)

    changed = store.evaluate(
        workspace_id=1,
        workspace_name="Studio",
        overview=_critical(unknown=1, queue_lag=500),
        now=now + timedelta(minutes=2),
    )
    assert changed.action == "notify"
    assert changed.fingerprint != first.fingerprint

    store.mark_notified(workspace_id=1, recipient_count=1, now=now + timedelta(minutes=2))
    resolved = store.evaluate(
        workspace_id=1,
        workspace_name="Studio",
        overview=_healthy(),
        now=now + timedelta(minutes=3),
    )
    assert resolved.action == "resolve"
    assert resolved.previously_notified is True
    state = store.get_state(1)
    assert state is not None and state["resolved_at"] is not None


def test_health_application_notifies_only_active_admins_and_sends_resolution(tmp_path: Path):
    store = _store(tmp_path)
    operations = FakeOperations(_critical())
    auth = FakeAuth(
        [
            {"role": "owner", "is_active": True, "telegram_user_id": 101},
            {"role": "admin", "is_active": True, "telegram_user_id": 202},
            {"role": "editor", "is_active": True, "telegram_user_id": 303},
            {"role": "admin", "is_active": False, "telegram_user_id": 404},
        ]
    )
    notifier = FakeNotifier()
    app = HealthAlertApplication(
        operations=operations,
        alerts=store,
        auth=auth,
        notifier=notifier,
        reminder_seconds=3600,
    )
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    first = app.run_once(now=now)
    assert first.notifications == 2
    assert [chat_id for chat_id, _ in notifier.messages] == [101, 202]

    second = app.run_once(now=now + timedelta(minutes=10))
    assert second.notifications == 0
    assert second.suppressed == 1

    operations.overview = _healthy()
    resolved = app.run_once(now=now + timedelta(minutes=20))
    assert resolved.resolved_notifications == 2
    assert len(notifier.messages) == 4
    assert all("восстановлен" in text for _, text in notifier.messages[-2:])


def test_undeliverable_alert_is_not_marked_notified(tmp_path: Path):
    store = _store(tmp_path)
    notifier = FakeNotifier(configured=False)
    app = HealthAlertApplication(
        operations=FakeOperations(_critical()),
        alerts=store,
        auth=FakeAuth([{"role": "owner", "is_active": True, "telegram_user_id": 101}]),
        notifier=notifier,
    )
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    stats = app.run_once(now=now)
    assert stats.undeliverable == 1
    state = store.get_state(1)
    assert state is not None
    assert state["last_notified_at"] is None
    assert state["last_delivery_error"]

    notifier.configured = True
    later = app.run_once(now=now + timedelta(minutes=1))
    assert later.notifications == 1
