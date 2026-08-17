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
        quarantined = self.queue.quarantine_stale_publishing(now)
        recovered = self.queue.requeue_stale(now)
        publication_ids = self.queue.claim_due(now, limit=limit)
        stats = {
            "claimed": len(publication_ids),
            "published": 0,
            "retried": 0,
            "failed": 0,
            "deduplicated": 0,
            "quarantined": quarantined,
            "recovered": recovered,
        }

        for publication_id in publication_ids:
            publication = self.store.get_publication(publication_id)
            if publication is None:
                stats["failed"] += 1
                continue

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

            try:
                adapter = self.publishing.registry.get(publication.platform)
                issues = adapter.validate(variant)
            except KeyError as exc:
                self._terminal_failure(publication, code="platform_missing", message=str(exc))
                stats["failed"] += 1
                continue
            if issues:
                self._terminal_failure(
                    publication,
                    code=issues[0].code,
                    message=issues[0].message,
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

            # Persist the network boundary before the provider call. After this
            # point a crash has an ambiguous remote outcome and must not be
            # automatically treated like a never-started queued job.
            publication.attempt_count += 1
            publication.status = PublicationStatus.PUBLISHING
            publication.last_error_code = None
            publication.last_error_message = None
            publication.metadata["publish_started_at"] = now.isoformat()
            publication.metadata["publish_phase"] = "remote_call_started"
            self.store.save_publication(publication)

            try:
                result = self.publishing.publish(
                    variant,
                    publication,
                    account_options=account["options"],
                    attempt_started=True,
                )
            except Exception as exc:
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

            publication.metadata["publish_phase"] = (
                "remote_confirmed" if result.ok else "remote_failed_or_unknown"
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
