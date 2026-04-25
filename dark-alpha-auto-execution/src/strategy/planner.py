"""Order planner — turn (SetupEvent + SizingResult) into concrete orders.

Gate 1/2 order shape (spec Section 4.3.4):
  - entry: limit at trigger.price_level
  - stop:  stop_market at invalidation.price_level, reduce_only=True
  - take_profit: limit at 2R by default, reduce_only=True (optional)
"""

from signal_adapter.schemas import SetupEvent

from .schemas import PlannedOrder
from .sizer import SizingResult

_DEFAULT_RR = 2.0  # reward:risk ratio for take-profit


def plan(
    event: SetupEvent,
    sizing: SizingResult,
    rr_ratio: float = _DEFAULT_RR,
) -> list[PlannedOrder]:
    entry = sizing.entry_price
    stop = sizing.stop_price
    qty = sizing.quantity
    direction = event.direction

    if direction == "long":
        entry_side: str = "buy"
        exit_side: str = "sell"
        tp_price = entry + (entry - stop) * rr_ratio
    else:
        entry_side = "sell"
        exit_side = "buy"
        tp_price = entry - (stop - entry) * rr_ratio

    orders: list[PlannedOrder] = [
        PlannedOrder(
            role="entry",
            side=entry_side,  # type: ignore[arg-type]
            type="limit",
            symbol=event.symbol,
            price=entry,
            quantity=qty,
            reduce_only=False,
        ),
        PlannedOrder(
            role="stop",
            side=exit_side,  # type: ignore[arg-type]
            type="stop_market",
            symbol=event.symbol,
            price=stop,
            quantity=qty,
            reduce_only=True,
        ),
        PlannedOrder(
            role="take_profit",
            side=exit_side,  # type: ignore[arg-type]
            type="limit",
            symbol=event.symbol,
            price=round(tp_price, 8),
            quantity=qty,
            reduce_only=True,
        ),
    ]
    return orders
