"""Pydantic models for Dark Alpha signal pipeline."""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class TriggerInfo(BaseModel):
    condition: str
    price_level: float
    timeframe: str = "15m"


class InvalidationInfo(BaseModel):
    condition: str
    price_level: float


class SetupEvent(BaseModel):
    """Canonical event contract between Signal Layer and Strategy Layer.

    Fields map 1-to-1 with spec Section 4.1. Strategy Layer reads ONLY this model —
    never the raw ProposalCard payload.
    """

    event_id: str
    timestamp: str  # ISO8601 UTC
    symbol: str  # e.g. "BTCUSDT-PERP"
    setup_type: str  # "active" | "alert" | "no_action"
    direction: str | None  # "long" | "short" | None
    regime: str  # Dark Alpha strategy name used as regime proxy
    today_decision: str  # rationale text from Dark Alpha
    ranking_score: float  # 0–10 (mapped from confidence 0–100)
    trigger: TriggerInfo | None
    invalidation: InvalidationInfo | None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ranking_score")
    @classmethod
    def clamp_ranking_score(cls, v: float) -> float:
        return max(0.0, min(10.0, v))

    @field_validator("setup_type")
    @classmethod
    def validate_setup_type(cls, v: str) -> str:
        allowed = {"active", "alert", "no_action"}
        if v not in allowed:
            raise ValueError(f"setup_type must be one of {allowed}, got {v!r}")
        return v

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str | None) -> str | None:
        if v is not None and v not in {"long", "short"}:
            raise ValueError(f"direction must be 'long', 'short', or None, got {v!r}")
        return v


class ProposalCardPayload(BaseModel):
    """Raw JSON payload emitted by Dark Alpha's postback_client.

    Matches the to_dict() output of ProposalCard + trace_id added by service.py.
    This model is used only for input validation in the receiver — it is never
    passed downstream. Only SetupEvent crosses the adapter boundary.
    """

    symbol: str
    strategy: str
    side: str  # "LONG" | "SHORT"
    entry: float
    stop: float
    leverage_suggest: int
    position_usdt: float
    max_risk_usdt: float
    ttl_minutes: int
    rationale: str
    created_at: str
    priority: int
    confidence: float  # 0–100
    take_profit: float | None = None
    invalid_condition: str = ""
    risk_level: str = "medium"
    oi_status: str = "fresh"
    data_health: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
