"""Thin Telegram Bot API wrapper.

Uses long-polling via `getUpdates`. No external dependency beyond httpx,
which we already have for the Binance public client.
"""

from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_DEFAULT_BASE = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, token: str, base_url: str = _DEFAULT_BASE, timeout: float = 35.0) -> None:
        self._token = token
        self._base = base_url
        self._timeout = timeout

    def send_message(self, chat_id: int, text: str) -> None:
        """Fire-and-forget send. Errors are logged, never raised."""
        try:
            httpx.post(
                f"{self._base}/bot{self._token}/sendMessage",
                data={"chat_id": chat_id, "text": text},
                timeout=self._timeout,
            ).raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.send_failed", chat_id=chat_id, error=str(exc))

    def get_updates(self, offset: int, timeout_sec: int = 30) -> list[dict[str, Any]]:
        """Long-poll. Returns parsed `result` list, or [] on any error.

        `allowed_updates=["message"]` is passed explicitly on every call.
        Telegram persists the last-seen allowed_updates list server-side,
        so if the bot's previous user (e.g. DarkAlpha) set it to a subset
        like ["callback_query"], we'd never receive plain messages without
        this override.
        """
        try:
            resp = httpx.get(
                f"{self._base}/bot{self._token}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": timeout_sec,
                    "allowed_updates": '["message"]',
                },
                timeout=self._timeout + timeout_sec,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                log.warning("telegram.get_updates_not_ok", data=data)
                return []
            result = data.get("result", [])
            return result if isinstance(result, list) else []
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.get_updates_failed", error=str(exc))
            return []
