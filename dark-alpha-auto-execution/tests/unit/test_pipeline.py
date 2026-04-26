"""Unit tests for strategy.pipeline end-to-end."""

from pathlib import Path

import pytest

from safety.circuit_breaker import CircuitBreaker
from safety.kill_switch import KillSwitch
from signal_adapter.schemas import SetupEvent
from storage.db import init_db
from strategy import config, pipeline
from strategy.risk_gate import RiskGate
from strategy.schemas import ExecutionTicket, Rejection


@pytest.fixture(autouse=True)
def _clear_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "main.yaml").write_text("mode: shadow\n")
    monkeypatch.setattr(config, "_CONFIG_DIR", cfg)
    config.clear_cache()


@pytest.fixture()
def ready_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "pipe.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_db(db)
    return db


@pytest.fixture()
def clean_gate(tmp_path: Path, ready_db: Path) -> RiskGate:
    ks = KillSwitch(sentinel_path=tmp_path / "kill")
    cb = CircuitBreaker(db_path=ready_db, config_path=tmp_path / "no.yaml")
    return RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)


def test_pipeline_happy_path(setup_event: SetupEvent, clean_gate: RiskGate, ready_db: Path) -> None:
    out = pipeline.run(setup_event, equity_usd=10_000.0, risk_gate=clean_gate, db_path=ready_db)
    assert isinstance(out, ExecutionTicket)
    assert out.symbol == setup_event.symbol
    assert out.direction == "long"
    assert out.shadow_mode is True  # main.yaml default
    assert len(out.orders) == 3


def test_pipeline_validator_rejection(
    setup_event: SetupEvent, clean_gate: RiskGate, ready_db: Path
) -> None:
    bad = setup_event.model_copy(update={"ranking_score": 1.0})
    out = pipeline.run(bad, equity_usd=10_000.0, risk_gate=clean_gate, db_path=ready_db)
    assert isinstance(out, Rejection) and out.stage == "validator"


def test_pipeline_sizer_rejection(
    setup_event: SetupEvent, clean_gate: RiskGate, ready_db: Path
) -> None:
    out = pipeline.run(setup_event, equity_usd=1.0, risk_gate=clean_gate, db_path=ready_db)
    # equity=1 → below min_equity in risk_gate, but sizer runs first and may also reject.
    # Either sizer or risk_gate rejection is acceptable — both are correct refusals.
    assert isinstance(out, Rejection)
    assert out.stage in {"sizer", "risk_gate"}


def test_pipeline_risk_gate_rejection(
    setup_event: SetupEvent, tmp_path: Path, ready_db: Path
) -> None:
    ks = KillSwitch(sentinel_path=tmp_path / "kill")
    ks.activate(reason="test")
    cb = CircuitBreaker(db_path=ready_db, config_path=tmp_path / "no.yaml")
    gate = RiskGate(kill_switch=ks, breaker=cb, db_path=ready_db)
    out = pipeline.run(setup_event, equity_usd=10_000.0, risk_gate=gate, db_path=ready_db)
    assert isinstance(out, Rejection) and out.stage == "risk_gate"
