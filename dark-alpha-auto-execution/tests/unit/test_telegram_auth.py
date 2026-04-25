"""Unit tests for telegram.auth — admin whitelist parsing."""

import pytest

from telegram.auth import allowed_chat_ids, is_authorized


def test_empty_returns_empty_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert allowed_chat_ids() == set()
    assert not is_authorized(123)


def test_single_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert allowed_chat_ids() == {42}
    assert is_authorized(42)
    assert not is_authorized(43)


def test_admin_overrides_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_IDS", "100,200")
    assert allowed_chat_ids() == {100, 200}
    assert not is_authorized(42)
    assert is_authorized(100)


def test_malformed_entries_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_IDS", "1,,abc, 2 ,")
    assert allowed_chat_ids() == {1, 2}
