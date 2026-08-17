from __future__ import annotations

from datetime import datetime
from typing import Any

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.application.retry import PublicationRetryPolicy
from autoposter_bot.domain.content import Publication, PublicationStatus
from autoposter_bot.integrations.credential_refresh import CredentialRefreshService
from autoposter_bot.platforms.base import PublicationResult


class PublicationWorker:
    def __init__(
        self,
        *,
        store: Any,
        queue: Any,
        publishing: PublishingApplication,
        retry_policy: PublicationRetryPolicy | None = None,
        credential_refresh: CredentialRefreshService | None = None,
    ) -> None:
        self.store = store
        self.queue = queue
        self.publishing = publishing
        self.retry_policy = retry_policy or PublicationRetryPolicy()
        self.credential_refresh = credential_refresh

    def run_once(self, *, now: datetime | None = None, limit: int = 25) -> dict[str, int]:
        now = now or datetime.now()
        recovered = self.queue.requeue_stale(now)
        publication_ids = self.queue.claim_due(now, limit=limit)
        stats = {
            "claimed": len(publication_ids),
            "published": 0,
            "retried": 0,
            "failed": 0,
            "deduplicated": 0,
            "recovered": recovered,
        }

        for publication_id in publication_ids:
            publication = self.store.get_publication(publication_id)
            if publication is None:
                stats["failed"] += 1
                continue

            # A stale/manual requeue must never republish an item that already has
            # durable evidence of a successful remote publish.
            if publication.external_post_id or publication.published_at:
                publication.status = PublicationStatus.PUBLISHED
                publication.last_error_code = None
                publication.last_error_message = None
                self.store.save_publication(publication)
                self.queue.clear_retry(publication.id, now=now)
                stats["deduplicated"] += 1
                continue

            variant = self.store.get_variant(publication.variant_id)
            if variant is None:
                self._terminal_failure(
                    publication,
                    code="variant_missing",
                    message="Platform variant no longer exists",
                )
                stats["failed"] += 1
                continue

            account = self.store.get_account(publication.account_id)
            if account is None:
                self._terminal_failure(
                    publication,
                    code="account_missing",
                    message="Social account no longer exists",
                )
                stats["failed"] += 1
                continue

            if account["platform"].lower() != publication.platform.lower():
                self._terminal_failure(
                    publication,
                    code="account_platform_mismatch",
                    message=f"Account platform {account['platform']} does not match {publication.platform}",
                )
                stats["failed"] += 1
                continue

            if self.credential_refresh is not None:
                try:
                    refreshed_options, changed = self.credential_refresh.refresh_if_needed(
                        publication.platform,
                        account["options"],
                    )
                    if changed:
                        refreshed_account = self.store.update_account(
                            publication.account_id,
                            options=refreshed_options,
                        )
                        if refreshed_account is not None:
                            account = refreshed_account
                except Exception as exc:
                    # Publishing has not started yet, so retrying a transient token
                    # refresh failure cannot create a duplicate remote post.
                    publication.attempt_count += 1
                    publication.status = PublicationStatus.FAILED
                    publication.last_error_code = "credential_refresh_failed"
                    publication.last_error_message = str(exc)
                    result = PublicationResult(
                        ok=False,
                        status="failed",
                        error_code="credential_refresh_failed",
                        error_message=str(exc),
                        retryable=True,
                    )
                    self._record_and_schedule(publication, result, now, stats)
                    continue

            try:
                result = self.publishing.publish(
                    variant,
                    publication,
                    account_options=account["options"],
                )
            except Exception as exc:
                # Unknown outcome: the provider may have accepted the POST before
                # the response or process was lost. Do not blind-retry and risk a
                # duplicate. A platform-specific reconciler can safely requeue it.
                publication.status = PublicationStatus.FAILED
                publication.last_error_code = "unknown_publish_outcome"
                publication.last_error_message = str(exc)
                result = PublicationResult(
                    ok=False,
                    status="failed",
                    error_code="unknown_publish_outcome",
                    error_message=str(exc),
                    retryable=False,
                    raw_response={"exception_type": type(exc).__name__},
                )

            self._record_and_schedule(publication, result, now, stats)

        return stats

    def _record_and_schedule(
        self,
        publication: Publication,
        result: PublicationResult,
        now: datetime,
        stats: dict[str, int],
    ) -> None:
        self.store.save_publication(publication)
        self.store.record_attempt(publication, result)

        if result.ok:
            self.queue.clear_retry(publication.id, now=now)
            stats["published"] += 1
            return

        decision = self.retry_policy.decide(
            result=result,
            attempt_count=publication.attempt_count,
            now=now,
        )
        publication.metadata["last_retry_decision"] = {
            "retry": decision.retry,
            "reason": decision.reason,
            "attempt_count": publication.attempt_count,
            "next_attempt_at": decision.next_attempt_at.isoformat() if decision.next_attempt_at else None,
        }
        self.store.save_publication(publication)

        if decision.retry and decision.next_attempt_at is not None:
            scheduled = self.queue.schedule_retry(
                publication.id,
                decision.next_attempt_at,
                now=now,
            )
            if scheduled:
                stats["retried"] += 1
                return

        self.queue.clear_retry(publication.id, now=now)
        stats["failed"] += 1

    def _terminal_failure(self, publication: Publication, *, code: str, message: str) -> None:
        publication.status = PublicationStatus.FAILED
        publication.last_error_code = code
        publication.last_error_message = message
        self.store.save_publication(publication)
