"""Live execution safety primitives.

These utilities are intentionally exchange-agnostic. They provide deterministic
client order IDs, idempotency reservation, and live-mode preflight checks around
the Gate 2 broker path.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from storage.db import get_db
from strategy.config import main_config, risk_gate_config
from strategy.schemas import ExecutionTicket, PlannedOrder


class LivePreflightError(RuntimeError):
    """Raised when live-mode safety requirements are not met."""


class LivePreOrderHealthError(LivePreflightError):
    """Raised when the live pre-order health gate must fail closed.

    Subclass of LivePreflightError so callers that already catch the parent type
    keep working — but the more specific class lets routers/tests distinguish a
    pre-order health failure from a generic mode-config failure.
    """


class _BrokerHealthClient(Protocol):
    """Minimal interface needed by the pre-order health gate.

    BinanceSignedClient already implements this; tests can pass any duck-typed
    object that supports ``position_risk``, ``open_orders``, ``open_algo_orders``.
    Task 7 also needs ``account_info`` for free-balance / margin checks.
    """

    def position_risk(self, symbol: str) -> list[dict[str, Any]]: ...

    def open_orders(self, symbol: str) -> list[dict[str, Any]]: ...

    def open_algo_orders(self, symbol: str) -> list[dict[str, Any]]: ...


class _AccountClient(Protocol):
    """Optional account-level interface — used when the deployment has wired
    a Binance Futures account_info call. ``BinanceSignedClient`` does not
    currently expose this directly; callers can pass any duck-typed object."""

    def account_info(self) -> dict[str, Any]: ...


class _PriceSource(Protocol):
    def last_price(self, symbol: str) -> float | None: ...


@dataclass(frozen=True)
class LiveExecutionConfig:
    mode: str
    environment: str
    allow_mainnet: bool
    require_gate_authorization: bool
    gate_authorization_file: str
    micro_live: dict[str, object] = field(default_factory=dict)


def load_live_execution_config() -> LiveExecutionConfig:
    cfg = main_config()
    live = cfg.get("live") or {}
    if not isinstance(live, dict):
        live = {}
    return LiveExecutionConfig(
        mode=str(cfg.get("mode", "shadow")).lower(),
        environment=str(live.get("environment", "testnet")).lower(),
        allow_mainnet=bool(live.get("allow_mainnet", False)),
        require_gate_authorization=bool(live.get("require_gate_authorization", True)),
        gate_authorization_file=str(
            live.get("gate_authorization_file", "docs/gate-2-authorization.md")
        ),
        micro_live=dict(live.get("micro_live") or {}),
    )


def assert_live_preflight(config: LiveExecutionConfig | None = None) -> None:
    config = config or load_live_execution_config()
    if config.mode != "live":
        return
    if config.environment not in {"testnet", "mainnet"}:
        raise LivePreflightError(f"invalid_live_environment:{config.environment}")
    if config.environment == "mainnet" and not config.allow_mainnet:
        raise LivePreflightError("mainnet_not_allowed")
    if config.require_gate_authorization and not Path(config.gate_authorization_file).exists():
        raise LivePreflightError("gate_authorization_missing")
    if config.environment == "mainnet":
        assert_mainnet_readiness(config)


def assert_live_mode_enabled(config: LiveExecutionConfig | None = None) -> None:
    config = config or load_live_execution_config()
    if config.mode != "live":
        raise LivePreflightError("live_ticket_while_global_mode_is_not_live")
    assert_live_preflight(config)


def assert_mainnet_readiness(
    config: LiveExecutionConfig | None = None,
    *,
    require_credentials: bool = True,
) -> None:
    """Fail closed unless mainnet has explicit micro-live limits.

    This is deliberately stricter than testnet. Gate 6 is a canary, not a
    general-purpose live switch.
    """
    config = config or load_live_execution_config()
    if config.environment != "mainnet":
        return
    auth_name = Path(config.gate_authorization_file).name.lower()
    if "gate-6" not in auth_name:
        raise LivePreflightError("mainnet_gate6_authorization_required")
    if require_credentials and (
        not os.getenv("BINANCE_FUTURES_MAINNET_API_KEY")
        or not os.getenv("BINANCE_FUTURES_MAINNET_API_SECRET")
    ):
        raise LivePreflightError("binance_mainnet_credentials_missing")

    micro = config.micro_live
    if not bool(micro.get("enabled", False)):
        raise LivePreflightError("mainnet_micro_live_not_enabled")
    if not _string_list(micro.get("allowed_symbols")):
        raise LivePreflightError("mainnet_allowed_symbols_missing")
    if _positive_float(micro.get("max_notional_usd")) <= 0:
        raise LivePreflightError("mainnet_max_notional_missing")
    if _positive_float(micro.get("max_leverage")) <= 0:
        raise LivePreflightError("mainnet_max_leverage_missing")
    if _positive_float(micro.get("max_daily_loss_usd")) <= 0:
        raise LivePreflightError("mainnet_daily_loss_cap_missing")
    if int(micro.get("max_concurrent_positions", 0) or 0) != 1:
        raise LivePreflightError("mainnet_max_concurrent_positions_must_be_1")
    _assert_in_exercise_window(micro)


def assert_micro_live_ticket(
    ticket: ExecutionTicket,
    config: LiveExecutionConfig | None = None,
    *,
    require_credentials: bool = True,
) -> None:
    """Validate a ticket against Gate 6 mainnet micro-live caps."""
    config = config or load_live_execution_config()
    if config.environment != "mainnet":
        return
    assert_mainnet_readiness(config, require_credentials=require_credentials)
    micro = config.micro_live

    allowed = {_normalize_symbol(symbol) for symbol in _string_list(micro.get("allowed_symbols"))}
    if _normalize_symbol(ticket.symbol) not in allowed:
        raise LivePreflightError("mainnet_symbol_not_allowed")
    max_notional = _positive_float(micro.get("max_notional_usd"))
    if ticket.notional_usd > max_notional:
        raise LivePreflightError(f"mainnet_notional_cap:{ticket.notional_usd:g}>{max_notional:g}")
    max_leverage = _positive_float(micro.get("max_leverage"))
    if ticket.leverage > max_leverage:
        raise LivePreflightError(f"mainnet_leverage_cap:{ticket.leverage:g}>{max_leverage:g}")
    if bool(micro.get("require_stop_loss", True)) and ticket.stop_price <= 0:
        raise LivePreflightError("mainnet_missing_stop_loss")
    if bool(micro.get("require_take_profit", True)) and not ticket.take_profit_price:
        raise LivePreflightError("mainnet_missing_take_profit")
    roles = {order.role for order in ticket.orders}
    if bool(micro.get("require_stop_loss", True)) and "stop" not in roles:
        raise LivePreflightError("mainnet_missing_stop_order")
    if bool(micro.get("require_take_profit", True)) and "take_profit" not in roles:
        raise LivePreflightError("mainnet_missing_take_profit_order")


def client_order_id(ticket: ExecutionTicket, order: PlannedOrder) -> str:
    """Return a deterministic Binance-safe client order id.

    Binance futures allows short custom IDs; keep it compact and stable. The
    ticket_id may be a ULID or test string, so strip separators and cap length.
    """
    ticket_part = "".join(ch for ch in ticket.ticket_id if ch.isalnum())[-18:]
    role = {
        "entry": "EN",
        "stop": "ST",
        "take_profit": "TP",
    }.get(order.role, order.role[:2].upper())
    side = order.side[:1].upper()
    return f"DA{role}{side}{ticket_part}"[:36]


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list | tuple):
        return [str(item) for item in value if str(item)]
    return []


def _positive_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _assert_in_exercise_window(micro: dict[str, object]) -> None:
    start_raw = str(micro.get("exercise_window_start") or "")
    end_raw = str(micro.get("exercise_window_end") or "")
    if not start_raw or not end_raw:
        raise LivePreflightError("mainnet_exercise_window_missing")
    start = _parse_dt(start_raw)
    end = _parse_dt(end_raw)
    now = datetime.now(tz=UTC)
    if not (start <= now <= end):
        raise LivePreflightError("mainnet_outside_exercise_window")


def _parse_dt(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LivePreflightError(f"invalid_exercise_window:{value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_symbol(symbol: str) -> str:
    for suffix in ("-PERP", "_PERP"):
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def reserve_order_idempotency(
    ticket: ExecutionTicket,
    order: PlannedOrder,
    *,
    db_path: Path | None = None,
) -> str:
    cid = client_order_id(ticket, order)
    with get_db(db_path) as conn:
        existing = conn.execute(
            """
            SELECT client_order_id, ticket_id, order_role, symbol, side
              FROM order_idempotency
             WHERE client_order_id=?
            """,
            (cid,),
        ).fetchone()
        if existing is not None:
            if (
                existing["ticket_id"] != ticket.ticket_id
                or existing["order_role"] != order.role
                or existing["symbol"] != order.symbol
                or existing["side"] != order.side
            ):
                raise LivePreflightError("client_order_id_collision")
            return cid
        conn.execute(
            """
            INSERT INTO order_idempotency
                (client_order_id, ticket_id, order_role, symbol, side, quantity, price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cid,
                ticket.ticket_id,
                order.role,
                order.symbol,
                order.side,
                order.quantity,
                order.price,
            ),
        )
        conn.commit()
    return cid


