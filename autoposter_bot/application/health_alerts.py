from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from autoposter_bot.infrastructure.auth_store import role_allows
from autoposter_bot.infrastructure.health_alert_store import HealthAlertDecision, HealthAlertStore


class AlertNotifier(Protocol):
    def is_bot_configured(self) -> bool: ...
    def send_to(self, chat_id: int | str, text: str) -> None: ...


@dataclass(slots=True)
class HealthAlertStats:
    workspaces: int = 0
    healthy: int = 0
    active: int = 0
    notifications: int = 0
    resolved_notifications: int = 0
    suppressed: int = 0
    undeliverable: int = 0
    delivery_failures: int = 0


class HealthAlertApplication:
    def __init__(
        self,
        *,
        operations: Any,
        alerts: HealthAlertStore,
        auth: Any,
        notifier: AlertNotifier,
        minimum_severity: str = "critical",
        reminder_seconds: int = 6 * 60 * 60,
    ) -> None:
        if minimum_severity not in {"degraded", "critical"}:
            raise ValueError("minimum_severity must be degraded or critical")
        self.operations = operations
        self.alerts = alerts
        self.auth = auth
        self.notifier = notifier
        self.minimum_severity = minimum_severity
        self.reminder_seconds = max(60, reminder_seconds)

    def run_once(self, *, now: datetime | None = None) -> HealthAlertStats:
        now = now or datetime.now(timezone.utc)
        stats = HealthAlertStats()
        for workspace in self.alerts.list_workspaces():
            stats.workspaces += 1
            workspace_id = int(workspace["id"])
            overview = self.operations.workspace_overview(workspace_id=workspace_id, now=now)
            alert_overview = self._thresholded_overview(overview)
            decision = self.alerts.evaluate(
                workspace_id=workspace_id,
                workspace_name=str(workspace["name"]),
                overview=alert_overview,
                now=now,
                reminder_seconds=self.reminder_seconds,
            )
            if decision.action == "none":
                if alert_overview.get("health") == "healthy":
                    stats.healthy += 1
                else:
                    stats.active += 1
                    stats.suppressed += 1
                continue

            if decision.action == "resolve":
                stats.healthy += 1
            else:
                stats.active += 1

            recipients = self._recipients(workspace_id)
            if not self.notifier.is_bot_configured() or not recipients:
                if decision.action == "notify":
                    self.alerts.mark_delivery_error(
                        workspace_id=workspace_id,
                        error="Telegram notifier is not configured or workspace has no active admin/owner Telegram recipient",
                    )
                stats.undeliverable += 1
                continue

            text = self._message(decision)
            delivered = 0
            failures: list[str] = []
            for chat_id in recipients:
                try:
                    self.notifier.send_to(chat_id, text)
                    delivered += 1
                except Exception as exc:  # provider failure must not stop other workspaces
                    failures.append(f"{chat_id}: {exc}")
                    stats.delivery_failures += 1

            if delivered:
                if decision.action == "notify":
                    self.alerts.mark_notified(
                        workspace_id=workspace_id,
                        recipient_count=delivered,
                        now=now,
                    )
                    stats.notifications += delivered
                else:
                    stats.resolved_notifications += delivered
            elif decision.action == "notify":
                self.alerts.mark_delivery_error(
                    workspace_id=workspace_id,
                    error="; ".join(failures) or "Telegram delivery failed",
                )
                stats.undeliverable += 1
        return stats

    def _thresholded_overview(self, overview: dict[str, Any]) -> dict[str, Any]:
        health = str(overview.get("health") or "healthy")
        severity_level = {"healthy": 0, "degraded": 1, "critical": 2}
        minimum = severity_level[self.minimum_severity]
        if severity_level.get(health, 0) >= minimum:
            return overview
        result = dict(overview)
        result["health"] = "healthy"
        result["health_reasons"] = []
        return result

    def _recipients(self, workspace_id: int) -> list[int]:
        recipients: list[int] = []
        for member in self.auth.list_members(workspace_id):
            if not member.get("is_active") or not role_allows(str(member.get("role")), "admin"):
                continue
            telegram_id = member.get("telegram_user_id")
            if telegram_id is not None:
                recipients.append(int(telegram_id))
        return sorted(set(recipients))

    @staticmethod
    def _message(decision: HealthAlertDecision) -> str:
        if decision.action == "resolve":
            return (
                "✅ Autoposter восстановлен\n"
                f"Workspace: {decision.workspace_name}\n"
                "Критические operational-сигналы больше не активны."
            )

        reasons = decision.overview.get("health_reasons") or []
        reason_lines = []
        labels = {
            "unknown_publish_outcome": "неизвестный результат публикации",
            "queue_lag": "отставание очереди",
            "stale_provider_processing": "зависшая обработка у платформы",
            "attempt_failure_rate": "высокая доля ошибок публикации",
            "social_connection_health": "проблема подключения соцсети",
        }
        for reason in reasons[:8]:
            code = str(reason.get("code") or "unknown")
            value = reason.get("count", reason.get("value"))
            suffix = f": {value}" if value is not None else ""
            reason_lines.append(f"• {labels.get(code, code)}{suffix}")
        reminder = "\nЭто повторное напоминание по тому же инциденту." if decision.previously_notified else ""
        return (
            "🚨 Autoposter: критический operational alert\n"
            f"Workspace: {decision.workspace_name}\n"
            + "\n".join(reason_lines)
            + reminder
            + "\n\nОткройте Operations → Recovery перед любым ручным retry."
        )
