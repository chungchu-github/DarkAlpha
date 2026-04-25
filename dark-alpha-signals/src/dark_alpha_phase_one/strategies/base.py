from __future__ import annotations

from abc import ABC, abstractmethod

from dark_alpha_phase_one.engine.signal_context import SignalContext
from dark_alpha_phase_one.models import ProposalCard


class Strategy(ABC):
    name: str

    @abstractmethod
    def generate(self, signal_context: SignalContext) -> ProposalCard | None:
        """Build a proposal card if strategy condition passes."""
