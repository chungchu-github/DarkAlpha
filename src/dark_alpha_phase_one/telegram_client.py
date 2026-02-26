from __future__ import annotations

import json
import logging
import time

import requests


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    def send_json_card(self, payload: dict[str, object]) -> tuple[bool, int | None, int | None, int]:
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if not self.enabled:
            return True, None, None, 0

        endpoint = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        start = time.perf_counter()
        resp = requests.post(
            endpoint,
            json={"chat_id": self.chat_id, "text": message},
            timeout=10,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        if 200 <= resp.status_code < 300:
            message_id = resp.json().get("result", {}).get("message_id")
            return True, resp.status_code, int(message_id) if message_id is not None else None, latency_ms
        return False, resp.status_code, None, latency_ms
