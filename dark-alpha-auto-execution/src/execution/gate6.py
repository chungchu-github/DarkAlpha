"""Gate 6 mainnet micro-live preflight and closeout helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from ulid import ULID

from safety.kill_switch import KillSwitch, get_kill_switch
from signal_adapter.schemas import InvalidationInfo, SetupEvent, TriggerInfo
from storage.db import get_db
from strategy.schemas import ExecutionTicket, PlannedOrder

from .binance_testnet_broker import (
    BinanceFuturesBroker,
    BinanceFuturesClient,
    _base_url_for_environment,
    normalize_symbol,
)
from .exchange_filters import BinanceExchangeInfoClient, ExchangeFilterProvider
from .live_order_sync import LiveOrderStatusSync, OrderSyncResult
from .live_reconciliation import LiveReconciler, ReconciliationResult
from .live_safety import (
    LiveExecutionConfig,
    LivePreflightError,
    assert_live_mode_enabled,
    load_live_execution_config,
)
from .router import ModeRouter


class Gate6Error(RuntimeError):
    """Raised when Gate 6 preflight/closeout fails closed."""


@dataclass(frozen=True)
class Gate6SymbolState:
    symbol: str
    open_regular_orders: int
    open_algo_orders: int
    position_amount: float
    leverage: float | None = None
    margin_type: str | None = None
    position_side: str | None = None


@dataclass(frozen=True)
class Gate6PreflightResult:
    environment: str
    status: str
    symbols: list[Gate6SymbolState]
    checks: list[str] = field(default_factory=list)

    def markdown(self) -> str:
        lines = [
            "# Gate 6 Preflight",
            "",
            f"- status: `{self.status}`",
            f"- environment: `{self.environment}`",
            "",
            "## Checks",
            "",
        ]
        lines.extend(f"- {item}" for item in self.checks)
        lines += ["", "## Symbols", ""]
        lines.append(
            "| Symbol | Open Orders | Open Algo Orders | Position Amt | Leverage | Margin | Side |"
        )
        lines.append("|---|---:|---:|---:|---:|---|---|")
        for symbol in self.symbols:
            lines.append(
                f"| {symbol.symbol} | {symbol.open_regular_orders} | {symbol.open_algo_orders} | "
                f"{symbol.position_amount:g} | {_fmt(symbol.leverage)} | "
                f"{symbol.margin_type or ''} | {symbol.position_side or ''} |"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class Gate6CloseoutResult:
    symbol: str
    cancelled: dict[str, Any]
    flatten_submitted: bool
    sync_results: list[OrderSyncResult]
    reconciliation: ReconciliationResult
    report_path: Path


@dataclass(frozen=True)
class Gate6CanaryResult:
    ticket: ExecutionTicket
    dispatch_ref: str
    mark_price: float


@dataclass(frozen=True)
class Gate6RepairResult:
    symbol: str
    closed_positions: int
    ticket_ids: list[str]


class Gate6Preflight:
    def __init__(
        self,
        *,
        client: BinanceFuturesClient | None = None,
        config: LiveExecutionConfig | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self._config = config or load_live_execution_config()
        self._broker = BinanceFuturesBroker(client=client, config=self._config)
        self._client = client or self._broker.client
        self._kill_switch = kill_switch or get_kill_switch()

    def run(self, symbols: list[str] | None = None) -> Gate6PreflightResult:
        assert_live_mode_enabled(self._config)
        if self._config.environment != "mainnet":
            raise LivePreflightError("gate6_preflight_requires_mainnet")
        if self._kill_switch.is_active():
            raise LivePreflightError("kill_switch_active")

        target_symbols = symbols or _allowed_symbols(self._config)
        if not target_symbols:
            raise LivePreflightError("gate6_symbols_missing")

        states = [self._symbol_state(symbol) for symbol in target_symbols]
        dirty = [
            state
            for state in states
            if state.open_regular_orders > 0
            or state.open_algo_orders > 0
            or abs(state.position_amount) > 0
        ]
        if dirty:
            raise Gate6Error("gate6_account_not_clean:" + ",".join(state.symbol for state in dirty))

        checks = [
            "live mode enabled",
            "mainnet environment confirmed",
            "Gate 6 authorization and micro-live caps accepted by live_safety",
            "kill switch clear",
            "no open regular orders for allowed symbols",
            "no open algo orders for allowed symbols",
            "no open positions for allowed symbols",
        ]
        return Gate6PreflightResult(
            environment=self._config.environment,
            status="ok",
            symbols=states,
            checks=checks,
        )

    def _symbol_state(self, symbol: str) -> Gate6SymbolState:
        positions = self._client.position_risk(symbol)
        amount = 0.0
        leverage: float | None = None
        margin_type: str | None = None
        position_side: str | None = None
        for row in positions:
            amount += _float(row.get("positionAmt"))
            if leverage is None and row.get("leverage") not in (None, ""):
                leverage = _float(row.get("leverage"))
            margin_type = margin_type or _optional_str(row.get("marginType"))
            position_side = position_side or _optional_str(row.get("positionSide"))
        return Gate6SymbolState(
            symbol=symbol,
            open_regular_orders=len(self._client.open_orders(symbol)),
            open_algo_orders=len(self._client.open_algo_orders(symbol)),
            position_amount=amount,
            leverage=leverage,
            margin_type=margin_type,
            position_side=position_side,
        )


def run_gate6_closeout(
    symbol: str,
    *,
    yes: bool,
    broker: BinanceFuturesBroker | None = None,
    sync: LiveOrderStatusSync | None = None,
    reconciler: LiveReconciler | None = None,
    reports_dir: Path | None = None,
) -> Gate6CloseoutResult:
    if not yes:
        raise Gate6Error("gate6_closeout_requires_yes")
    assert_live_mode_enabled()
    config = load_live_execution_config()
    if config.environment != "mainnet":
        raise LivePreflightError("gate6_closeout_requires_mainnet")

    broker = broker or BinanceFuturesBroker(config=config)
    cancelled = dict(broker.cancel_all_open_orders(symbol))
    flatten_ack = broker.emergency_close_symbol(symbol)
    syncer = sync or LiveOrderStatusSync()
    sync_results = syncer.sync_symbol(symbol)
    repair: Gate6RepairResult | None = None
    if flatten_ack is not None:
        repair = repair_local_flat_after_closeout(symbol, yes=True)
    reconciler = reconciler or LiveReconciler()
    reconciliation = reconciler.run([symbol])
    path = write_closeout_report(
        symbol=symbol,
        cancelled=cancelled,
        flatten_submitted=flatten_ack is not None,
        sync_results=sync_results,
        reconciliation=reconciliation,
        repair=repair,
        reports_dir=reports_dir,
    )
    return Gate6CloseoutResult(
        symbol=symbol,
        cancelled=cancelled,
        flatten_submitted=flatten_ack is not None,
        sync_results=sync_results,
        reconciliation=reconciliation,
        report_path=path,
    )


def repair_local_flat_after_closeout(
    symbol: str,
    *,
    yes: bool,
    client: BinanceFuturesClient | None = None,
    db_path: Path | None = None,
) -> Gate6RepairResult:
    """Mark local live positions closed after a verified exchange flatten.

    This is intentionally conservative: it only repairs local state when the
    exchange reports zero position and no open regular/algo orders for the
    symbol.
    """
    if not yes:
        raise Gate6Error("gate6_repair_requires_yes")
    config = load_live_execution_config()
    assert_live_mode_enabled(config)
    if config.environment != "mainnet":
        raise LivePreflightError("gate6_repair_requires_mainnet")

    broker = BinanceFuturesBroker(client=client, config=config)
    exchange_client = client or broker.client
    position_amt = sum(
        _float(row.get("positionAmt")) for row in exchange_client.position_risk(symbol)
    )
    if abs(position_amt) > 0:
        raise Gate6Error(f"gate6_repair_exchange_position_not_flat:{position_amt:g}")
    if exchange_client.open_orders(symbol):
        raise Gate6Error("gate6_repair_exchange_regular_orders_still_open")
    if exchange_client.open_algo_orders(symbol):
        raise Gate6Error("gate6_repair_exchange_algo_orders_still_open")

    normalized = normalize_symbol(symbol)
    now = datetime.now(tz=UTC).isoformat()
    ticket_ids: list[str] = []
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT position_id, ticket_id, symbol
              FROM positions
             WHERE shadow_mode=0
               AND status IN ('pending','open','partial')
            """
        ).fetchall()
        matching = [row for row in rows if normalize_symbol(str(row["symbol"])) == normalized]
        for row in matching:
            conn.execute(
                """
                UPDATE positions
                   SET status='closed',
                       filled_quantity=0,
                       closed_at=?,
                       exit_reason='manual_flatten_reconciled'
                 WHERE position_id=?
                """,
                (now, row["position_id"]),
            )
            if row["ticket_id"]:
                ticket_ids.append(str(row["ticket_id"]))
        for ticket_id in ticket_ids:
            conn.execute(
                "UPDATE execution_tickets SET status='closed' WHERE ticket_id=?",
                (ticket_id,),
            )
        conn.commit()
    return Gate6RepairResult(
        symbol=symbol,
        closed_positions=len(matching),
        ticket_ids=ticket_ids,
    )