def assert_live_pre_order_health(
    ticket: ExecutionTicket,
    *,
    config: LiveExecutionConfig | None = None,
    broker_client: _BrokerHealthClient | None = None,
    price_source: _PriceSource | None = None,
    kill_switch_active: bool | None = None,
    max_price_age_seconds: float = 5.0,
    user_stream_active: bool | None = None,
    user_stream_max_age_seconds: float = 90.0,
    account_client: _AccountClient | None = None,
    db_path: Path | None = None,
) -> None:
    """Comprehensive pre-order health gate (Task 3).

    Invoked by the router immediately before submitting a live ticket. Fails
    closed on:

      1.  kill switch active
      2.  config-level live preflight (mode/env/gate auth/mainnet readiness)
      3.  symbol allowlist + leverage/notional caps + stop/tp presence
      4.  Binance public price unreachable / stale / non-positive
      5.  any account / position-risk query failure
      6.  any open-orders / algo open-orders query failure
      7.  existing exchange position on the same symbol
      8.  existing exchange open orders (regular or algo)

    The broker performs a partial subset of (5)-(8) inside ``submit_ticket``;
    this gate is the **router-level** fail-closed line so the broker is never
    invoked when health is unknown. The duplicate read-only calls are cheap
    insurance — they are GETs only and never mutate exchange state.
    """
    config = config or load_live_execution_config()

    # 1. Kill switch must be clear.
    if kill_switch_active is None:
        # Local import to avoid a circular dependency at module load time.
        from safety.kill_switch import get_kill_switch

        kill_switch_active = get_kill_switch().is_active()
    if kill_switch_active:
        raise LivePreOrderHealthError("kill_switch_active")

    # 2. Mode / authorization / mainnet-readiness preflight.
    assert_live_mode_enabled(config)

    # 3. Ticket-shape and per-symbol caps.
    if config.environment == "mainnet":
        assert_micro_live_ticket(ticket, config)
    else:
        if ticket.stop_price <= 0:
            raise LivePreOrderHealthError("ticket_missing_stop_loss")
        if ticket.take_profit_price is None or ticket.take_profit_price <= 0:
            raise LivePreOrderHealthError("ticket_missing_take_profit")
        if ticket.leverage <= 0:
            raise LivePreOrderHealthError("ticket_invalid_leverage")
        if ticket.notional_usd <= 0:
            raise LivePreOrderHealthError("ticket_invalid_notional")

    # 4. Public price liveness.
    price_source = price_source or _default_price_source()
    started = time.monotonic()
    try:
        last_price = price_source.last_price(ticket.symbol)
    except Exception as exc:  # noqa: BLE001
        raise LivePreOrderHealthError("public_price_query_failed") from exc
    elapsed = time.monotonic() - started
    if last_price is None:
        raise LivePreOrderHealthError("public_price_unavailable")
    if last_price <= 0:
        raise LivePreOrderHealthError("public_price_invalid")
    if elapsed > max_price_age_seconds:
        raise LivePreOrderHealthError(
            f"public_price_stale:{elapsed:.2f}s>{max_price_age_seconds:.2f}s"
        )

    # 5–8. Account/position + open-orders queries via signed broker client.
    if broker_client is None:
        raise LivePreOrderHealthError("broker_client_unavailable")

    try:
        positions = broker_client.position_risk(ticket.symbol)
    except LivePreflightError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LivePreOrderHealthError("position_query_failed") from exc

    try:
        open_orders = broker_client.open_orders(ticket.symbol)
    except LivePreflightError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LivePreOrderHealthError("open_orders_query_failed") from exc

    try:
        open_algo = broker_client.open_algo_orders(ticket.symbol)
    except LivePreflightError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LivePreOrderHealthError("open_algo_orders_query_failed") from exc

    for row in positions or []:
        amount_raw = row.get("positionAmt", 0) if isinstance(row, dict) else 0
        try:
            amt = float(amount_raw or 0)
        except (TypeError, ValueError) as exc:
            raise LivePreOrderHealthError("position_query_unparseable") from exc
        if abs(amt) > 0:
            raise LivePreOrderHealthError("existing_exchange_position")
    if open_orders:
        raise LivePreOrderHealthError("existing_exchange_open_orders")
    if open_algo:
        raise LivePreOrderHealthError("existing_exchange_open_algo_orders")

    # 9. Task 6 — user-stream heartbeat must be fresh in live mode. A stalled
    # WebSocket means we'd miss fills; new entries are not allowed until the
    # stream is healthy again. Reduce-only / emergency_close paths bypass this
    # gate by design (they live on different broker entry points).
    if user_stream_active is None:
        user_stream_active, detail = user_stream_heartbeat_fresh(
            db_path=db_path,
            max_age_seconds=user_stream_max_age_seconds,
        )
    else:
        detail = "explicit_override"
    if not user_stream_active:
        raise LivePreOrderHealthError(f"user_stream_unhealthy:{detail}")

    # 10. Task 7 — account-level guards. Skipped when no account client wired,
    # but mainnet operators are expected to always pass one. Tests can pass
    # a duck-typed object exposing ``account_info()``.
    if account_client is not None:
        _assert_account_health(ticket, account_client, positions or [])


