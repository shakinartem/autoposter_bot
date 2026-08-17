from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from autoposter_bot.application.retry import PublicationRetryPolicy
from autoposter_bot.infrastructure.retry_schema import init_retry_schema
from autoposter_bot.platforms.base import PublicationResult
from autoposter_bot.platforms.legacy import _legacy_failure_semantics


def test_retry_policy_uses_exponential_backoff_without_rewriting_provider_reset():
    now = datetime(2026, 8, 18, 12, 0, 0)
    policy = PublicationRetryPolicy(
        max_attempts=5,
        base_delay_seconds=60,
        max_delay_seconds=3600,
        jitter_ratio=0,
        uniform=lambda low, high: low,
    )
    result = PublicationResult(ok=False, status="failed", retryable=True)

    first = policy.decide(result=result, attempt_count=1, now=now)
    third = policy.decide(result=result, attempt_count=3, now=now)

    assert first.retry is True
    assert first.next_attempt_at == now + timedelta(seconds=60)
    assert third.next_attempt_at == now + timedelta(seconds=240)


def test_retry_policy_honors_later_rate_limit_reset_and_max_attempts():
    now = datetime(2026, 8, 18, 12, 0, 0)
    reset_at = now + timedelta(minutes=20)
    policy = PublicationRetryPolicy(
        max_attempts=3,
        base_delay_seconds=60,
        max_delay_seconds=3600,
        jitter_ratio=0,
        uniform=lambda low, high: low,
    )
    limited = PublicationResult(
        ok=False,
        status="failed",
        error_code="rate_limited",
        retryable=True,
        rate_limit_reset_at=reset_at,
    )

    decision = policy.decide(result=limited, attempt_count=1, now=now)
    terminal = policy.decide(result=limited, attempt_count=3, now=now)

    assert decision.retry is True
    assert decision.reason == "rate_limit"
    assert decision.next_attempt_at == reset_at + timedelta(seconds=1)
    assert terminal.retry is False
    assert terminal.reason == "max_attempts"


def test_non_retryable_failure_is_terminal_even_before_attempt_limit():
    policy = PublicationRetryPolicy(max_attempts=5, uniform=lambda low, high: 0)
    decision = policy.decide(
        result=PublicationResult(
            ok=False,
            status="failed",
            error_code="permission_denied",
            retryable=False,
        ),
        attempt_count=1,
        now=datetime.now(),
    )
    assert decision.retry is False
    assert decision.reason == "non_retryable"


def test_legacy_failure_classifier_is_conservative():
    assert _legacy_failure_semantics("429 Too Many Requests") == ("rate_limited", True)
    assert _legacy_failure_semantics("503 Service Unavailable") == ("transient_platform_error", True)
    assert _legacy_failure_semantics("Invalid token / access denied") == (
        "auth_config_or_validation_error",
        False,
    )
    assert _legacy_failure_semantics("Something strange happened") == ("legacy_publish_failed", False)


def test_retry_schema_upgrades_existing_sqlite_publications_table(tmp_path):
    db_path = tmp_path / "legacy-retry.sqlite3"

    def connect():
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return connection

    with connect() as db:
        db.execute(
            """
            CREATE TABLE publications_v2 (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                scheduled_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "CREATE INDEX idx_publications_v2_due ON publications_v2(status, scheduled_at)"
        )

    init_retry_schema(backend="sqlite", connect=connect)

    with connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(publications_v2)")}
        index_sql = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_publications_v2_due'"
        ).fetchone()["sql"]

    assert "next_attempt_at" in columns
    assert "next_attempt_at" in index_sql
