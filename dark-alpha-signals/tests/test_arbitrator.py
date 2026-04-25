from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dark_alpha_phase_one.calculations import Candle
from dark_alpha_phase_one.engine.arbitrator import Arbitrator, ArbitratorConfig
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.models import ProposalCard


def _ctx(symbol: str = "BTCUSDT") -> SignalContext:
    return SignalContext(
        symbol=symbol,
        timestamp=datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        price=100.0,
        klines_1m=[Candle(open=1, high=2, low=1, close=1.5)] * 21,
        return_5m=0.01,
        atr_15m=1.0,
        atr_15m_baseline=0.8,
        funding_rate=0.0005,
        open_interest=1000,
        mark_price=100.2,
        open_interest_zscore_15m=2.0,
        open_interest_delta_15m=0.12,
        last_kline_close_ts=datetime(2026, 2, 25, 11, 59, tzinfo=timezone.utc),
    )


def _card(strategy: str, side: str, entry: float, stop: float, priority: int, confidence: float, ttl: int) -> ProposalCard:
    return ProposalCard.create(
        symbol="BTCUSDT",
        strategy=strategy,
        side=side,
        entry=entry,
        stop=stop,
        leverage_suggest=50,
        position_usdt=100,
        max_risk_usdt=10,
        ttl_minutes=ttl,
        rationale="r",
        priority=priority,
        confidence=confidence,
    )


def _arb(last_lookup):
    return Arbitrator(ArbitratorConfig(300, 0.001, 0.001), last_lookup)


def test_opposite_sides_choose_higher_priority() -> None:
    arb = _arb(lambda _: None)
    a = _card("vol_breakout_card", "LONG", 100, 99, 40, 60, 15)
    b = _card("fake_breakout_reversal", "SHORT", 100, 101, 100, 50, 5)
    winner = arb.choose_best([a, b], _ctx())
    assert winner is not None
    assert winner.strategy == "fake_breakout_reversal"


def test_same_side_similar_entry_keep_higher_priority() -> None:
    arb = _arb(lambda _: None)
    a = _card("A", "LONG", 100.0, 99.0, 80, 60, 10)
    b = _card("B", "LONG", 100.05, 99.01, 60, 90, 10)
    winner = arb.choose_best([a, b], _ctx())
    assert winner is not None
    assert winner.strategy == "A"


def test_same_priority_choose_higher_confidence() -> None:
    arb = _arb(lambda _: None)
    a = _card("A", "LONG", 100, 99, 80, 61, 10)
    b = _card("B", "SHORT", 100, 101, 80, 75, 10)
    winner = arb.choose_best([a, b], _ctx())
    assert winner is not None
    assert winner.strategy == "B"


def test_same_priority_confidence_choose_shorter_ttl() -> None:
    arb = _arb(lambda _: None)
    a = _card("A", "LONG", 100, 99, 80, 70, 15)
    b = _card("B", "SHORT", 100, 101, 80, 70, 5)
    winner = arb.choose_best([a, b], _ctx())
    assert winner is not None
    assert winner.strategy == "B"


def test_dedupe_window_blocks_push() -> None:
    now = datetime(2026, 2, 25, 11, 59, 30, tzinfo=timezone.utc)
    arb = _arb(lambda _: now)
    a = _card("A", "LONG", 100, 99, 80, 70, 15)
    winner = arb.choose_best([a], _ctx())
    assert winner is None


def test_dedupe_is_symbol_scoped() -> None:
    def lookup(symbol: str):
        if symbol == "BTCUSDT":
            return datetime(2026, 2, 25, 11, 59, 30, tzinfo=timezone.utc)
        return None

    arb = _arb(lookup)
    a = _card("A", "LONG", 100, 99, 80, 70, 15)
    winner_eth = arb.choose_best([a], _ctx("ETHUSDT"))
    assert winner_eth is not None


def test_same_side_similar_stop_uses_stop_based_ratio() -> None:
    arb = _arb(lambda _: None)
    a = _card("A", "LONG", 100.0, 0.10, 80, 60, 10)
    b = _card("B", "LONG", 100.0, 0.12, 60, 90, 10)

    winner = arb.choose_best([a, b], _ctx())

    assert winner is not None
    assert winner.strategy == "A"
