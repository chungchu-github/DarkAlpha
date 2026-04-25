"""Polling bot loop.

Runs `getUpdates` in a long-polling loop, dispatches commands to handlers.
Unauthorized chat IDs are silently rejected (with a log line).
"""

import os
import signal
from types import FrameType
from typing import Any

import structlog

from telegram.auth import is_authorized
from telegram.client import TelegramClient
from telegram.handlers import DISPATCH, handle_help

log = structlog.get_logger(__name__)


class Bot:
    def __init__(
        self,
        client: TelegramClient | None = None,
        token: str | None = None,
    ) -> None:
        tok: str = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._client = client or TelegramClient(token=tok)
        self._offset = 0
        self._stop = False

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, max_iterations: int | None = None) -> int:
        """Main loop. `max_iterations` is for tests — None means forever."""
        self._install_signal_handlers()
        log.info("telegram.bot_start")
        iters = 0
        while not self._stop:
            self.poll_once()
            iters += 1
            if max_iterations is not None and iters >= max_iterations:
                break
        log.info("telegram.bot_stop", iterations=iters)
        return iters

    def poll_once(self) -> int:
        """One pass of getUpdates + dispatch. Returns number of updates handled."""
        updates = self._client.get_updates(offset=self._offset)
        handled = 0
        for u in updates:
            update_id = int(u.get("update_id", 0))
            self._offset = max(self._offset, update_id + 1)
            if self._handle_update(u):
                handled += 1
        return handled

    def request_stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle_update(self, update: dict[str, Any]) -> bool:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text:
            return False

        try:
            chat_id_int = int(chat_id)
        except (TypeError, ValueError):
            return False

        if not is_authorized(chat_id_int):
            log.warning("telegram.unauthorized", chat_id=chat_id_int, text=text[:60])
            return False

        cmd, *args = text.split()
        # Strip @botname suffix so `/status@darkalpha_alert_bot` → `/status`
        cmd_lc = cmd.lower().split("@", 1)[0]

        def reply(body: str) -> None:
            self._client.send_message(chat_id_int, body)

        handler = DISPATCH.get(cmd_lc)
        if handler is None:
            handle_help(reply, [])
            return True

        try:
            handler(reply, args)
        except Exception as exc:  # noqa: BLE001
            log.error("telegram.handler_error", cmd=cmd_lc, error=str(exc))
            reply(f"handler error: {exc}")
        return True

    def _install_signal_handlers(self) -> None:
        def _h(_s: int, _f: FrameType | None) -> None:
            log.warning("telegram.signal_received", signum=_s)
            self._stop = True

        try:
            signal.signal(signal.SIGINT, _h)
            signal.signal(signal.SIGTERM, _h)
        except ValueError:
            pass


def run_bot() -> None:
    """CLI entry point."""
    Bot().run()
