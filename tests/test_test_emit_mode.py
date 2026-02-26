from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.data.datastore import FundingRatePoint, SymbolSnapshot
from dark_alpha_phase_one.service import DerivativesGate, SignalService


def _snapshot(price: float = 100.0) -> SymbolSnapshot:
    now = datetime.now(tz=timezone.utc)
    return SymbolSnapshot(
        symbol="BTCUSDT",
        price=price,
        klines_1m=[Candle(open=99.0, high=101.0, low=98.0, close=100.0)],
        last_price_ts=now,
        last_kline_close_ts=now,
        last_kline_recv_ts=now,
        data_source_mode="ws",
        last_funding_rate=0.0001,
        next_funding_time_ms=1700000000000,
        mark_price=100.0,
        funding_rate_history=[FundingRatePoint(funding_rate=0.0001, funding_time=1700000000000)],
        open_interest=1234.0,
        open_interest_ts=now,
        funding_ts=now,
        open_interest_series=[(now, 1234.0)],
    )


def _service_for_test_emit() -> SignalService:
    service = object.__new__(SignalService)
    service.settings = SimpleNamespace(
        max_risk_usdt=10.0,
        leverage_suggest=20,
        test_emit_enabled=True,
        test_emit_symbols=["BTCUSDT"],
        test_emit_interval_sec=60,
        test_emit_tf="1m",
    )
    service._last_test_emit_ts_by_symbol = {}
    service._log_signal_decision = lambda **_kwargs: None
    return service


def test_maybe_test_emit_emits_card_for_enabled_symbol(monkeypatch) -> None:
    service = _service_for_test_emit()
    monkeypatch.setattr("dark_alpha_phase_one.service.time.time", lambda: 1_000.0)

    card, trace_id = service._maybe_test_emit(
        "BTCUSDT",
        _snapshot(),
        DerivativesGate(allow=True, oi_status="stale", funding_raw_age_ms=1, oi_raw_age_ms=200_000, reason="ok"),
    )

    assert card is not None
    assert trace_id is not None
    assert card.strategy == "test_emit_dryrun"
    assert card.oi_status == "stale"


def test_maybe_test_emit_respects_interval_per_symbol(monkeypatch) -> None:
    service = _service_for_test_emit()
    monkeypatch.setattr("dark_alpha_phase_one.service.time.time", lambda: 1_000.0)

    first_card, first_trace_id = service._maybe_test_emit(
        "BTCUSDT",
        _snapshot(),
        DerivativesGate(allow=True, oi_status="fresh", funding_raw_age_ms=1, oi_raw_age_ms=1, reason="ok"),
    )
    blocked_card, blocked_trace_id = service._maybe_test_emit(
        "BTCUSDT",
        _snapshot(),
        DerivativesGate(allow=True, oi_status="fresh", funding_raw_age_ms=1, oi_raw_age_ms=1, reason="ok"),
    )

    assert first_card is not None
    assert first_trace_id is not None
    assert blocked_card is None
    assert blocked_trace_id is None
