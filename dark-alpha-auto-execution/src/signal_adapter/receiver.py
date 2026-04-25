"""FastAPI HTTP receiver for Dark Alpha postback signals.

Dark Alpha's POSTBACK_URL should point here:  http://127.0.0.1:8765/signal

Flow:
  POST /signal (ProposalCardPayload JSON)
    → validate with Pydantic
    → check kill switch (reject immediately if active)
    → translate to SetupEvent
    → persist to setup_events table
    → audit log entry
    → return 200 + event_id
"""

import json
import time

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from pydantic import ValidationError

import bootstrap  # noqa: F401  — must run before any os.getenv read
from execution.router import ModeRouter
from safety.audit import (
    SIGNAL_ACCEPTED,
    SIGNAL_RECEIVED,
    SIGNAL_REJECTED,
    log_event,
)
from safety.kill_switch import get_kill_switch
from signal_adapter.schemas import ProposalCardPayload
from signal_adapter.translator import proposal_card_to_setup_event
from storage.db import get_db
from strategy.pipeline import run as run_strategy
from strategy.schemas import ExecutionTicket, Rejection

log = structlog.get_logger(__name__)

app = FastAPI(title="Dark Alpha Signal Receiver", version="0.1.0")


@app.post("/signal", status_code=status.HTTP_200_OK)
async def receive_signal(request: Request) -> dict[str, str]:
    t0 = time.monotonic()

    raw = await request.body()
    try:
        payload_dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("signal.parse_error", error=str(exc))
        raise HTTPException(status_code=400, detail="invalid JSON") from exc

    try:
        card = ProposalCardPayload(**payload_dict)
    except ValidationError as exc:
        log.warning("signal.validation_error", errors=exc.errors(), payload=payload_dict)
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    event = proposal_card_to_setup_event(card)

    # Audit: signal received
    log_event(SIGNAL_RECEIVED, source="receiver", decision="received", event_id=event.event_id)

    # Kill switch check — synchronous, instant
    ks = get_kill_switch()
    if ks.is_active():
        log.warning("signal.rejected_kill_switch", event_id=event.event_id)
        log_event(
            SIGNAL_REJECTED,
            source="receiver",
            decision="reject",
            reason="kill_switch_active",
            event_id=event.event_id,
        )
        raise HTTPException(status_code=503, detail="kill switch is active — system halted")

    with get_db() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO setup_events
                (event_id, timestamp, symbol, setup_type, payload, received_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                event.event_id,
                event.timestamp,
                event.symbol,
                event.setup_type,
                event.model_dump_json(),
            ),
        )
        db.commit()

    log_event(SIGNAL_ACCEPTED, source="receiver", decision="accept", event_id=event.event_id)

    # Strategy pipeline — build a ticket and dispatch to the broker (shadow mode)
    try:
        outcome = run_strategy(event)
    except Exception as exc:  # noqa: BLE001
        log.error("strategy.pipeline_failed", event_id=event.event_id, error=str(exc))
        outcome = None

    ticket_id: str | None = None
    if isinstance(outcome, ExecutionTicket):
        try:
            router = ModeRouter()
            router.dispatch(outcome)
            ticket_id = outcome.ticket_id
        except NotImplementedError:
            log.warning("strategy.live_mode_not_implemented", event_id=event.event_id)
        except Exception as exc:  # noqa: BLE001
            log.error("strategy.dispatch_failed", event_id=event.event_id, error=str(exc))
    elif isinstance(outcome, Rejection):
        from execution.position_manager import PositionManager

        PositionManager().persist_rejection(outcome)
        log_event(
            SIGNAL_REJECTED,
            source="strategy",
            decision="reject",
            reason=f"{outcome.stage}:{outcome.reason}",
            event_id=event.event_id,
        )

    latency_ms = int((time.monotonic() - t0) * 1000)
    log.info(
        "signal.received",
        event_id=event.event_id,
        symbol=event.symbol,
        direction=event.direction,
        ranking_score=event.ranking_score,
        regime=event.regime,
        latency_ms=latency_ms,
    )

    response: dict[str, str] = {"event_id": event.event_id, "symbol": event.symbol}
    if ticket_id:
        response["ticket_id"] = ticket_id
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    ks = get_kill_switch()
    return {"status": "halted" if ks.is_active() else "ok"}
