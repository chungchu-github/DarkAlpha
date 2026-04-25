from __future__ import annotations

from dataclasses import dataclass

from dark_alpha_phase_one.calculations import calculate_position_usdt
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.models import ProposalCard
from dark_alpha_phase_one.strategies.base import Strategy


@dataclass(frozen=True)
class VolBreakoutStrategy(Strategy):
    return_threshold: float
    atr_spike_multiplier: float
    leverage_suggest: int
    max_risk_usdt: float
    ttl_minutes: int
    priority: int = 0
    name: str = "vol_breakout_card"

    def generate(self, signal_context: SignalContext) -> ProposalCard | None:
        return_trigger = abs(signal_context.return_5m) > self.return_threshold
        atr_trigger = signal_context.atr_15m > (
            signal_context.atr_15m_baseline * self.atr_spike_multiplier
        )

        if not (return_trigger or atr_trigger):
            return None

        side = "LONG" if signal_context.return_5m >= 0 else "SHORT"
        entry = signal_context.price
        stop = entry - (1.2 * signal_context.atr_15m) if side == "LONG" else entry + (1.2 * signal_context.atr_15m)
        position_usdt = calculate_position_usdt(
            entry=entry,
            stop=stop,
            max_risk_usdt=self.max_risk_usdt,
        )
        score_return = abs(signal_context.return_5m) / max(self.return_threshold, 1e-9)
        score_atr = signal_context.atr_15m / max(signal_context.atr_15m_baseline, 1e-9)
        confidence = min(100.0, 40.0 + (score_return * 20.0) + (score_atr * 10.0))

        rationale = (
            f"triggered: return_5m={signal_context.return_5m:.4%} (th={self.return_threshold:.2%}), "
            f"atr_15m={signal_context.atr_15m:.4f} vs baseline={signal_context.atr_15m_baseline:.4f}"
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