def submit_gate6_canary(
    *,
    symbol: str,
    side: str,
    yes: bool,
    entry_offset_pct: float = 0.005,
    stop_distance_pct: float = 0.01,
    take_profit_distance_pct: float = 0.01,
    db_path: Path | None = None,
    config: LiveExecutionConfig | None = None,
    filters: ExchangeFilterProvider | None = None,
    broker: BinanceFuturesBroker | None = None,
) -> Gate6CanaryResult:
    """Submit one Gate 6 mainnet micro-live canary bracket.

    This intentionally bypasses strategy selection but not live safety:
    ModeRouter still persists the ticket, reserves deterministic IDs, checks
    live mode, and routes through the live broker.
    """
    if not yes:
        raise Gate6Error("gate6_submit_requires_yes")
    config = config or load_live_execution_config()
    assert_live_mode_enabled(config)
    if config.environment != "mainnet":
        raise LivePreflightError("gate6_submit_requires_mainnet")
    Gate6Preflight(config=config).run([symbol])

    upper_side = side.upper()
    if upper_side not in {"LONG", "SHORT"}:
        raise Gate6Error("gate6_side_must_be_long_or_short")
    filters = filters or BinanceExchangeInfoClient(base_url=_base_url_for_environment("mainnet"))
    symbol_filters = filters.symbol_filters(symbol)
    max_notional = float(config.micro_live["max_notional_usd"])
    mark = _last_price(symbol)

    if upper_side == "LONG":
        entry = mark * (1 - entry_offset_pct)
        stop = entry * (1 - stop_distance_pct)
        take_profit = entry * (1 + take_profit_distance_pct)
        direction = "long"
        entry_side = "buy"
        exit_side = "sell"
    else:
        entry = mark * (1 + entry_offset_pct)
        stop = entry * (1 + stop_distance_pct)
        take_profit = entry * (1 - take_profit_distance_pct)
        direction = "short"
        entry_side = "sell"
        exit_side = "buy"

    entry_price = float(symbol_filters.price(entry))
    stop_price = float(symbol_filters.price(stop))
    take_profit_price = float(symbol_filters.price(take_profit))
    _validate_canary_conditional_triggers(
        direction=direction,
        mark_price=mark,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
    )
    quantity = float(symbol_filters.quantity(max_notional / entry_price))
    if quantity <= 0:
        raise Gate6Error("gate6_quantity_below_step")
    notional = quantity * entry_price
    symbol_filters.assert_min_notional(price=entry_price, quantity=quantity)
    if notional > max_notional:
        raise Gate6Error(f"gate6_notional_exceeds_cap:{notional:g}>{max_notional:g}")

    event_id = f"gate6-canary-{ULID()}"
    ticket_id = str(ULID())
    created_at = datetime.now(tz=UTC).isoformat()
    event = SetupEvent(
        event_id=event_id,
        timestamp=created_at,
        symbol=symbol,
        setup_type="active",
        direction=direction,
        regime="gate6_manual_canary",
        today_decision="Gate 6 manual mainnet canary",
        ranking_score=9.0,
        trigger=TriggerInfo(condition="Gate 6 manual canary entry", price_level=entry_price),
        invalidation=InvalidationInfo(condition="Gate 6 canary stop", price_level=stop_price),
        metadata={"gate": "gate6", "manual": True, "mark_price": mark},
    )
    ticket = ExecutionTicket(
        ticket_id=ticket_id,
        source_event_id=event_id,
        symbol=symbol,
        direction=direction,
        regime="gate6_manual_canary",
        ranking_score=9.0,
        shadow_mode=False,
        gate="gate6",
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        quantity=quantity,
        notional_usd=notional,
        leverage=1.0,
        risk_usd=abs(entry_price - stop_price) * quantity,
        orders=[
            PlannedOrder(
                role="entry",
                side=entry_side,  # type: ignore[arg-type]
                type="limit",
                symbol=symbol,
                price=entry_price,
                quantity=quantity,
            ),
            PlannedOrder(
                role="stop",
                side=exit_side,  # type: ignore[arg-type]
                type="stop_market",
                symbol=symbol,
                price=stop_price,
                quantity=quantity,
                reduce_only=True,
            ),
            PlannedOrder(
                role="take_profit",
                side=exit_side,  # type: ignore[arg-type]
                type="limit",
                symbol=symbol,
                price=take_profit_price,
                quantity=quantity,
                reduce_only=True,
            ),
        ],
        created_at=created_at,
        metadata={"gate6_canary": True, "mark_price": mark},
    )

    _persist_setup_event(event, db_path=db_path)
    router = ModeRouter(live_broker=broker or BinanceFuturesBroker(config=config), db_path=db_path)
    dispatch_ref = router.dispatch(ticket)
    return Gate6CanaryResult(ticket=ticket, dispatch_ref=dispatch_ref, mark_price=mark)


