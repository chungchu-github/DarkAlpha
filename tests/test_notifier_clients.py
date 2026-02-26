from __future__ import annotations

from dark_alpha_phase_one.postback_client import PostbackClient
from dark_alpha_phase_one.telegram_client import TelegramNotifier


def test_telegram_notifier_disabled_returns_success() -> None:
    notifier = TelegramNotifier(bot_token="", chat_id="")
    ok, status, message_id, latency_ms = notifier.send_json_card({"k": "v"})

    assert ok is True
    assert status is None
    assert message_id is None
    assert latency_ms == 0


def test_postback_client_disabled_returns_success() -> None:
    client = PostbackClient(url="")
    ok, status, latency_ms = client.send({"k": "v"})

    assert ok is True
    assert status is None
    assert latency_ms == 0
