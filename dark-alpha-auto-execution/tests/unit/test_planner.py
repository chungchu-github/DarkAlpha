"""Unit tests for strategy.planner."""

from signal_adapter.schemas import SetupEvent
from strategy import planner
from strategy.sizer import SizingResult


def _sizing(entry: float, stop: float, qty: float = 1.0) -> SizingResult:
    return SizingResult(
        quantity=qty,
        notional_usd=qty * entry,
        leverage=1.0,
        risk_usd=qty * abs(entry - stop),
        entry_price=entry,
        stop_price=stop,
    )


def test_long_plan_has_three_orders(setup_event: SetupEvent) -> None:
    orders = planner.plan(setup_event, _sizing(100.0, 99.0))
    roles = [o.role for o in orders]
    assert roles == ["entry", "stop", "take_profit"]


def test_long_sides(setup_event: SetupEvent) -> None:
    orders = planner.plan(setup_event, _sizing(100.0, 99.0))
    assert orders[0].side == "buy"
    assert orders[1].side == "sell" and orders[1].reduce_only
    assert orders[2].side == "sell" and orders[2].reduce_only


def test_short_sides(short_event: SetupEvent) -> None:
    orders = planner.plan(short_event, _sizing(100.0, 101.0))
    assert orders[0].side == "sell"
    assert orders[1].side == "buy" and orders[1].reduce_only
    assert orders[2].side == "buy" and orders[2].reduce_only


def test_long_take_profit_at_2r(setup_event: SetupEvent) -> None:
    orders = planner.plan(setup_event, _sizing(100.0, 99.0), rr_ratio=2.0)
    tp = next(o for o in orders if o.role == "take_profit")
    assert tp.price == 102.0  # 100 + (100-99)*2


def test_short_take_profit_at_2r(short_event: SetupEvent) -> None:
    orders = planner.plan(short_event, _sizing(100.0, 101.0), rr_ratio=2.0)
    tp = next(o for o in orders if o.role == "take_profit")
    assert tp.price == 98.0  # 100 - (101-100)*2


def test_stop_is_stop_market(setup_event: SetupEvent) -> None:
    orders = planner.plan(setup_event, _sizing(100.0, 99.0))
    stop = next(o for o in orders if o.role == "stop")
    assert stop.type == "stop_market"