def write_gate6_authorization(
    *,
    symbol: str,
    max_notional_usd: float,
    max_leverage: float,
    max_daily_loss_usd: float,
    window_start: str,
    window_end: str,
    strategy_scope: str,
    directions: str,
    auto_flatten: bool,
    operator: str,
    output: Path,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Gate 6 Mainnet Micro-Live Authorization",
        "",
        f"- Generated at UTC: `{datetime.now(tz=UTC).isoformat()}`",
        f"- Operator: `{operator}`",
        "",
        "## Operator Limits",
        "",
        f"- Authorized symbol: `{symbol}`",
        f"- Direction allowed: `{directions}`",
        f"- Strategy allowed: `{strategy_scope}`",
        f"- Max notional per order USDT: `{max_notional_usd:g}`",
        f"- Max leverage: `{max_leverage:g}`",
        "- Max concurrent positions: `1`",
        f"- Max daily loss USDT: `{max_daily_loss_usd:g}`",
        f"- Exercise window start UTC: `{window_start}`",
        f"- Exercise window end UTC: `{window_end}`",
        "- Auto cancel-all after window: `yes`",
        f"- Auto flatten after window: `{'yes' if auto_flatten else 'manual confirm'}`",
        "",
        "## Safety Acknowledgement",
        "",
        "- [x] Mainnet key is dedicated to this bot.",
        "- [x] Mainnet key has no withdrawal permission.",
        "- [x] Mainnet key is IP restricted when possible.",
        "- [ ] `poetry run dark-alpha gate-check all` passed immediately before the run.",
        "- [ ] Binance account has no unknown open orders.",
        "- [ ] Binance account has no unknown position.",
        "- [x] Every live ticket must include stop loss and take profit.",
        "- [x] Operator accepts that this is a micro-live canary, not production live trading.",
        "",
        "## Matching `config/main.yaml` Block",
        "",
        "```yaml",
        "mode: live",
        "live:",
        "  environment: mainnet",
        "  allow_mainnet: true",
        "  require_gate_authorization: true",
        "  gate_authorization_file: docs/gate-6-authorization.md",
        "  micro_live:",
        "    enabled: true",
        "    allowed_symbols:",
        f"      - {symbol}",
        f"    max_notional_usd: {max_notional_usd:g}",
        f"    max_leverage: {max_leverage:g}",
        f"    max_daily_loss_usd: {max_daily_loss_usd:g}",
        "    max_concurrent_positions: 1",
        "    require_stop_loss: true",
        "    require_take_profit: true",
        f'    exercise_window_start: "{window_start}"',
        f'    exercise_window_end: "{window_end}"',
        f"    auto_cancel_flatten_after: {'true' if auto_flatten else 'false'}",
        "```",
        "",
        "## Signature",
        "",
        f"- Operator: `{operator}`",
        f"- Date: `{datetime.now(tz=UTC).date().isoformat()}`",
    ]
    output.write_text("\n".join(lines) + "\n")
    return output