def _default_price_source() -> _PriceSource:
    # Local import keeps live_safety free of network-layer imports at module load.
    from market_data.binance_public import BinancePublicClient

    return BinancePublicClient()


# ---------------------------------------------------------------------------
# Task 7 — account-level (free balance / margin / liquidation) guards
# ---------------------------------------------------------------------------


def _account_guard_caps() -> dict[str, float]:
    """Read account-level caps from risk_gate.yaml with conservative defaults."""
    cfg = risk_gate_config()
    return {
        "min_free_balance_usd": float(cfg.get("min_free_balance_usd", 0.0)),
        "min_free_balance_ratio": float(cfg.get("min_free_balance_ratio", 0.0)),
        "max_margin_ratio": float(cfg.get("max_margin_ratio", 0.0)),
        "min_liquidation_buffer_pct": float(cfg.get("min_liquidation_buffer_pct", 0.0)),
    }


def _assert_account_health(
    ticket: ExecutionTicket,
    account_client: _AccountClient,
    positions: list[dict[str, Any]],
) -> None:
    """Account-level fail-closed checks before a live order is submitted.

    All thresholds come from ``config/risk_gate.yaml`` and default to 0
    (disabled) so the gate remains permissive unless an operator opts in.
    """
    caps = _account_guard_caps()

    try:
        info = account_client.account_info()
    except LivePreflightError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LivePreOrderHealthError("account_info_query_failed") from exc

    if not isinstance(info, dict):
        raise LivePreOrderHealthError("account_info_unparseable")

    total_margin_balance = _positive_float(info.get("totalMarginBalance"))
    total_maint_margin = _positive_float(info.get("totalMaintMargin"))
    available_balance = _positive_float(info.get("availableBalance"))

    min_free_abs = caps["min_free_balance_usd"]
    if min_free_abs > 0 and available_balance < min_free_abs:
        raise LivePreOrderHealthError(
            f"free_balance_insufficient available={available_balance:g}<min={min_free_abs:g}"
        )

    min_free_ratio = caps["min_free_balance_ratio"]
    if min_free_ratio > 0 and ticket.notional_usd > 0:
        # Need enough free balance to cover the worst-case margin for THIS ticket.
        required = (ticket.notional_usd / max(ticket.leverage, 1.0)) * min_free_ratio
        if available_balance < required:
            raise LivePreOrderHealthError(
                f"free_balance_insufficient_for_ticket "
                f"available={available_balance:g}<required={required:g}"
            )

    max_margin_ratio = caps["max_margin_ratio"]
    if max_margin_ratio > 0 and total_margin_balance > 0:
        margin_ratio = total_maint_margin / total_margin_balance
        if margin_ratio > max_margin_ratio:
            raise LivePreOrderHealthError(
                f"margin_ratio_unsafe ratio={margin_ratio:.4f}>cap={max_margin_ratio:.4f}"
            )

    min_liq_buffer = caps["min_liquidation_buffer_pct"]
    if min_liq_buffer > 0:
        for row in positions or []:
            if not isinstance(row, dict):
                continue
            amt = _positive_float(row.get("positionAmt"))
            if amt == 0:
                continue
            mark_price = _positive_float(row.get("markPrice"))
            liq_price = _positive_float(row.get("liquidationPrice"))
            if mark_price <= 0 or liq_price <= 0:
                continue
            buffer_pct = abs(mark_price - liq_price) / mark_price
            if buffer_pct < min_liq_buffer:
                raise LivePreOrderHealthError(
                    f"liquidation_buffer_insufficient "
                    f"buffer={buffer_pct:.4f}<min={min_liq_buffer:.4f}"
                )


