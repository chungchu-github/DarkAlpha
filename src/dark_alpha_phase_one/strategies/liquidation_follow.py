from __future__ import annotations

from dataclasses import dataclass

from dark_alpha_phase_one.calculations import calculate_position_usdt
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.models import ProposalCard
from dark_alpha_phase_one.strategies.base import Strategy


@dataclass(frozen=True)
class LiquidationFollowStrategy(Strategy):
    oi_delta_pct_threshold: float
    leverage_suggest: int
    max_risk_usdt: float
    ttl_minutes: int
    priority: int = 0
    name: str = "liquidation_follow"

    def generate(self, signal_context: SignalContext) -> ProposalCard | None:
        if signal_context.open_interest_delta_15m is None:
            return None

        trend_dir = 1 if signal_context.return_5m > 0 else -1
        funding_dir = 1 if signal_context.funding_rate > 0 else -1
        aligned = trend_dir == funding_dir
        trigger = (
            signal_context.open_interest_delta_15m >= self.oi_delta_pct_threshold
            and abs(signal_context.return_5m) >= 0.01
            and aligned
        )
        if not trigger:
            return None

        side = "LONG" if signal_context.return_5m > 0 else "SHORT"
        entry = signal_context.price
        stop = entry - (1.5 * signal_context.atr_15m) if side == "LONG" else entry + (1.5 * signal_context.atr_15m)
        position_usdt = calculate_position_usdt(entry=entry, stop=stop, max_risk_usdt=self.max_risk_usdt)
        confidence = min(
            100.0,
            40.0
            + (signal_context.open_interest_delta_15m / max(self.oi_delta_pct_threshold, 1e-9)) * 25
            + abs(signal_context.return_5m) * 1000,
        )
        rationale = (
            f"oi_delta_15m={signal_context.open_interest_delta_15m:.2%}, funding={signal_context.funding_rate:.6f}, "
            f"return_5m={signal_context.return_5m:.2%}, aligned_trend={aligned}"
        )
        return ProposalCard.create(
            symbol=signal_context.symbol,
            side=side,
            entry=entry,
            stop=stop,
            leverage_suggest=self.leverage_suggest,
            position_usdt=position_usdt,
            max_risk_usdt=self.max_risk_usdt,
            ttl_minutes=self.ttl_minutes,
            rationale=rationale,
            strategy=self.name,
            priority=self.priority,
            confidence=confidence,
        )