def write_closeout_report(
    *,
    symbol: str,
    cancelled: dict[str, Any],
    flatten_submitted: bool,
    sync_results: list[OrderSyncResult],
    reconciliation: ReconciliationResult,
    repair: Gate6RepairResult | None = None,
    reports_dir: Path | None = None,
) -> Path:
    out_dir = reports_dir or Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = (
        out_dir
        / f"gate6-closeout-{normalize_symbol(symbol)}-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}.md"
    )
    lines = [
        f"# Gate 6 Closeout - {symbol}",
        "",
        f"- Generated at UTC: `{datetime.now(tz=UTC).isoformat()}`",
        f"- Flatten submitted: `{flatten_submitted}`",
        f"- Reconciliation status: `{reconciliation.status}`",
        f"- Local flat repair: `{repair.closed_positions if repair else 0}`",
        "",
        "## Cancel All Result",
        "",
        "```json",
        json.dumps(cancelled, indent=2, sort_keys=True),
        "```",
        "",
        "## Sync Results",
        "",
    ]
    if not sync_results:
        lines.append("- No local live orders to sync.")
    else:
        lines.append("| Client Order ID | Exchange | Local | Filled | Avg |")
        lines.append("|---|---|---|---:|---:|")
        for item in sync_results:
            avg = (
                ""
                if item.average_price is None
                else f"{item.average_price:.8f}".rstrip("0").rstrip(".")
            )
            lines.append(
                f"| `{item.client_order_id}` | {item.exchange_status} | {item.local_status} | "
                f"{item.filled_quantity:g} | {avg} |"
            )
    lines += [
        "",
        "## Reconciliation",
        "",
        f"- Run ID: `{reconciliation.run_id}`",
        f"- Status: `{reconciliation.status}`",
    ]
    for symbol_result in reconciliation.symbols:
        if symbol_result.mismatches:
            lines.append(f"- {symbol_result.symbol}: mismatch")
            lines.extend(f"  - {item}" for item in symbol_result.mismatches)
        else:
            lines.append(f"- {symbol_result.symbol}: ok")
    if repair is not None:
        lines += [
            "",
            "## Local Flat Repair",
            "",
            f"- Symbol: `{repair.symbol}`",
            f"- Closed positions: `{repair.closed_positions}`",
            f"- Ticket IDs: `{', '.join(repair.ticket_ids)}`",
        ]
    out.write_text("\n".join(lines) + "\n")
    return out


