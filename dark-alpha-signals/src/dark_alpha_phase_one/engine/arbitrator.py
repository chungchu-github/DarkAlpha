from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.models import ProposalCard


@dataclass(frozen=True)
class ArbitratorConfig:
    dedupe_window_seconds: int
    entry_similar_pct: float
    stop_similar_pct: float


class Arbitrator:
    def __init__(self, config: ArbitratorConfig, last_sent_lookup) -> None:
        self.config = config
        self.last_sent_lookup = last_sent_lookup

    def choose_best(self, cards: list[ProposalCard], ctx: SignalContext) -> ProposalCard | None:
        if not cards:
            return None

        logging.info(
            "arbitration candidates symbol=%s cards=%s",
            ctx.symbol,
            [
                {
                    "strategy": c.strategy,
                    "side": c.side,
                    "entry": c.entry,
                    "stop": c.stop,
                    "priority": c.priority,
                    "confidence": c.confidence,
                }
                for c in cards
            ],
        )

        now = ctx.timestamp if ctx.timestamp.tzinfo else ctx.timestamp.replace(tzinfo=timezone.utc)
        last_sent = self.last_sent_lookup(ctx.symbol)
        if last_sent is not None and (now - last_sent).total_seconds() <= self.config.dedupe_window_seconds:
            logging.info("arbitration dropped symbol=%s reason=dedupe_window", ctx.symbol)
            return None

        selected = cards[:]
        selected = self._dedupe_similar(selected)
        if not selected:
            return None

        winner = sorted(
            selected,
            key=lambda c: (c.priority, c.confidence, -c.ttl_minutes),
            reverse=True,
        )[0]
        logging.info(
            "arbitration winner symbol=%s strategy=%s side=%s priority=%s confidence=%.2f",
            ctx.symbol,
            winner.strategy,
            winner.side,
            winner.priority,
            winner.confidence,
        )
        return winner

    def _dedupe_similar(self, cards: list[ProposalCard]) -> list[ProposalCard]:
        kept: list[ProposalCard] = []
        for card in sorted(cards, key=lambda c: (c.priority, c.confidence, -c.ttl_minutes), reverse=True):
            duplicate = False
            for existing in kept:
                same_side = existing.side == card.side
                entry_close = abs(existing.entry - card.entry) / max(existing.entry, 1e-9) < self.config.entry_similar_pct
                stop_close = abs(existing.stop - card.stop) / max(abs(existing.stop), 1e-9) < self.config.stop_similar_pct
                if same_side and (entry_close or stop_close):
                    duplicate = True
                    logging.info(
                        "arbitration dropped strategy=%s reason=similar_entry_or_stop winner=%s",
                        card.strategy,
                        existing.strategy,
                    )
                    break
            if not duplicate:
                kept.append(card)
        return kept
