"""Pydantic models for the Strategy Layer."""

from typing import Literal

from pydantic import BaseModel, Field


class PlannedOrder(BaseModel):
    """One concrete order to place (entry, stop, or take profit)."""

    role: Literal["entry", "stop", "take_profit"]
    side: Literal["buy", "sell"]
    type: Literal["limit", "market", "stop_market"]
    symbol: str
    price: float | None  # None for market orders
    quantity: float
    reduce_only: bool = False


class ExecutionTicket(BaseModel):
    """Canonical contract between Strategy Layer and Execution Layer.

    Spec Section 4.2 — created only when validator + sizer + risk_gate all accept.
    """

    ticket_id: str
    source_event_id: str
    symbol: str
    direction: Literal["long", "short"]
    regime: str
    ranking_score: float
    shadow_mode: bool
    gate: str  # "gate1" | "gate2" | "gate3"
    entry_price: float
    stop_price: float
    take_profit_price: float | None
    quantity: float
    notional_usd: float
    leverage: float
    risk_usd: float
    orders: list[PlannedOrder]
    created_at: str  # ISO8601 UTC
    metadata: dict[str, object] = Field(default_factory=dict)


class Rejection(BaseModel):
    """Returned when a signal fails to become a ticket."""

    source_event_id: str
    stage: Literal["validator", "sizer", "planner", "risk_gate"]
    reason: str
    detail: str = ""
