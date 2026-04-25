"""Unit tests for telegram.client — HTTP interactions mocked."""

from typing import Any

import httpx
import pytest

from telegram.client import TelegramClient


def test_send_message_posts_correct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResp:
        def raise_for_status(self) -> None:
            pass

    def fake_post(url: str, data: dict[str, Any], timeout: float) -> FakeResp:
        captured["url"] = url
        captured["data"] = data
        return FakeResp()

    monkeypatch.setattr(httpx, "post", fake_post)
    TelegramClient(token="ABC").send_message(42, "hi")
    assert "/botABC/sendMessage" in captured["url"]
    assert captured["data"] == {"chat_id": 42, "text": "hi"}


def test_send_message_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_kw: Any) -> None:
        raise httpx.HTTPError("down")

    monkeypatch.setattr(httpx, "post", boom)
    TelegramClient(token="x").send_message(1, "ping")  # must not raise


def test_get_updates_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"ok": True, "result": [{"update_id": 1}]}

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp())
    updates = TelegramClient(token="x").get_updates(offset=0)
    assert updates == [{"update_id": 1}]


def test_get_updates_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"ok": False, "description": "error"}

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp())
    assert TelegramClient(token="x").get_updates(offset=0) == []


def test_get_updates_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_kw: Any) -> None:
        raise httpx.HTTPError("network")

    monkeypatch.setattr(httpx, "get", boom)
    assert TelegramClient(token="x").get_updates(offset=0) == []
