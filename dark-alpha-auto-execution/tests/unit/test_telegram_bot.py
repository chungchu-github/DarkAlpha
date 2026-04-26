"""Unit tests for telegram.bot — polling loop + update dispatch."""

from pathlib import Path
from typing import Any

import pytest

from storage.db import init_db
from telegram.bot import Bot
from telegram.client import TelegramClient


class FakeClient:
    """Stand-in for TelegramClient — deterministic, no network."""

    def __init__(self, scripted_updates: list[list[dict[str, Any]]]) -> None:
        self._scripted = scripted_updates
        self.sent: list[tuple[int, str]] = []
        self.calls = 0

    def get_updates(self, offset: int, timeout_sec: int = 30) -> list[dict[str, Any]]:
        if self.calls < len(self._scripted):
            out = self._scripted[self.calls]
            self.calls += 1
            return out
        return []

    def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "bot.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    return db


@pytest.fixture()
def admin_env(monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_IDS", "999")
    return 999


def _update(update_id: int, chat_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat_id}, "text": text},
    }


def test_authorized_help_replies(admin_env: int, ready_db: Path) -> None:
    client = FakeClient([[_update(1, admin_env, "/help")]])
    bot = Bot(client=client)  # type: ignore[arg-type]
    bot.poll_once()
    assert client.sent and "/status" in client.sent[0][1]


def test_unauthorized_is_silent(admin_env: int, ready_db: Path) -> None:
    intruder = 1111
    client = FakeClient([[_update(1, intruder, "/halt")]])
    bot = Bot(client=client)  # type: ignore[arg-type]
    bot.poll_once()
    assert client.sent == []


def test_unknown_command_returns_help(admin_env: int, ready_db: Path) -> None:
    client = FakeClient([[_update(1, admin_env, "/doesnotexist")]])
    bot = Bot(client=client)  # type: ignore[arg-type]
    bot.poll_once()
    assert client.sent and "/halt" in client.sent[0][1]


def test_offset_advances(admin_env: int, ready_db: Path) -> None:
    client = FakeClient(
        [
            [_update(5, admin_env, "/help")],
            [_update(7, admin_env, "/help")],
        ]
    )
    bot = Bot(client=client)  # type: ignore[arg-type]
    bot.poll_once()
    assert bot._offset == 6
    bot.poll_once()
    assert bot._offset == 8


def test_run_max_iterations(admin_env: int, ready_db: Path) -> None:
    client = FakeClient([[_update(1, admin_env, "/help")]])
    bot = Bot(client=client)  # type: ignore[arg-type]
    iters = bot.run(max_iterations=2)
    assert iters == 2


def test_empty_message_ignored(admin_env: int, ready_db: Path) -> None:
    update = {"update_id": 1, "message": {"chat": {"id": admin_env}, "text": ""}}
    client = FakeClient([[update]])
    bot = Bot(client=client)  # type: ignore[arg-type]
    bot.poll_once()
    assert client.sent == []


def test_handler_exception_replies_error(
    admin_env: int, ready_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blow_up(_reply: object, _args: list[str]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(
        __import__("telegram.handlers", fromlist=["DISPATCH"]).DISPATCH,
        "/status",
        blow_up,
    )

    client = FakeClient([[_update(1, admin_env, "/status")]])
    bot = Bot(client=client)  # type: ignore[arg-type]
    bot.poll_once()
    assert client.sent and "handler error" in client.sent[0][1]


def test_mention_suffix_stripped(admin_env: int, ready_db: Path) -> None:
    client = FakeClient([[_update(1, admin_env, "/status@darkalpha_alert_bot")]])
    bot = Bot(client=client)  # type: ignore[arg-type]
    bot.poll_once()
    assert client.sent
    # help message contains "/halt"; status message contains "Kill switch"
    assert "Kill switch" in client.sent[0][1]


def test_client_wrapper_signature() -> None:
    """Sanity: real client has the methods the bot calls on it."""
    c = TelegramClient(token="x")
    assert hasattr(c, "get_updates")
    assert hasattr(c, "send_message")
