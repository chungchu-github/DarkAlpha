from __future__ import annotations

from dataclasses import dataclass

from dark_alpha_phase_one.calculations import calculate_position_usdt
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.models import ProposalCard
from dark_alpha_phase_one.strategies.base import Strategy


@dataclass(frozen=True)
class FundingOiSkewStrategy(Strategy):
    funding_extreme: float
    oi_zscore_threshold: float
    leverage_suggest: int
    max_risk_usdt: float
    ttl_minutes: int
    priority: int = 0
    name: str = "funding_oi_skew"

    def generate(self, signal_context: SignalContext) -> ProposalCard | None:
        if signal_context.open_interest_zscore_15m is None:
            return None
        funding = signal_context.funding_rate
        crowded_long = funding > 0 and signal_context.return_5m > 0
        crowded_short = funding < 0 and signal_context.return_5m < 0

        if abs(funding) < self.funding_extreme:
            return None
        if signal_context.open_interest_zscore_15m < self.oi_zscore_threshold:
            return None
        if not (crowded_long or crowded_short):
            return None

        side = "SHORT" if crowded_long else "LONG"
        entry = signal_context.price
        stop = entry + signal_context.atr_15m if side == "SHORT" else entry - signal_context.atr_15m
        position_usdt = calculate_position_usdt(entry=entry, stop=stop, max_risk_usdt=self.max_risk_usdt)
        confidence = min(
            100.0,
            45.0 + (abs(funding) / max(self.funding_extreme, 1e-9)) * 20 + signal_context.open_interest_zscore_15m * 10,
        )
        rationale = (
            f"funding={funding:.6f}, oi_zscore_15m={signal_context.open_interest_zscore_15m:.2f}, "
            f"crowded={'long' if crowded_long else 'short'} -> contrarian {side}"
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
