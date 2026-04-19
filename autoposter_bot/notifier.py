from __future__ import annotations

import requests

from autoposter_bot.config import Settings


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.token_warning_chat_id)

    def send(self, text: str) -> None:
        if not self.is_configured():
            raise ValueError("Telegram notifier is not configured")
        requests.post(
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
            data={
                "chat_id": self.settings.token_warning_chat_id,
                "text": text[:4000],
            },
            timeout=(10, 60),
        )
