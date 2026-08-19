from __future__ import annotations

from datetime import datetime
from typing import Any

from autoposter_bot.application.publishing import PublishingApplication
from autoposter_bot.domain.content import Publication, PublicationStatus
from autoposter_bot.integrations.credential_refresh import CredentialRefreshService


class PublicationReconciler:
    """Resolve asynchronous/ambiguous provider outcomes without republishing."""

    def __init__(
        self,
        *,
        store: Any,
        publishing: PublishingApplication,
        credential_refresh: CredentialRefreshService | None = None,
    ) -> None:
        self.store = store
        self.publishing = publishing
        self.credential_refresh = credential_refresh

    def run_once(self, *, now: datetime | None = None, limit: int = 100) -> dict[str, int]:
        now = now or datetime.now()
        candidates = self._candidates(limit=limit)
        stats = {
            "candidates": len(candidates),
            "processing": 0,
            "published": 0,
            "provider_failed": 0,
            "check_failed": 0,
            "skipped": 0,
        }
        for publication in candidates:
            account = self.store.get_account(publication.account_id)
            if account is None or account["platform"].lower() != publication.platform.lower():
                publication.metadata["reconciliation_last_error"] = {
                    "code": "account_missing_or_mismatch",
                    "checked_at": now.isoformat(),
                }
                self.store.save_publication(publication)
                stats["skipped"] += 1
                continue

            if self.credential_refresh is not None:
                try:
                    refreshed_options, changed = self.credential_refresh.refresh_if_needed(
                        publication.platform,
                        account.get("options") or {},
                    )
                    if changed:
                        refreshed = self.store.update_account(
                            publication.account_id,
                            options=refreshed_options,
                        )
                        if refreshed is not None:
                            account = refreshed
                except Exception as exc:
                    publication.metadata["reconciliation_last_error"] = {
                        "code": "credential_refresh_failed",
                        "message": str(exc),
                        "checked_at": now.isoformat(),
                    }
                    self.store.save_publication(publication)
                    stats["check_failed"] += 1
                    continue

            try:
                result = self.publishing.reconcile(
                    publication,
                    account_options=account.get("options") or {},
                )
            except KeyError:
                stats["skipped"] += 1
                continue
            except Exception as exc:
                publication.metadata["reconciliation_last_error"] = {
                    "code": "status_check_exception",
                    "message": str(exc),
                    "checked_at": now.isoformat(),
                }
                self.store.save_publication(publication)
                stats["check_failed"] += 1
                continue

            publication.metadata["last_reconciled_at"] = now.isoformat()
            publication.metadata["last_provider_status"] = result.status
            if result.raw_response:
                publication.metadata["last_provider_status_payload"] = result.raw_response
            if result.status == "published" and result.ok:
                stats["published"] += 1
            elif result.status == "processing" and result.ok:
                stats["processing"] += 1
            elif result.status == "failed":
                publication.metadata["republish_recommended"] = bool(result.retryable)
                stats["provider_failed"] += 1
            else:
                stats["check_failed"] += 1
            self.store.save_publication(publication)
        return stats

    def _candidates(self, *, limit: int) -> list[Publication]:
        processing = self.store.list_publications(status=PublicationStatus.PROCESSING, limit=limit)
        remaining = max(0, limit - len(processing))
        if not remaining:
            return processing
        # Quarantined records are safe to inspect only when a provider tracking
        # handle exists. Without that handle they remain terminal for manual review.
        failed = self.store.list_publications(status=PublicationStatus.FAILED, limit=max(limit, remaining))
        recoverable = [
            item
            for item in failed
            if item.provider_tracking_id
            and (
                item.last_error_code == "unknown_publish_outcome"
                or bool(item.metadata.get("reconciliation_required"))
                or bool(item.metadata.get("provider_tracking_recorded_at"))
            )
        ][:remaining]
        return processing + recoverable
