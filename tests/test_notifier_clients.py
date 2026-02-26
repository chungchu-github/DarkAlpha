from __future__ import annotations

from dark_alpha_phase_one.postback_client import PostbackClient
from dark_alpha_phase_one.telegram_client import TelegramNotifier


class _Resp:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


def test_telegram_notifier_disabled_returns_success() -> None:
    notifier = TelegramNotifier(bot_token="", chat_id="")
    ok, status, message_id, latency_ms = notifier.send_json_card({"k": "v"})

    assert ok is True
    assert status is None
    assert message_id is None
    assert latency_ms == 0


def test_telegram_notifier_uses_html_parse_mode_and_keyboard(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_post(url: str, json: dict[str, object], timeout: int) -> _Resp:  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _Resp(200, {"result": {"message_id": 123}})

    monkeypatch.setattr("dark_alpha_phase_one.telegram_client.requests.post", _fake_post)

    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    ok, status, message_id, _latency_ms = notifier.send_json_card(
        {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry": 100,
            "stop": 99,
            "leverage_suggest": 10,
            "strategy": "test_emit_dryrun",
            "priority": 10000,
            "trace_id": "abc",
        }
    )

    assert ok is True
    assert status == 200
    assert message_id == 123
    payload = captured["json"]
    assert payload["parse_mode"] == "HTML"
    assert "#TEST #DRYRUN" in str(payload["text"])
    assert "reply_markup" in payload


def test_callback_copy_levels_routes_to_answer_and_message(monkeypatch) -> None:
    notifier = TelegramNotifier(bot_token="token", chat_id="chat")
    notifier._payload_by_trace["abc"] = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry": 100,
        "stop": 99,
    }
    calls: list[tuple[str, str]] = []

    def _fake_answer(callback_query_id: str, text: str) -> None:
        calls.append(("answer", text))

    def _fake_send(chat_id: str, text: str) -> None:
        calls.append(("send", text))

    monkeypatch.setattr(notifier, "_answer_callback", _fake_answer)
    monkeypatch.setattr(notifier, "_send_html", _fake_send)

    notifier._handle_callback_query(
        {
            "id": "cid",
            "data": "copy_levels:BTCUSDT:abc",
            "message": {"chat": {"id": "chat"}},
        }
    )

    assert calls[0][0] == "answer"
    assert "可複製價位" in calls[0][1]
    assert calls[1][0] == "send"
    assert "ENTRY" in calls[1][1]


def test_postback_client_disabled_returns_success() -> None:
    client = PostbackClient(url="")
    ok, status, latency_ms = client.send({"k": "v"})

    assert ok is True
    assert status is None
    assert latency_ms == 0
