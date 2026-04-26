from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    take_profit: float | None = None
    invalid_condition: str = ""
    risk_level: str = "medium"
    oi_status: str = "fresh"
    data_health: dict[str, object] = field(default_factory=dict)

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
        take_profit: float | None = None,
        invalid_condition: str = "",
        risk_level: str = "medium",
        oi_status: str = "fresh",
        data_health: dict[str, object] | None = None,
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
            take_profit=take_profit,
            invalid_condition=invalid_condition,
            risk_level=risk_level,
            oi_status=oi_status,
            data_health=data_health or {},
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
