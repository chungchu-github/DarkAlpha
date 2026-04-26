"""Mode router — dispatches a ticket to the paper broker (shadow) or live."""

from pathlib import Path

import structlog

from strategy.schemas import ExecutionTicket

from .binance_testnet_broker import BinanceFuturesBroker, LiveBrokerError
from .live_safety import (
    LivePreflightError,
    assert_live_pre_order_health,
    order_idempotency_statuses,
    reserve_order_idempotency,
)
from .paper_broker import PaperBroker
from .position_manager import PositionManager

log = structlog.get_logger(__name__)


class ModeRouter:
    def __init__(
        self,
        broker: PaperBroker | None = None,
        live_broker: BinanceFuturesBroker | None = None,
        manager: PositionManager | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._broker = broker or PaperBroker()
        self._live_broker = live_broker or BinanceFuturesBroker()
        self._manager = manager or PositionManager(db_path=db_path)

    def dispatch(self, ticket: ExecutionTicket) -> str:
        """Route the ticket.

        In shadow mode: creates a pending position. The evaluator fills it only
        if market price touches the entry before TTL expiry.

        In live mode: submits planned orders and returns a live ticket reference.
        Exchange fills are reconciled by live order sync/reconciliation.
        """
        if not ticket.shadow_mode:
            # Pre-order health gate (Task 3): kill switch, config, symbol caps,
            # public price freshness, account/position + open-orders queries.
            # Must run BEFORE we touch local state or the broker.
            assert_live_pre_order_health(
                ticket,
                broker_client=getattr(self._live_broker, "client", None),
            )

            # Task 8 — refuse to re-dispatch a ticket that already has any
            # idempotency state. The deterministic clientOrderId derived from
            # ticket_id+role+side means that a second submit would collide on
            # the exchange anyway; surfacing the precondition here means the
            # broker is never called twice for the same ticket and forces an
            # explicit reconcile path for ambiguous reservations.
            existing = order_idempotency_statuses(ticket, db_path=self._manager.db_path)
            if existing:
                statuses = set(existing.values())
                if statuses & {"submitted", "acknowledged", "filled"}:
                    raise LivePreflightError("duplicate_live_ticket_already_submitted")
                if "reserved" in statuses:
                    # Submission outcome unknown (timeout / partial). Sync must
                    # heal these rows before another attempt is allowed.
                    raise LivePreflightError("ticket_reserved_reconcile_required")
                if statuses & {"cancelled", "rejected"}:
                    raise LivePreflightError("ticket_already_terminated")

            self._manager.persist_ticket(ticket, status="accepted")
            for order in ticket.orders:
                reserve_order_idempotency(ticket, order, db_path=self._manager.db_path)
            try:
                acks = self._live_broker.submit_ticket(ticket)
            except LiveBrokerError as exc:
                self._manager.update_ticket_status(ticket.ticket_id, "rejected", str(exc))
                raise
            for ack in acks:
                self._manager.record_live_order_ack(ticket, ack)
            log.info(
                "router.dispatched_live_testnet",
                ticket_id=ticket.ticket_id,
                symbol=ticket.symbol,
                orders=len(acks),
            )
            return f"live:{ticket.ticket_id}"

        self._manager.persist_ticket(ticket, status="accepted")
        position_id = self._manager.create_pending_position(ticket)
        log.info(
            "router.dispatched_shadow",
            ticket_id=ticket.ticket_id,
            position_id=position_id,
            status="pending",
        )
        return position_id
