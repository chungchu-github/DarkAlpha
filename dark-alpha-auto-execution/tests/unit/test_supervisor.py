"""Unit tests for scheduler.supervisor."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from scheduler.supervisor import Supervisor
from storage.db import init_db


class FakeEvaluator:
    def __init__(self) -> None:
        self.ticks = 0

    def tick(self) -> list[object]:
        self.ticks += 1
        return []


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "s.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    return db


def test_runs_requested_ticks(ready_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ev = FakeEvaluator()
    sup = Supervisor(evaluator=ev, eval_interval_sec=0, db_path=ready_db)  # type: ignore[arg-type]
    # Skip sleep
    monkeypatch.setattr(sup, "_sleep", lambda s: None)
    ticks = sup.run(max_ticks=3)
    assert ticks == 3
    assert ev.ticks == 3


def test_kill_switch_halts_evaluator(
    ready_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from safety.kill_switch import KillSwitch

    sentinel = tmp_path / "kill"
    sentinel.touch()

    ks = KillSwitch(sentinel_path=sentinel)

    import scheduler.supervisor as sup_mod

    monkeypatch.setattr(sup_mod, "get_kill_switch", lambda: ks)

    ev = FakeEvaluator()
    sup = Supervisor(evaluator=ev, eval_interval_sec=0, db_path=ready_db)  # type: ignore[arg-type]
    monkeypatch.setattr(sup, "_sleep", lambda s: None)
    sup.run(max_ticks=2)
    assert ev.ticks == 0  # kill switch blocked evaluator


def test_tick_exception_does_not_break_loop(
    ready_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BadEvaluator:
        def tick(self) -> list[object]:
            raise RuntimeError("boom")

    sup = Supervisor(evaluator=BadEvaluator(), eval_interval_sec=0,  # type: ignore[arg-type]
                     db_path=ready_db)
    monkeypatch.setattr(sup, "_sleep", lambda s: None)
    ticks = sup.run(max_ticks=2)
    assert ticks == 2  # loop survived


def test_day_rollover_writes_snapshot(
    ready_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sup = Supervisor(evaluator=FakeEvaluator(), eval_interval_sec=0,  # type: ignore[arg-type]
                     db_path=ready_db)
    monkeypatch.setattr(sup, "_sleep", lambda s: None)

    yesterday = datetime.now(tz=UTC).date() - timedelta(days=1)
    sup.set_last_snapshot_date(yesterday)

    calls: list[date] = []
    import scheduler.supervisor as mod

    def fake_write(d: date, db_path: Path | None = None) -> None:
        calls.append(d)

    monkeypatch.setattr(mod, "write_snapshot", fake_write)
    sup.run(max_ticks=1)
    assert calls == [yesterday]


def test_request_stop_exits_cleanly(
    ready_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sup = Supervisor(evaluator=FakeEvaluator(), eval_interval_sec=0,  # type: ignore[arg-type]
                     db_path=ready_db)
    monkeypatch.setattr(sup, "_sleep", lambda s: None)
    sup.request_stop()
    ticks = sup.run(max_ticks=10)
    assert ticks == 0  # _stop already True so while-loop skips entirely
