"""Strategy pipeline — SetupEvent → ExecutionTicket | Rejection.

Thin orchestrator that chains validator → sizer → planner → risk_gate.
The receiver/scheduler calls this; the execution layer consumes the ticket.
"""

from datetime import UTC, datetime
from pathlib import Path

import structlog
from ulid import ULID

from signal_adapter.schemas import SetupEvent

from . import planner, validator
from .config import main_config, sizer_config
from .risk_gate import RiskGate
from .schemas import ExecutionTicket, Rejection
from .sizer import SizingResult, size

log = structlog.get_logger(__name__)


def _gate_name() -> str:
    gate = main_config().get("gate", 1)
    return f"gate{gate}"


def _shadow_mode() -> bool:
    return str(main_config().get("mode", "shadow")).lower() == "shadow"


def run(
    event: SetupEvent,
    equity_usd: float | None = None,
    risk_gate: RiskGate | None = None,
    db_path: Path | None = None,
) -> ExecutionTicket | Rejection:
    gate = _gate_name()

    if equity_usd is None:
        equity_usd = float(sizer_config(gate).get("starting_equity_usd", 10_000.0))

    rejection = validator.validate(event)
    if rejection is not None:
        return rejection

    sizing = size(event, equity_usd=equity_usd, gate=gate)
    if isinstance(sizing, Rejection):
        return sizing

    gate_check = (risk_gate or RiskGate(db_path=db_path)).check(event, equity_usd)
    if gate_check is not None:
        return gate_check

    return _assemble_ticket(event, sizing, gate)


def _assemble_ticket(
    event: SetupEvent,
    sizing: SizingResult,
    gate: str,
) -> ExecutionTicket:
    orders = planner.plan(event, sizing)
    assert event.direction in {"long", "short"}
    ticket = ExecutionTicket(
        ticket_id=str(ULID()),
        source_event_id=event.event_id,
        symbol=event.symbol,
        direction=event.direction,  # type: ignore[arg-type]
        regime=event.regime,
        ranking_score=event.ranking_score,
        shadow_mode=_shadow_mode(),
        gate=gate,
        entry_price=sizing.entry_price,
        stop_price=sizing.stop_price,
        take_profit_price=next(
            (o.price for o in orders if o.role == "take_profit"),
            None,
        ),
        quantity=sizing.quantity,
        notional_usd=sizing.notional_usd,
        leverage=sizing.leverage,
        risk_usd=sizing.risk_usd,
        orders=orders,
        created_at=datetime.now(tz=UTC).isoformat(),
        metadata={"event_metadata": event.metadata},
    )
    log.info(
        "strategy.ticket_created",
        ticket_id=ticket.ticket_id,
        event_id=event.event_id,
        symbol=ticket.symbol,
        direction=ticket.direction,
        quantity=ticket.quantity,
        notional_usd=ticket.notional_usd,
        gate=gate,
    )
    return ticket
