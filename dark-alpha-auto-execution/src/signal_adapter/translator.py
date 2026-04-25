"""ProposalCard → SetupEvent translation.

Pure functions only — no I/O, no side effects. Easy to unit test in isolation.
"""

from ulid import ULID

from signal_adapter.schemas import (
    InvalidationInfo,
    ProposalCardPayload,
    SetupEvent,
    TriggerInfo,
)


def _normalize_symbol(raw: str) -> str:
    """Append -PERP suffix if not already present.

    Dark Alpha emits bare symbols like "BTCUSDT"; execution layer uses "BTCUSDT-PERP"
    to make exchange/instrument type explicit.
    """
    if raw.endswith("-PERP") or raw.endswith("_PERP"):
        return raw
    return f"{raw}-PERP"


def _map_direction(side: str) -> str:
    """Normalize Dark Alpha side strings to spec direction values."""
    return "long" if side.upper() == "LONG" else "short"


def _map_regime(strategy: str) -> str:
    """Map Dark Alpha strategy name to regime string.

    Strategy names are used directly as regime identifiers so that
    validator.yaml's blocked_regimes can reference them by name.
    """
    return strategy.lower()


def proposal_card_to_setup_event(payload: ProposalCardPayload) -> SetupEvent:
    """Translate a validated ProposalCardPayload into a SetupEvent.

    Field mapping (spec Section 4.1):
      event_id        ← trace_id (or fresh ULID if trace_id absent)
      timestamp       ← created_at
      symbol          ← symbol + "-PERP" normalization
      setup_type      ← always "active" (ProposalCard is never emitted unless active)
      direction       ← "long" if side=="LONG" else "short"
      regime          ← strategy (used as regime proxy)
      today_decision  ← rationale
      ranking_score   ← confidence / 10  (0–100 → 0–10)
      trigger         ← entry price + rationale + default timeframe "15m"
      invalidation    ← stop price
      metadata        ← leverage_suggest, position_usdt, max_risk_usdt,
                         ttl_minutes, priority, oi_status
    """
    event_id = payload.trace_id if payload.trace_id else str(ULID())

    return SetupEvent(
        event_id=event_id,
        timestamp=payload.created_at,
        symbol=_normalize_symbol(payload.symbol),
        setup_type="active",
        direction=_map_direction(payload.side),
        regime=_map_regime(payload.strategy),
        today_decision=payload.rationale,
        ranking_score=payload.confidence / 10.0,
        trigger=TriggerInfo(
            condition=payload.rationale,
            price_level=payload.entry,
            timeframe="15m",
        ),
        invalidation=InvalidationInfo(
            condition=f"stop hit at {payload.stop}",
            price_level=payload.stop,
        ),
        metadata={
            "leverage_suggest": payload.leverage_suggest,
            "position_usdt": payload.position_usdt,
            "max_risk_usdt": payload.max_risk_usdt,
            "ttl_minutes": payload.ttl_minutes,
            "priority": payload.priority,
            "oi_status": payload.oi_status,
        },
    )