# ---------------------------------------------------------------------------
# Task 6 — user-stream heartbeat helper
# ---------------------------------------------------------------------------


def user_stream_heartbeat_fresh(
    *,
    db_path: Path | None = None,
    max_age_seconds: float = 90.0,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Return ``(is_fresh, detail)`` for the most recent user-stream heartbeat.

    A heartbeat row is written by ``live_user_stream`` whenever the listenKey
    is created, the WS connects, an event is ingested, or a keepalive lands.
    Live readiness depends on those rows arriving frequently enough to detect
    a stalled stream within ``max_age_seconds``.
    """
    cutoff = (now or datetime.now(tz=UTC)) - timedelta(seconds=max_age_seconds)
    cutoff_sql = cutoff.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with get_db(db_path) as conn:
            row = conn.execute(
                """
                SELECT status, created_at
                  FROM live_runtime_heartbeats
                 WHERE component='user_stream'
                   AND datetime(created_at) >= datetime(?)
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (cutoff_sql,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        # Fail closed — we cannot prove the stream is alive.
        return False, f"heartbeat_query_failed:{type(exc).__name__}"
    if row is None:
        return False, f"no_heartbeat_in_last_{int(max_age_seconds)}s"
    return True, str(row["status"])


def order_idempotency_statuses(
    ticket: ExecutionTicket,
    *,
    db_path: Path | None = None,
) -> dict[str, str]:
    ids = [client_order_id(ticket, order) for order in ticket.orders]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with get_db(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT client_order_id, status
              FROM order_idempotency
             WHERE client_order_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
    return {str(row["client_order_id"]): str(row["status"]) for row in rows}
