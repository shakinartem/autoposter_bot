from __future__ import annotations

import requests

from autoposter_bot.config import Settings


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.token_warning_chat_id)

    def is_bot_configured(self) -> bool:
        return bool(self.settings.telegram_bot_token)

    def send_to(self, chat_id: int | str, text: str) -> None:
        if not self.is_bot_configured():
            raise ValueError("Telegram bot notifier is not configured")
        response = requests.post(
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
            data={"chat_id": chat_id, "text": text[:4000]},
            timeout=(10, 60),
        )
        response.raise_for_status()

    def send(self, text: str) -> None:
        if not self.settings.token_warning_chat_id:
            raise ValueError("Telegram warning chat is not configured")
        self.send_to(self.settings.token_warning_chat_id, text)
