from __future__ import annotations

import json
import logging

import requests


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    def send_json_card(self, payload: dict[str, object]) -> None:
        message = json.dumps(payload, ensure_ascii=False, indent=2)
        if not self.enabled:
            logging.info("Telegram not configured, card output: %s", message)
            return

        endpoint = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        resp = requests.post(
            endpoint,
            json={"chat_id": self.chat_id, "text": message},
            timeout=10,
        )
        resp.raise_for_status()
        logging.info("Sent proposal card to Telegram")
