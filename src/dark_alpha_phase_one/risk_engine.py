from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


class RiskEngine:
    def __init__(
        self,
        *,
        state_path: str,
        max_daily_loss_usdt: float,
        max_cards_per_day: int,
        cooldown_after_trigger_minutes: int,
        kill_switch: bool,
        pnl_csv_path: str | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.max_daily_loss_usdt = max_daily_loss_usdt
        self.max_cards_per_day = max_cards_per_day
        self.cooldown_after_trigger_minutes = cooldown_after_trigger_minutes
        self.kill_switch = kill_switch
        self.pnl_csv_path = Path(pnl_csv_path) if pnl_csv_path else None

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._save_state({"days": {}, "last_trigger_by_symbol": {}})

    def evaluate(self, symbol: str, now: datetime | None = None) -> RiskDecision:
        now = now or datetime.now(tz=timezone.utc)
        if self.kill_switch:
            return RiskDecision(allowed=False, reason="kill_switch_enabled")

        state = self._load_state()
        date_key = now.date().isoformat()
        day_state = state["days"].get(date_key, {"cards_count": 0, "realized_loss_usdt": 0.0})
        realized_loss = self._resolve_realized_loss(date_key, day_state)

        if realized_loss >= self.max_daily_loss_usdt:
            return RiskDecision(allowed=False, reason="max_daily_loss_exceeded")

        if int(day_state.get("cards_count", 0)) >= self.max_cards_per_day:
            return RiskDecision(allowed=False, reason="max_cards_per_day_exceeded")

        cooldown_until = self._cooldown_until(symbol, state)
        if cooldown_until and now < cooldown_until:
            return RiskDecision(allowed=False, reason="symbol_cooldown_active")

        return RiskDecision(allowed=True, reason="ok")

    def record_trigger(self, symbol: str, now: datetime | None = None) -> None:
        now = now or datetime.now(tz=timezone.utc)
        date_key = now.date().isoformat()

        state = self._load_state()
        day_state = state["days"].setdefault(date_key, {"cards_count": 0, "realized_loss_usdt": 0.0})
        day_state["cards_count"] = int(day_state.get("cards_count", 0)) + 1
        state["last_trigger_by_symbol"][symbol] = now.isoformat()
        self._save_state(state)


    def get_last_trigger_time(self, symbol: str) -> datetime | None:
        state = self._load_state()
        raw = state["last_trigger_by_symbol"].get(symbol)
        if not raw:
            return None
        return self._parse_timestamp(raw)

    def _cooldown_until(self, symbol: str, state: dict[str, object]) -> datetime | None:
        raw = state["last_trigger_by_symbol"].get(symbol)
        if not raw:
            return None
        last_trigger = self._parse_timestamp(raw)
        return last_trigger + timedelta(minutes=self.cooldown_after_trigger_minutes)


    def _parse_timestamp(self, raw: str) -> datetime:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _resolve_realized_loss(self, date_key: str, day_state: dict[str, object]) -> float:
        if not self.pnl_csv_path or not self.pnl_csv_path.exists():
            return float(day_state.get("realized_loss_usdt", 0.0))

        realized_loss = 0.0
        with self.pnl_csv_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("date"):
                    continue
                date_value, pnl_value = [item.strip() for item in stripped.split(",", maxsplit=1)]
                if date_value != date_key:
                    continue
                pnl = float(pnl_value)
                if pnl < 0:
                    realized_loss += abs(pnl)
        return realized_loss

    def _load_state(self) -> dict[str, object]:
        with self.state_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save_state(self, state: dict[str, object]) -> None:
        with self.state_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
