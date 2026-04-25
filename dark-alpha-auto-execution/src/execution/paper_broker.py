"""Paper broker — simulates fills against configurable slippage + fees.

Used in shadow mode (Gate 1 & Gate 4 backtest). Never touches the network.

Fill model (spec Section 6.1):
  - limit entry: fills at price * (1 ± slippage_bps) if price is 'reachable'
  - stop_market: fills at stop * (1 ± slippage_bps)
  - maker fee applied to limit orders; taker fee to stop_market fills
"""

from dataclasses import dataclass

from strategy.schemas import ExecutionTicket, PlannedOrder

_DEFAULT_SLIPPAGE_BPS = 2.0  # 0.02%
_DEFAULT_MAKER_FEE_BPS = 2.0  # Binance USDT-M futures VIP0 maker
_DEFAULT_TAKER_FEE_BPS = 4.0  # Binance USDT-M futures VIP0 taker


@dataclass
class Fill:
    order_role: str  # entry | stop | take_profit
    side: str
    symbol: str
    price: float
    quantity: float
    fee_usd: float
    reduce_only: bool


class PaperBroker:
    """Simulates order fills. Deterministic for a given input price."""

    def __init__(
        self,
        slippage_bps: float = _DEFAULT_SLIPPAGE_BPS,
        maker_fee_bps: float = _DEFAULT_MAKER_FEE_BPS,
        taker_fee_bps: float = _DEFAULT_TAKER_FEE_BPS,
    ) -> None:
        self._slip = slippage_bps / 10_000
        self._maker = maker_fee_bps / 10_000
        self._taker = taker_fee_bps / 10_000

    def simulate_entry(self, ticket: ExecutionTicket) -> Fill:
        """Assume the entry limit fills at the requested price plus slippage."""
        order = next(o for o in ticket.orders if o.role == "entry")
        assert order.price is not None
        price = self._apply_slippage(order.price, order.side)
        fee = self._fee(price, order.quantity, self._maker)
        return Fill(
            order_role="entry",
            side=order.side,
            symbol=order.symbol,
            price=price,
            quantity=order.quantity,
            fee_usd=fee,
            reduce_only=False,
        )

    def simulate_exit(
        self,
        ticket: ExecutionTicket,
        reason: str,
        mark_price: float | None = None,
    ) -> Fill:
        """Simulate closing the position.

        reason:
          stop_loss     → stop_market at stop_price ± slippage (taker fee)
          take_profit   → limit at tp_price ± slippage (maker fee)
          manual / kill → taker at mark_price (defaults to entry)
        """
        exit_order = self._pick_exit_order(ticket, reason)
        if mark_price is not None:
            base_price = mark_price
            fee_bps = self._taker
        elif exit_order is not None and exit_order.price is not None:
            base_price = exit_order.price
            fee_bps = self._maker if exit_order.type == "limit" else self._taker
        else:
            base_price = ticket.entry_price
            fee_bps = self._taker

        side = exit_order.side if exit_order else ("sell" if ticket.direction == "long" else "buy")
        price = self._apply_slippage(base_price, side)
        fee = self._fee(price, ticket.quantity, fee_bps)
        return Fill(
            order_role=exit_order.role if exit_order else "stop",
            side=side,
            symbol=ticket.symbol,
            price=price,
            quantity=ticket.quantity,
            fee_usd=fee,
            reduce_only=True,
        )

    @staticmethod
    def _pick_exit_order(ticket: ExecutionTicket, reason: str) -> PlannedOrder | None:
        role = "take_profit" if reason == "take_profit" else "stop"
        return next((o for o in ticket.orders if o.role == role), None)

    def _apply_slippage(self, price: float, side: str) -> float:
        if side == "buy":
            return price * (1 + self._slip)
        return price * (1 - self._slip)

    @staticmethod
    def _fee(price: float, qty: float, rate: float) -> float:
        return price * qty * rate
