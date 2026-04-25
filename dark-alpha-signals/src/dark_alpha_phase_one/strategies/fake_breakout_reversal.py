from __future__ import annotations

from dataclasses import dataclass

from dark_alpha_phase_one.calculations import calculate_position_usdt
from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.models import ProposalCard
from dark_alpha_phase_one.strategies.base import Strategy


@dataclass(frozen=True)
class FakeBreakoutReversalStrategy(Strategy):
    sweep_pct: float
    wick_body_ratio: float
    stop_buffer_atr: float
    min_atr_pct: float
    leverage_suggest: int
    max_risk_usdt: float
    ttl_minutes: int
    priority: int = 0
    max_kline_age_seconds: int = 90
    name: str = "fake_breakout_reversal"

    def generate(self, signal_context: SignalContext) -> ProposalCard | None:
        if signal_context.last_kline_close_ts is None:
            return None
        age = (signal_context.timestamp - signal_context.last_kline_close_ts).total_seconds()
        if age > self.max_kline_age_seconds:
            return None

        if signal_context.atr_15m < (self.min_atr_pct * signal_context.price):
            return None

        if len(signal_context.klines_1m) < 21:
            return None

        latest = signal_context.klines_1m[-1]
        recent_20 = signal_context.klines_1m[-21:-1]
        prev_20m_high = max(c.high for c in recent_20)
        prev_20m_low = min(c.low for c in recent_20)

        body = abs(latest.close - latest.open)
        body = max(body, 1e-9)
        upper_wick = max(0.0, latest.high - max(latest.open, latest.close))
        lower_wick = max(0.0, min(latest.open, latest.close) - latest.low)

        sweep_high = (
            latest.high > prev_20m_high * (1 + self.sweep_pct)
            and latest.close < prev_20m_high
            and (upper_wick / body) >= self.wick_body_ratio
        )
        sweep_low = (
            latest.low < prev_20m_low * (1 - self.sweep_pct)
            and latest.close > prev_20m_low
            and (lower_wick / body) >= self.wick_body_ratio
        )

        if not (sweep_high or sweep_low):
            return None

        side = "SHORT" if sweep_high else "LONG"
        entry = signal_context.price
        if sweep_high:
            stop = latest.high + (self.stop_buffer_atr * signal_context.atr_15m)
            sweep_pct_val = (latest.high / prev_20m_high) - 1
            wick_ratio = upper_wick / body
            reclaim_level = prev_20m_high
        else:
            stop = latest.low - (self.stop_buffer_atr * signal_context.atr_15m)
            sweep_pct_val = 1 - (latest.low / prev_20m_low)
            wick_ratio = lower_wick / body
            reclaim_level = prev_20m_low

        position_usdt = calculate_position_usdt(entry=entry, stop=stop, max_risk_usdt=self.max_risk_usdt)
        confidence = min(100.0, 50.0 + (wick_ratio * 10.0) + (sweep_pct_val * 10000.0))
        rationale = (
            f"prev_20m_high={prev_20m_high:.4f}, prev_20m_low={prev_20m_low:.4f}, "
            f"sweep_pct={sweep_pct_val:.4%}, wick_body={wick_ratio:.2f}, reclaim={reclaim_level:.4f} -> {side}"
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
