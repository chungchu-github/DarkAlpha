"""Mode router — dispatches a ticket to the paper broker (shadow) or live (Phase 5+).

Live dispatch is intentionally unimplemented in Phase 3. Attempting live mode
raises NotImplementedError so a misconfigured mode=live cannot silently run.
"""

from pathlib import Path

import structlog

from strategy.schemas import ExecutionTicket

from .paper_broker import PaperBroker
from .position_manager import PositionManager

log = structlog.get_logger(__name__)


class ModeRouter:
    def __init__(
        self,
        broker: PaperBroker | None = None,
        manager: PositionManager | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._broker = broker or PaperBroker()
        self._manager = manager or PositionManager(db_path=db_path)

    def dispatch(self, ticket: ExecutionTicket) -> str:
        """Route the ticket. Returns position_id once the entry has filled.

        In shadow mode: simulates the entry fill immediately. Stop/TP are
        resolved later by the fill tracker against historical or live prices.
        """
        if not ticket.shadow_mode:
            raise NotImplementedError("live broker arrives in Phase 5")

        self._manager.persist_ticket(ticket, status="accepted")
        fill = self._broker.simulate_entry(ticket)
        position_id = self._manager.open_position(ticket, fill)
        log.info(
            "router.dispatched_shadow",
            ticket_id=ticket.ticket_id,
            position_id=position_id,
            fill_price=fill.price,
            fee_usd=fill.fee_usd,
        )
        return position_id