def _validate_canary_conditional_triggers(
    *,
    direction: str,
    mark_price: float,
    stop_price: float,
    take_profit_price: float,
) -> None:
    """Reject canaries whose protective triggers are already crossed.

    The broker submits protective conditional orders before the entry order
    fills. Binance rejects those orders with -2021 when the trigger is already
    on the triggered side of MARK_PRICE, so fail locally with a clearer error.
    """
    if direction == "long":
        if stop_price >= mark_price:
            raise Gate6Error(
                f"gate6_stop_would_immediately_trigger:stop={stop_price:g},mark={mark_price:g}"
            )
        if take_profit_price <= mark_price:
            raise Gate6Error(
                f"gate6_take_profit_would_immediately_trigger:"
                f"take_profit={take_profit_price:g},mark={mark_price:g}"
            )
        return
    if direction == "short":
        if stop_price <= mark_price:
            raise Gate6Error(
                f"gate6_stop_would_immediately_trigger:stop={stop_price:g},mark={mark_price:g}"
            )
        if take_profit_price >= mark_price:
            raise Gate6Error(
                f"gate6_take_profit_would_immediately_trigger:"
                f"take_profit={take_profit_price:g},mark={mark_price:g}"
            )
        return
    raise Gate6Error(f"gate6_unknown_direction:{direction}")


def _allowed_symbols(config: LiveExecutionConfig) -> list[str]:
    value = config.micro_live.get("allowed_symbols")
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list | tuple):
        return [str(item) for item in value if str(item)]
    return []


def _persist_setup_event(event: SetupEvent, *, db_path: Path | None) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO setup_events
                (event_id, timestamp, symbol, setup_type, payload, received_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.timestamp,
                event.symbol,
                event.setup_type,
                event.model_dump_json(),
                datetime.now(tz=UTC).isoformat(),
            ),
        )
        conn.commit()


def _last_price(symbol: str) -> float:
    resp = httpx.get(
        f"{_base_url_for_environment('mainnet')}/fapi/v1/ticker/price",
        params={"symbol": normalize_symbol(symbol)},
        timeout=10.0,
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _fmt(value: object) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)
