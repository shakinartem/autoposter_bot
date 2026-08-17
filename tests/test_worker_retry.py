from __future__ import annotations

from datetime import datetime, timedelta

from autoposter_bot.application.retry import PublicationRetryPolicy
from autoposter_bot.apps.worker.scheduler import PublicationWorker
from autoposter_bot.domain.content import PlatformVariant, Publication, PublicationStatus
from autoposter_bot.platforms.base import PublicationResult


class FakeStore:
    def __init__(self, publication: Publication, variant: PlatformVariant, account: dict):
        self.publication = publication
        self.variant = variant
        self.account = account
        self.attempts: list[PublicationResult] = []
        self.saved_states: list[tuple[PublicationStatus, int]] = []

    def get_publication(self, publication_id: str):
        return self.publication if publication_id == self.publication.id else None

    def get_variant(self, variant_id: str):
        return self.variant if variant_id == self.variant.id else None

    def get_account(self, account_id: int):
        return self.account if account_id == self.publication.account_id else None

    def update_account(self, account_id: int, **changes):
        self.account = {**self.account, **changes}
        return self.account

    def save_publication(self, publication: Publication):
        self.publication = publication
        self.saved_states.append((publication.status, publication.attempt_count))
        return publication

    def record_attempt(self, publication: Publication, result: PublicationResult):
        self.attempts.append(result)


class FakeQueue:
    def __init__(self, publication_id: str):
        self.publication_id = publication_id
        self.retry_at: datetime | None = None
        self.cleared = 0

    def quarantine_stale_publishing(self, now: datetime):
        return 0

    def requeue_stale(self, now: datetime):
        return 0

    def claim_due(self, now: datetime, *, limit: int = 25):
        return [self.publication_id]

    def schedule_retry(self, publication_id: str, next_attempt_at: datetime, *, now: datetime):
        self.retry_at = next_attempt_at
        return True

    def clear_retry(self, publication_id: str, *, now: datetime):
        self.cleared += 1
        self.retry_at = None


class FakeAdapter:
    def validate(self, variant):
        return []


class FakeRegistry:
    def get(self, platform):
        return FakeAdapter()


class FakePublishing:
    def __init__(self, result: PublicationResult | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0
        self.registry = FakeRegistry()
        self.attempt_started_values: list[bool] = []
        self.pre_call_states: list[tuple[PublicationStatus, int]] = []

    def publish(self, variant, publication, *, account_options, attempt_started=False):
        self.calls += 1
        self.attempt_started_values.append(attempt_started)
        self.pre_call_states.append((publication.status, publication.attempt_count))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        if self.result.ok:
            publication.status = PublicationStatus.PUBLISHED
            publication.external_post_id = self.result.external_post_id
            publication.published_at = self.result.published_at or datetime.now()
        else:
            publication.status = PublicationStatus.FAILED
            publication.last_error_code = self.result.error_code
            publication.last_error_message = self.result.error_message
        return self.result


def fixture():
    variant = PlatformVariant(content_id="content-1", platform="telegram", text="hello")
    publication = Publication(
        variant_id=variant.id,
        platform="telegram",
        account_id=5,
        scheduled_at=datetime(2026, 8, 18, 12, 0, 0),
        status=PublicationStatus.SCHEDULED,
    )
    account = {
        "id": 5,
        "platform": "telegram",
        "destination": "-100123",
        "options": {"bot_token": "secret"},
    }
    return publication, variant, account


def test_transient_failure_schedules_retry_and_preserves_original_schedule():
    publication, variant, account = fixture()
    original_schedule = publication.scheduled_at
    store = FakeStore(publication, variant, account)
    queue = FakeQueue(publication.id)
    publishing = FakePublishing(
        PublicationResult(
            ok=False,
            status="failed",
            error_code="rate_limited",
            error_message="429",
            retryable=True,
        )
    )
    policy = PublicationRetryPolicy(
        base_delay_seconds=60,
        jitter_ratio=0,
        uniform=lambda low, high: low,
    )
    now = datetime(2026, 8, 18, 12, 5, 0)
    worker = PublicationWorker(store=store, queue=queue, publishing=publishing, retry_policy=policy)

    stats = worker.run_once(now=now)

    assert stats["retried"] == 1
    assert stats["failed"] == 0
    assert queue.retry_at == now + timedelta(seconds=60)
    assert store.publication.scheduled_at == original_schedule
    assert store.publication.attempt_count == 1
    assert publishing.pre_call_states == [(PublicationStatus.PUBLISHING, 1)]
    assert publishing.attempt_started_values == [True]
    assert (PublicationStatus.PUBLISHING, 1) in store.saved_states
    assert store.publication.metadata["last_retry_decision"]["reason"] == "transient"


def test_unknown_publish_exception_is_terminal_to_avoid_duplicate():
    publication, variant, account = fixture()
    store = FakeStore(publication, variant, account)
    queue = FakeQueue(publication.id)
    publishing = FakePublishing(error=TimeoutError("response lost after POST"))
    worker = PublicationWorker(store=store, queue=queue, publishing=publishing)

    stats = worker.run_once(now=datetime(2026, 8, 18, 12, 5, 0))

    assert stats["failed"] == 1
    assert stats["retried"] == 0
    assert queue.retry_at is None
    assert store.publication.last_error_code == "unknown_publish_outcome"
    assert store.publication.attempt_count == 1
    assert publishing.pre_call_states == [(PublicationStatus.PUBLISHING, 1)]
    assert store.attempts[0].retryable is False


def test_durable_remote_id_prevents_republish_after_stale_requeue():
    publication, variant, account = fixture()
    publication.external_post_id = "remote-77"
    store = FakeStore(publication, variant, account)
    queue = FakeQueue(publication.id)
    publishing = FakePublishing(
        PublicationResult(ok=True, status="published", external_post_id="should-not-run")
    )
    worker = PublicationWorker(store=store, queue=queue, publishing=publishing)

    stats = worker.run_once(now=datetime(2026, 8, 18, 12, 5, 0))

    assert publishing.calls == 0
    assert stats["deduplicated"] == 1
    assert store.publication.status == PublicationStatus.PUBLISHED
    assert queue.cleared == 1


def test_non_retryable_failure_stays_failed():
    publication, variant, account = fixture()
    store = FakeStore(publication, variant, account)
    queue = FakeQueue(publication.id)
    publishing = FakePublishing(
        PublicationResult(
            ok=False,
            status="failed",
            error_code="invalid_token",
            error_message="Invalid token",
            retryable=False,
        )
    )
    worker = PublicationWorker(store=store, queue=queue, publishing=publishing)

    stats = worker.run_once(now=datetime(2026, 8, 18, 12, 5, 0))

    assert stats["failed"] == 1
    assert stats["retried"] == 0
    assert queue.retry_at is None
    assert store.publication.metadata["last_retry_decision"]["reason"] == "non_retryable"
