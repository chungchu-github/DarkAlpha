"""Alert notifier — sends critical events to configured channels.

Currently supports Telegram. Email and others can be added later.
All functions are best-effort: they never raise, never block the caller.

Required env vars (add to .env):
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — your personal or group chat ID
"""

import os
import urllib.parse
import urllib.request

import structlog

log = structlog.get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT_SEC = 5


def send_alert(level: str, message: str) -> None:
    """Send an alert to all configured channels. Never raises."""
    _send_telegram(level, message)


def _send_telegram(level: str, message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        log.debug("notifier.telegram_skipped", reason="no credentials configured")
        return

    text = f"[{level}] dark-alpha-auto\n{message}"
    url = _TELEGRAM_API.format(token=token)

    try:
        data = f"chat_id={chat_id}&text={urllib.parse.quote(text)}".encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            if resp.status != 200:
                log.warning("notifier.telegram_failed", status=resp.status)
    except Exception as exc:  # noqa: BLE001
        log.warning("notifier.telegram_error", error=str(exc))
