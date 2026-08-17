from __future__ import annotations

from datetime import datetime

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.domain.content import PublicationStatus
from autoposter_bot.infrastructure.content_store import SQLiteContentStore
from autoposter_bot.infrastructure.publication_queue import SQLitePublicationQueue
from autoposter_bot.platforms.base import PublicationResult


class PublicationWorker:
    def __init__(
        self,
        *,
        store: SQLiteContentStore,
        queue: SQLitePublicationQueue,
        publishing: PublishingApplication,
    ) -> None:
        self.store = store
        self.queue = queue
        self.publishing = publishing

    def run_once(self, *, now: datetime | None = None, limit: int = 25) -> dict[str, int]:
        now = now or datetime.now()
        recovered = self.queue.requeue_stale(now)
        publication_ids = self.queue.claim_due(now, limit=limit)
        stats = {
            "claimed": len(publication_ids),
            "published": 0,
            "failed": 0,
            "recovered": recovered,
        }

        for publication_id in publication_ids:
            publication = self.store.get_publication(publication_id)
            if publication is None:
                stats["failed"] += 1
                continue

            variant = self.store.get_variant(publication.variant_id)
            if variant is None:
                publication.status = PublicationStatus.FAILED
                publication.last_error_code = "variant_missing"
                publication.last_error_message = "Platform variant no longer exists"
                self.store.save_publication(publication)
                stats["failed"] += 1
                continue

            account = self.store.get_account(publication.account_id)
            if account is None:
                publication.status = PublicationStatus.FAILED
                publication.last_error_code = "account_missing"
                publication.last_error_message = "Social account no longer exists"
                self.store.save_publication(publication)
                stats["failed"] += 1
                continue

            if account["platform"].lower() != publication.platform.lower():
                publication.status = PublicationStatus.FAILED
                publication.last_error_code = "account_platform_mismatch"
                publication.last_error_message = (
                    f"Account platform {account['platform']} does not match {publication.platform}"
                )
                self.store.save_publication(publication)
                stats["failed"] += 1
                continue

            try:
                result = self.publishing.publish(
                    variant,
                    publication,
                    account_options=account["options"],
                )
            except Exception as exc:
                publication.status = PublicationStatus.FAILED
                publication.last_error_code = "worker_exception"
                publication.last_error_message = str(exc)
                result = PublicationResult(
                    ok=False,
                    status="failed",
                    error_code="worker_exception",
                    error_message=str(exc),
                    retryable=True,
                )

            self.store.save_publication(publication)
            self.store.record_attempt(publication, result)
            if result.ok:
                stats["published"] += 1
            else:
                stats["failed"] += 1

        return stats
