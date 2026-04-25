"""Admin whitelist — env-configured chat IDs that may command the bot.

Env vars (either works; TELEGRAM_ADMIN_CHAT_IDS takes precedence):
  TELEGRAM_ADMIN_CHAT_IDS=123,456     # comma-separated
  TELEGRAM_CHAT_ID=123                # single id (reused from notifier)
"""

import os


def allowed_chat_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ADMIN_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
    ids: set[int] = set()
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            ids.add(int(p))
        except ValueError:
            continue
    return ids


def is_authorized(chat_id: int) -> bool:
    return chat_id in allowed_chat_ids()
