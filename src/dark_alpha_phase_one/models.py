from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class ProposalCard:
    symbol: str
    strategy: str
    side: str
    entry: float
    stop: float
    leverage_suggest: int
    position_usdt: float
    max_risk_usdt: float
    ttl_minutes: int
    rationale: str
    created_at: str
    priority: int
    confidence: float

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        leverage_suggest: int,
        position_usdt: float,
        max_risk_usdt: float,
        ttl_minutes: int,
        rationale: str,
        strategy: str = "vol_breakout_card",
        priority: int = 0,
        confidence: float = 0.0,
    ) -> "ProposalCard":
        return cls(
            symbol=symbol,
            strategy=strategy,
            side=side,
            entry=entry,
            stop=stop,
            leverage_suggest=leverage_suggest,
            position_usdt=position_usdt,
            max_risk_usdt=max_risk_usdt,
            ttl_minutes=ttl_minutes,
            rationale=rationale,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
            priority=priority,
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
