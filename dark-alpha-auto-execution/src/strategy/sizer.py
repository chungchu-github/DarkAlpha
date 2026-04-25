"""Position sizer — fixed fractional risk (spec Section 4.3.3).

Formula:
    risk_usd     = equity * risk_per_trade_pct
    stop_dist    = abs(entry - stop)
    quantity     = risk_usd / stop_dist
    notional     = quantity * entry
    leverage_req = notional / equity

Rejections:
  - stop_distance_too_small (division-by-zero protection)
  - leverage_req > max_leverage (Gate ceiling)
  - notional < min_notional_usd (below exchange minimum)
  - notional > hard_notional_cap_usd
"""

from dataclasses import dataclass
from math import floor

import structlog

from signal_adapter.schemas import SetupEvent

from .config import sizer_config
from .schemas import Rejection

log = structlog.get_logger(__name__)


@dataclass
class SizingResult:
    quantity: float
    notional_usd: float
    leverage: float
    risk_usd: float
    entry_price: float
    stop_price: float


def size(
    event: SetupEvent,
    equity_usd: float,
    gate: str = "gate1",
) -> SizingResult | Rejection:
    cfg = sizer_config(gate)
    risk_pct = float(cfg.get("risk_per_trade_pct", 0.005))
    max_lev = float(cfg.get("max_leverage", 3.0))
    hard_cap = float(cfg.get("hard_notional_cap_usd", 100_000))
    min_notional = float(cfg.get("min_notional_usd", 10.0))
    qty_step = float(cfg.get("min_qty_step", 0.001))

    if event.trigger is None or event.invalidation is None:
        return _reject(event, "missing_levels", "sizer requires trigger/invalidation")

    entry = event.trigger.price_level
    stop = event.invalidation.price_level
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return _reject(event, "stop_distance_too_small", f"entry={entry} stop={stop}")

    risk_usd = equity_usd * risk_pct
    raw_qty = risk_usd / stop_dist
    qty = _round_step(raw_qty, qty_step)
    if qty <= 0:
        return _reject(
            event,
            "qty_below_step",
            f"raw={raw_qty} step={qty_step}",
        )

    notional = qty * entry
    leverage = notional / equity_usd if equity_usd > 0 else float("inf")

    if leverage > max_lev:
        return _reject(
            event,
            "leverage_cap_exceeded",
            f"required={leverage:.2f} max={max_lev:.2f}",
        )
    if notional < min_notional:
        return _reject(event, "below_min_notional", f"{notional:.2f} < {min_notional}")
    if notional > hard_cap:
        return _reject(event, "above_hard_cap", f"{notional:.2f} > {hard_cap}")

    return SizingResult(
        quantity=qty,
        notional_usd=notional,
        leverage=leverage,
        risk_usd=risk_usd,
        entry_price=entry,
        stop_price=stop,
    )


def _round_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return floor(qty / step) * step


def _reject(event: SetupEvent, reason: str, detail: str) -> Rejection:
    log.info("sizer.reject", event_id=event.event_id, reason=reason, detail=detail)
    return Rejection(
        source_event_id=event.event_id,
        stage="sizer",
        reason=reason,
        detail=detail,
    )
