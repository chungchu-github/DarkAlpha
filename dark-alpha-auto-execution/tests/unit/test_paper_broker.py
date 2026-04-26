"""Unit tests for execution.paper_broker."""

from datetime import UTC, datetime

from execution.paper_broker import PaperBroker
from strategy.schemas import ExecutionTicket, PlannedOrder


def _ticket(direction: str = "long") -> ExecutionTicket:
    if direction == "long":
        entry_side, exit_side = "buy", "sell"
        entry, stop, tp = 100.0, 99.0, 102.0
    else:
        entry_side, exit_side = "sell", "buy"
        entry, stop, tp = 100.0, 101.0, 98.0
    return ExecutionTicket(
        ticket_id="t1",
        source_event_id="e1",
        symbol="BTCUSDT-PERP",
        direction=direction,  # type: ignore[arg-type]
        regime="x",
        ranking_score=8.0,
        shadow_mode=True,
        gate="gate1",
        entry_price=entry,
        stop_price=stop,
        take_profit_price=tp,
        quantity=1.0,
        notional_usd=100.0,
        leverage=1.0,
        risk_usd=1.0,
        orders=[
            PlannedOrder(
                role="entry",
                side=entry_side,
                type="limit",
                symbol="BTCUSDT-PERP",  # type: ignore[arg-type]
                price=entry,
                quantity=1.0,
                reduce_only=False,
            ),
            PlannedOrder(
                role="stop",
                side=exit_side,
                type="stop_market",
                symbol="BTCUSDT-PERP",  # type: ignore[arg-type]
                price=stop,
                quantity=1.0,
                reduce_only=True,
            ),
            PlannedOrder(
                role="take_profit",
                side=exit_side,
                type="limit",
                symbol="BTCUSDT-PERP",  # type: ignore[arg-type]
                price=tp,
                quantity=1.0,
                reduce_only=True,
            ),
        ],
        created_at=datetime.now(tz=UTC).isoformat(),
    )


def test_entry_fill_adds_slippage_buy() -> None:
    broker = PaperBroker(slippage_bps=10)  # 10 bps = 0.1%
    fill = broker.simulate_entry(_ticket("long"))
    assert fill.price > 100.0
    assert fill.fee_usd > 0
    assert fill.side == "buy"


def test_entry_fill_adds_slippage_sell() -> None:
    broker = PaperBroker(slippage_bps=10)
    fill = broker.simulate_entry(_ticket("short"))
    assert fill.price < 100.0
    assert fill.side == "sell"


def test_exit_stop_loss_long() -> None:
    broker = PaperBroker(slippage_bps=10)
    fill = broker.simulate_exit(_ticket("long"), reason="stop_loss")
    assert fill.reduce_only
    assert fill.side == "sell"
    assert fill.price < 99.0  # stop with negative slippage on sell


def test_exit_take_profit_long() -> None:
    broker = PaperBroker(slippage_bps=10)
    fill = broker.simulate_exit(_ticket("long"), reason="take_profit")
    assert fill.reduce_only
    assert fill.price < 102.0  # sell with negative slippage


def test_exit_manual_with_mark_price() -> None:
    broker = PaperBroker(slippage_bps=10)
    fill = broker.simulate_exit(_ticket("long"), reason="manual", mark_price=101.5)
    assert fill.reduce_only
    assert fill.price < 101.5


def test_fee_scales_with_notional() -> None:
    broker = PaperBroker()
    small = broker.simulate_entry(_ticket("long"))
    # Double the quantity by rebuilding ticket
    t = _ticket("long")
    t.orders[0].quantity = 2.0
    t.quantity = 2.0
    big = broker.simulate_entry(t)
    assert big.fee_usd > small.fee_usd
