"""Dark Alpha Auto-Execution CLI.

Commands:
  halt    — activate kill switch (stops new ticket generation)
  resume  — clear kill switch (use with caution)
  status  — show kill switch + circuit breaker state
  flatten — (Phase 5+) force-close all open positions via live broker
"""

import sys
from pathlib import Path

# Ensure src/ is on the path when invoked as `python -m cli.main`
sys.path.insert(0, str(Path(__file__).parent.parent))

import click

import bootstrap  # noqa: F401,E402  — must run before any os.getenv read
from safety.circuit_breaker import CircuitBreaker
from safety.kill_switch import KillSwitch


def _make_kill_switch(sentinel: str | None) -> KillSwitch:
    return KillSwitch(sentinel_path=Path(sentinel) if sentinel else None)


@click.group()
def cli() -> None:
    """Dark Alpha Auto-Execution safety controls."""


@cli.command()
@click.option("--reason", default="manual CLI halt", help="Reason for halting")
@click.option("--sentinel", default=None, hidden=True, help="Override sentinel path (tests only)")
def halt(reason: str, sentinel: str | None) -> None:
    """Activate kill switch — stops all new ticket generation immediately."""
    ks = _make_kill_switch(sentinel)
    ks.activate(reason=reason)
    click.echo(f"✓ Kill switch ACTIVATED ({reason})")
    click.echo(f"  Sentinel: {ks.sentinel_path()}")
    click.echo("  To resume: dark-alpha resume")


@cli.command()
@click.option("--sentinel", default=None, hidden=True, help="Override sentinel path (tests only)")
def resume(sentinel: str | None) -> None:
    """Clear kill switch — resume normal operation.

    WARNING: Only call after investigating the reason for the halt.
    """
    ks = _make_kill_switch(sentinel)
    if not ks.is_active():
        click.echo("Kill switch is not active — nothing to clear.")
        return
    click.confirm("Are you sure you want to resume trading?", abort=True)
    ks.deactivate()
    click.echo("✓ Kill switch cleared.")


@cli.command()
def status() -> None:
    """Show current kill switch, circuit breaker, and live execution state."""
    from execution.live_safety import (
        LivePreflightError,
        assert_mainnet_readiness,
        load_live_execution_config,
    )

    ks = KillSwitch()
    cb = CircuitBreaker()

    click.echo("\n=== Dark Alpha Auto-Execution Status ===\n")

    # Kill switch
    ks_status = "🔴 ACTIVE" if ks.is_active() else "🟢 clear"
    click.echo(f"Kill switch : {ks_status}")
    click.echo(f"  Sentinel  : {ks.sentinel_path()}")

    # Circuit breakers
    click.echo("\nCircuit breakers:")
    states = cb.all_states()
    if not states:
        click.echo("  (no breakers have fired)")
    for name, state in states.items():
        icon = "🔴" if state.status == "tripped" else "🟢"
        line = f"  {icon} {name:30s} {state.status}"
        if state.status == "tripped":
            line += f"  action={state.action}  clear_at={state.clear_at or 'manual'}"
        click.echo(line)

    # Live execution arming state
    click.echo("\nLive execution:")
    try:
        live_cfg = load_live_execution_config()
    except Exception as exc:  # noqa: BLE001
        click.echo(f"  ✗ failed to load live config: {exc}")
        click.echo()
        return

    micro = live_cfg.micro_live
    enabled = bool(micro.get("enabled", False))
    win_start = str(micro.get("exercise_window_start") or "—")
    win_end = str(micro.get("exercise_window_end") or "—")
    click.echo(f"  mode               : {live_cfg.mode}")
    click.echo(f"  environment        : {live_cfg.environment}")
    click.echo(f"  allow_mainnet      : {live_cfg.allow_mainnet}")
    click.echo(f"  micro_live enabled : {enabled}")
    click.echo(f"  exercise_window    : {win_start} → {win_end}")

    is_mainnet_live = live_cfg.mode == "live" and live_cfg.environment == "mainnet"
    if not is_mainnet_live:
        click.echo("  mainnet live armed : 🟢 no  (mode/environment is safe)")
    else:
        try:
            assert_mainnet_readiness(live_cfg)
        except LivePreflightError as exc:
            click.echo(f"  mainnet live armed : 🟢 no  (blocked: {exc})")
        else:
            click.echo("  mainnet live armed : 🔴 YES — orders will hit real exchange")

    click.echo()


@cli.command("cancel-open-orders")
@click.option("--symbol", required=True, help="Symbol to cancel, e.g. BTCUSDT-PERP")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def cancel_open_orders(symbol: str, yes: bool) -> None:
    """Cancel all open orders for one symbol on the configured Binance Futures environment."""
    from execution.binance_testnet_broker import BinanceFuturesBroker, LiveBrokerError
    from execution.live_safety import (
        LivePreflightError,
        assert_live_mode_enabled,
        load_live_execution_config,
    )

    env = load_live_execution_config().environment
    if not yes:
        click.confirm(f"Cancel all {env.upper()} open orders for {symbol}?", abort=True)
    try:
        assert_live_mode_enabled()
        result = BinanceFuturesBroker().cancel_all_open_orders(symbol)
    except (LiveBrokerError, LivePreflightError) as exc:
        click.echo(f"✗ cancel-open-orders blocked: {exc}", err=True)
        sys.exit(2)
    click.echo(f"✓ cancel-all submitted for {symbol}: {result}")


@cli.command("reconcile-live")
@click.option("--symbol", "symbols", multiple=True, help="Symbol to reconcile; repeatable")
def reconcile_live(symbols: tuple[str, ...]) -> None:
    """Run Gate 2 live/testnet reconciliation and halt on mismatch."""
    from execution.live_reconciliation import LiveReconciler
    from execution.live_safety import LivePreflightError, assert_live_mode_enabled

    try:
        assert_live_mode_enabled()
        reconciler = LiveReconciler()
        result = reconciler.run(list(symbols)) if symbols else reconciler.run_for_local_symbols()
    except LivePreflightError as exc:
        click.echo(f"✗ reconcile-live blocked: {exc}", err=True)
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"✗ reconcile-live failed: {exc}", err=True)
        sys.exit(2)

    click.echo(f"run_id={result.run_id} status={result.status}")
    for symbol in result.symbols:
        if symbol.mismatches:
            click.echo(f"  {symbol.symbol}: mismatch")
            for item in symbol.mismatches:
                click.echo(f"    - {item}")
        else:
            click.echo(f"  {symbol.symbol}: ok")
    if result.status != "ok":
        sys.exit(3)


@cli.command("sync-live-orders")
@click.option("--symbol", default=None, help="Symbol to sync; default = all local live orders")
def sync_live_orders(symbol: str | None) -> None:
    """Poll Binance testnet order status and update local live positions."""
    from execution.live_order_sync import LiveOrderStatusSync
    from execution.live_safety import LivePreflightError, assert_live_mode_enabled

    try:
        assert_live_mode_enabled()
        sync = LiveOrderStatusSync()
        results = sync.sync_symbol(symbol) if symbol else sync.sync_all()
    except LivePreflightError as exc:
        click.echo(f"✗ sync-live-orders blocked: {exc}", err=True)
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"✗ sync-live-orders failed: {exc}", err=True)
        sys.exit(2)

    if not results:
        click.echo("No local live orders to sync.")
        return
    for result in results:
        avg = f"{result.average_price:.8f}" if result.average_price is not None else "n/a"
        click.echo(
            f"{result.client_order_id} exchange={result.exchange_status} "
            f"local={result.local_status} filled={result.filled_quantity:g} avg={avg}"
        )


@cli.group("user-stream")
def user_stream() -> None:
    """Binance Futures user data stream controls."""


@user_stream.command("listen")
@click.option("--once", is_flag=True, help="Exit after the first WebSocket message")
def user_stream_listen(once: bool) -> None:
    """Listen to Binance user-data stream and ingest live fill events."""
    import asyncio

    from execution.live_safety import LivePreflightError, assert_live_mode_enabled
    from execution.live_user_stream import UserStreamError, run_user_stream

    try:
        assert_live_mode_enabled()
        asyncio.run(run_user_stream(once=once))
    except (LivePreflightError, UserStreamError) as exc:
        click.echo(f"✗ user-stream listen blocked: {exc}", err=True)
        sys.exit(2)
    except KeyboardInterrupt:
        click.echo("user-stream stopped.")


@cli.group("gate2-test")
def gate2_test() -> None:
    """Gate 2 testnet helper commands."""


@gate2_test.command("filters")
@click.option("--symbol", required=True, help="Symbol, e.g. ETHUSDT-PERP")
def gate2_filters(symbol: str) -> None:
    """Print Binance Futures testnet exchange filters for one symbol."""
    from execution.exchange_filters import BinanceExchangeInfoClient

    filters = BinanceExchangeInfoClient().symbol_filters(symbol)
    click.echo(f"symbol={filters.symbol}")
    click.echo(f"tick_size={filters.tick_size}")
    click.echo(f"step_size={filters.step_size}")
    click.echo(f"min_qty={filters.min_qty}")
    click.echo(f"min_notional={filters.min_notional}")


@gate2_test.command("bracket")
@click.option("--symbol", default="ETHUSDT-PERP", show_default=True)
@click.option("--side", type=click.Choice(["LONG", "SHORT"], case_sensitive=False), default="LONG")
@click.option("--strategy", default="gate2_test_signal", show_default=True)
@click.option("--trace-id", default=None, help="Optional trace_id; default auto-generated")
@click.option("--receiver-url", default="http://127.0.0.1:8765/signal", show_default=True)
@click.option("--submit", is_flag=True, help="POST the generated payload to receiver")
def gate2_bracket(
    symbol: str,
    side: str,
    strategy: str,
    trace_id: str | None,
    receiver_url: str,
    submit: bool,
) -> None:
    """Generate or submit a Gate 2 testnet bracket signal."""
    import json

    import httpx
    from ulid import ULID

    from execution.exchange_filters import BinanceExchangeInfoClient
    from execution.gate2_test import Gate2TestBuilder
    from execution.live_safety import LivePreflightError, assert_live_mode_enabled

    if submit:
        try:
            assert_live_mode_enabled()
        except LivePreflightError as exc:
            click.echo(f"✗ gate2-test bracket blocked: {exc}", err=True)
            sys.exit(2)

    builder = Gate2TestBuilder(filters=BinanceExchangeInfoClient())
    bracket = builder.build_bracket_payload(
        symbol=symbol,
        side=side.upper(),
        strategy=strategy,
        trace_id=trace_id or f"gate2-test-{ULID()}",
    )
    click.echo(f"mark_price={bracket.mark_price:.8f}")
    if not submit:
        click.echo(json.dumps(bracket.payload, indent=2, sort_keys=True))
        click.echo("dry_run=true")
        return

    try:
        resp = httpx.post(receiver_url, json=bracket.payload, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        click.echo(f"✗ gate2-test bracket submit failed: {exc}", err=True)
        sys.exit(2)
    click.echo(resp.text)


@gate2_test.command("report")
@click.option("--ticket-id", default=None, help="Execution ticket id")
@click.option("--event-id", default=None, help="Source event id")
def gate2_report(ticket_id: str | None, event_id: str | None) -> None:
    """Write a Gate 2 Markdown report from local DB state."""
    from reporting.gate2 import write_report

    try:
        path = write_report(ticket_id=ticket_id, event_id=event_id)
    except ValueError as exc:
        click.echo(f"✗ gate2 report failed: {exc}", err=True)
        sys.exit(2)
    click.echo(f"✓ gate2 report written → {path}")


@cli.group("gate-check")
def gate_check() -> None:
    """Run deterministic Gate 2.5 -> Gate 6 safety checks."""


@gate_check.command("gate25")
def gate_check_gate25() -> None:
    """Run Gate 2.5 fill lifecycle checks."""
    from execution.gate_checks import run_gate25_fill_lifecycle

    click.echo(run_gate25_fill_lifecycle().markdown())


@gate_check.command("gate3")
def gate_check_gate3() -> None:
    """Run Gate 3 restart/reconciliation safety checks."""
    from execution.gate_checks import run_gate3_restart_safety

    click.echo(run_gate3_restart_safety().markdown())


@gate_check.command("gate35")
def gate_check_gate35() -> None:
    """Run Gate 3.5 risk rejection matrix checks."""
    from execution.gate_checks import run_gate35_risk_matrix

    click.echo(run_gate35_risk_matrix().markdown())


@gate_check.command("gate5")
def gate_check_gate5() -> None:
    """Run Gate 5 mainnet readiness lock checks."""
    from execution.gate_checks import run_gate5_mainnet_preflight

    click.echo(run_gate5_mainnet_preflight().markdown())


@gate_check.command("gate6")
def gate_check_gate6() -> None:
    """Run Gate 6 micro-live canary scaffold checks."""
    from execution.gate_checks import run_gate6_micro_live_canary_scaffold

    click.echo(run_gate6_micro_live_canary_scaffold().markdown())


@gate_check.command("gate64")
def gate_check_gate64() -> None:
    """Run Gate 6.4 user-stream ingestion checks."""
    from execution.gate_checks import run_gate64_user_stream_ingestion

    click.echo(run_gate64_user_stream_ingestion().markdown())


@gate_check.command("gate66")
def gate_check_gate66() -> None:
    """Run Gate 6.6 event-driven risk checks."""
    from execution.gate_checks import run_gate66_event_driven_risk

    click.echo(run_gate66_event_driven_risk().markdown())


@gate_check.command("gate68")
def gate_check_gate68() -> None:
    """Run Gate 6.8 Go/No-Go readiness checks."""
    from execution.gate_checks import run_gate68_readiness_review

    click.echo(run_gate68_readiness_review().markdown())


@gate_check.command("all")
def gate_check_all() -> None:
    """Run all deterministic Gate 2.5 -> Gate 6 checks."""
    from execution.gate_checks import (
        run_gate3_restart_safety,
        run_gate5_mainnet_preflight,
        run_gate6_micro_live_canary_scaffold,
        run_gate25_fill_lifecycle,
        run_gate35_risk_matrix,
        run_gate64_user_stream_ingestion,
        run_gate66_event_driven_risk,
        run_gate68_readiness_review,
    )

    reports = [
        run_gate25_fill_lifecycle(),
        run_gate3_restart_safety(),
        run_gate35_risk_matrix(),
        run_gate5_mainnet_preflight(),
        run_gate6_micro_live_canary_scaffold(),
        run_gate64_user_stream_ingestion(),
        run_gate66_event_driven_risk(),
        run_gate68_readiness_review(),
    ]
    click.echo("\n\n".join(report.markdown() for report in reports))


@cli.group("gate6")
def gate6() -> None:
    """Gate 6 mainnet micro-live operator commands."""


@gate6.command("authorize")
@click.option("--symbol", required=True, help="Authorized symbol, e.g. ETHUSDT-PERP")
@click.option("--max-notional-usd", type=float, required=True)
@click.option("--max-leverage", type=float, required=True)
@click.option("--max-daily-loss-usd", type=float, required=True)
@click.option("--window-start", required=True, help="UTC ISO timestamp")
@click.option("--window-end", required=True, help="UTC ISO timestamp")
@click.option(
    "--strategy-scope",
    type=click.Choice(["manual_test_signal", "existing_strategy"], case_sensitive=False),
    required=True,
)
@click.option("--directions", type=click.Choice(["long", "short", "both"]), required=True)
@click.option("--operator", required=True, help="Operator name/handle")
@click.option("--auto-flatten/--manual-flatten", default=True, show_default=True)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("docs/gate-6-authorization.md"),
    show_default=True,
)
def gate6_authorize(
    symbol: str,
    max_notional_usd: float,
    max_leverage: float,
    max_daily_loss_usd: float,
    window_start: str,
    window_end: str,
    strategy_scope: str,
    directions: str,
    operator: str,
    auto_flatten: bool,
    output: Path,
) -> None:
    """Write the concrete Gate 6 authorization file."""
    from execution.gate6 import write_gate6_authorization

    if max_notional_usd <= 0 or max_leverage <= 0 or max_daily_loss_usd <= 0:
        click.echo("✗ Gate 6 limits must be positive.", err=True)
        sys.exit(2)
    path = write_gate6_authorization(
        symbol=symbol,
        max_notional_usd=max_notional_usd,
        max_leverage=max_leverage,
        max_daily_loss_usd=max_daily_loss_usd,
        window_start=window_start,
        window_end=window_end,
        strategy_scope=strategy_scope,
        directions=directions,
        auto_flatten=auto_flatten,
        operator=operator,
        output=output,
    )
    click.echo(f"✓ Gate 6 authorization written → {path}")


@gate6.command("preflight")
@click.option(
    "--symbol", "symbols", multiple=True, help="Symbol to check; default = micro_live allowlist"
)
def gate6_preflight(symbols: tuple[str, ...]) -> None:
    """Check mainnet account cleanliness before a Gate 6 canary."""
    from execution.gate6 import Gate6Error, Gate6Preflight
    from execution.live_safety import LivePreflightError

    try:
        result = Gate6Preflight().run(list(symbols) if symbols else None)
    except (Gate6Error, LivePreflightError) as exc:
        click.echo(f"✗ gate6 preflight blocked: {exc}", err=True)
        sys.exit(2)
    click.echo(result.markdown())


@gate6.command("closeout")
@click.option("--symbol", required=True, help="Symbol to close out, e.g. ETHUSDT-PERP")
@click.option("--yes", is_flag=True, help="Required acknowledgement")
def gate6_closeout(symbol: str, yes: bool) -> None:
    """Cancel, flatten, sync, reconcile, and write a Gate 6 closeout report."""
    from execution.gate6 import Gate6Error, run_gate6_closeout
    from execution.live_safety import LivePreflightError

    if not yes:
        click.echo("✗ gate6 closeout requires --yes.", err=True)
        sys.exit(2)
    try:
        result = run_gate6_closeout(symbol, yes=yes)
    except (Gate6Error, LivePreflightError) as exc:
        click.echo(f"✗ gate6 closeout blocked: {exc}", err=True)
        sys.exit(2)
    click.echo(f"✓ gate6 closeout report written → {result.report_path}")
    click.echo(f"  reconciliation={result.reconciliation.status}")


@gate6.command("submit-canary")
@click.option("--symbol", required=True, help="Symbol to submit, e.g. BTCUSDT-PERP")
@click.option("--side", type=click.Choice(["LONG", "SHORT"], case_sensitive=False), default="LONG")
@click.option("--entry-offset-pct", type=float, default=0.005, show_default=True)
@click.option("--stop-distance-pct", type=float, default=0.01, show_default=True)
@click.option("--take-profit-distance-pct", type=float, default=0.01, show_default=True)
@click.option("--yes", is_flag=True, help="Required acknowledgement")
def gate6_submit_canary(
    symbol: str,
    side: str,
    entry_offset_pct: float,
    stop_distance_pct: float,
    take_profit_distance_pct: float,
    yes: bool,
) -> None:
    """Submit one Gate 6 mainnet micro-live bracket canary."""
    from execution.gate6 import Gate6Error, submit_gate6_canary
    from execution.live_safety import LivePreflightError

    if not yes:
        click.echo("✗ gate6 submit-canary requires --yes.", err=True)
        sys.exit(2)
    try:
        result = submit_gate6_canary(
            symbol=symbol,
            side=side,
            entry_offset_pct=entry_offset_pct,
            stop_distance_pct=stop_distance_pct,
            take_profit_distance_pct=take_profit_distance_pct,
            yes=yes,
        )
    except (Gate6Error, LivePreflightError) as exc:
        click.echo(f"✗ gate6 submit-canary blocked: {exc}", err=True)
        sys.exit(2)
    click.echo(f"✓ gate6 canary submitted: {result.dispatch_ref}")
    click.echo(f"  ticket_id={result.ticket.ticket_id}")
    click.echo(f"  mark={result.mark_price:.8f}")
    click.echo(f"  entry={result.ticket.entry_price:g}")
    click.echo(f"  stop={result.ticket.stop_price:g}")
    click.echo(f"  take_profit={result.ticket.take_profit_price:g}")
    click.echo(f"  quantity={result.ticket.quantity:g}")
    click.echo(f"  notional={result.ticket.notional_usd:g}")


@gate6.command("repair-flat")
@click.option("--symbol", required=True, help="Symbol to repair, e.g. BTCUSDT-PERP")
@click.option("--yes", is_flag=True, help="Required acknowledgement")
def gate6_repair_flat(symbol: str, yes: bool) -> None:
    """Repair local live position state after verified exchange flatten."""
    from execution.gate6 import Gate6Error, repair_local_flat_after_closeout
    from execution.live_safety import LivePreflightError

    if not yes:
        click.echo("✗ gate6 repair-flat requires --yes.", err=True)
        sys.exit(2)
    try:
        result = repair_local_flat_after_closeout(symbol, yes=yes)
    except (Gate6Error, LivePreflightError) as exc:
        click.echo(f"✗ gate6 repair-flat blocked: {exc}", err=True)
        sys.exit(2)
    click.echo(
        f"✓ gate6 local flat repair complete: symbol={result.symbol} "
        f"closed_positions={result.closed_positions}"
    )
    if result.ticket_ids:
        click.echo(f"  tickets={','.join(result.ticket_ids)}")


@gate6.command("readiness")
@click.option("--symbol", "symbols", multiple=True, help="Symbol to review; repeatable")
@click.option("--recent-stream-minutes", default=30, show_default=True)
@click.option("--burn-in-hours", default=24, show_default=True)
@click.option("--require-go", is_flag=True, help="Exit non-zero when review is NO_GO")
def gate6_readiness(
    symbols: tuple[str, ...],
    recent_stream_minutes: int,
    burn_in_hours: int,
    require_go: bool,
) -> None:
    """Run Gate 6.8 Go/No-Go review from local DB evidence."""
    from execution.gate6_readiness import Gate6ReadinessReviewer

    report = Gate6ReadinessReviewer().run(
        symbols=list(symbols) if symbols else None,
        require_recent_stream_minutes=recent_stream_minutes,
        require_burn_in_hours=burn_in_hours,
    )
    click.echo(report.markdown())
    if require_go and report.status != "go":
        sys.exit(3)


@cli.command()
@click.option("--symbol", default=None, help="Symbol to flatten, e.g. BTCUSDT-PERP")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def flatten(symbol: str | None, yes: bool) -> None:
    """Force-close one configured-environment position via reduce-only market order."""
    from execution.binance_testnet_broker import BinanceFuturesBroker, LiveBrokerError
    from execution.live_safety import (
        LivePreflightError,
        assert_live_mode_enabled,
        load_live_execution_config,
    )

    if not symbol:
        click.echo("Phase 5/6 live flatten requires --symbol.", err=True)
        sys.exit(1)
    env = load_live_execution_config().environment
    if not yes:
        click.confirm(
            f"Flatten {env.upper()} position for {symbol} with reduce-only MARKET?", abort=True
        )
    try:
        assert_live_mode_enabled()
        ack = BinanceFuturesBroker().emergency_close_symbol(symbol)
    except (LiveBrokerError, LivePreflightError) as exc:
        click.echo(f"✗ flatten blocked: {exc}", err=True)
        sys.exit(2)
    if ack is None:
        click.echo(f"✓ no open {env} position for {symbol}")
        return
    click.echo(
        f"✓ flatten submitted for {symbol}: "
        f"clientOrderId={ack.client_order_id} exchangeOrderId={ack.exchange_order_id}"
    )


@cli.command()
@click.option("--interval", default=30, show_default=True, help="Evaluator tick seconds")
def run(interval: int) -> None:
    """Start the shadow-mode supervisor loop (7×24 foreground process)."""
    from scheduler.supervisor import Supervisor

    click.echo(f"▶ supervisor starting (interval={interval}s). Ctrl-C to stop.")
    Supervisor(eval_interval_sec=interval).run()


@cli.command()
def evaluate() -> None:
    """Run one position evaluator tick (useful for manual checks/cron)."""
    from execution.evaluator import PositionEvaluator

    results = PositionEvaluator().tick()
    if not results:
        click.echo("No open positions.")
        return
    for r in results:
        marker = r.triggered or "hold"
        price = f"{r.mark_price:.4f}" if r.mark_price is not None else "n/a"
        click.echo(f"  {r.symbol:20s} {marker:12s} mark={price}")


@cli.group()
def report() -> None:
    """Reporting commands — daily snapshot and weekly summary."""


@report.command("daily")
@click.option("--date", "date_str", default=None, help="ISO date (UTC); default = yesterday")
def report_daily(date_str: str | None) -> None:
    """Compute and persist the daily snapshot."""
    from datetime import date as date_cls

    from reporting.daily import write_snapshot

    target = date_cls.fromisoformat(date_str) if date_str else None
    stats = write_snapshot(target)
    click.echo(f"✓ daily snapshot written for {stats.date}")
    click.echo(
        f"  trades={stats.trade_count} net_pnl={stats.net_pnl:+,.2f} "
        f"equity={stats.ending_equity:,.2f}"
    )


@report.command("weekly")
@click.option("--end", "end_str", default=None, help="Week ending ISO date (UTC); default = today")
def report_weekly(end_str: str | None) -> None:
    """Generate the weekly Markdown report."""
    from datetime import date as date_cls

    from reporting.weekly import generate

    end = date_cls.fromisoformat(end_str) if end_str else None
    path = generate(end)
    click.echo(f"✓ weekly report written → {path}")


@report.command("performance")
def report_performance() -> None:
    """Print performance grouped by symbol, strategy, and regime."""
    from reporting.performance import render_markdown

    click.echo(render_markdown())


@report.command("backtest-compare")
@click.option(
    "--csv-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True
)
def report_backtest_compare(csv_dir: Path) -> None:
    """Compare CSV-backed historical backtest output to shadow positions."""
    from backtest.runner import CsvHistoricalPriceSource, run_backtest
    from reporting.backtest_compare import render_markdown

    trades = run_backtest(CsvHistoricalPriceSource(csv_dir))
    click.echo(render_markdown(trades))


@cli.command("telegram")
def telegram_bot() -> None:
    """Start the Telegram monitoring bot (long-poll, foreground)."""
    import os

    from telegram.auth import allowed_chat_ids
    from telegram.bot import Bot

    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        click.echo("❌ TELEGRAM_BOT_TOKEN not set. Add it to .env first.", err=True)
        sys.exit(2)
    admins = allowed_chat_ids()
    if not admins:
        click.echo(
            "❌ No admin chat IDs configured. "
            "Set TELEGRAM_ADMIN_CHAT_IDS or TELEGRAM_CHAT_ID in .env.",
            err=True,
        )
        sys.exit(2)

    click.echo(f"▶ telegram bot starting (admins={sorted(admins)}). Ctrl-C to stop.")
    Bot().run()


@cli.command()
def doctor() -> None:
    """Pre-flight check before starting shadow-mode services."""
    import os

    from storage.db import get_db, init_db

    click.echo("=== Dark Alpha doctor ===\n")

    db_path = Path(os.getenv("DB_PATH", "data/shadow.db"))
    existed = db_path.exists()
    init_db(db_path)  # idempotent — also applies any pending migrations
    click.echo(f"✓ DB {'exists' if existed else 'created'} at {db_path} (migrations applied)")

    try:
        with get_db(db_path) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        needed = {
            "setup_events",
            "execution_tickets",
            "positions",
            "daily_snapshots",
            "audit_log",
            "circuit_breaker_state",
            "equity_snapshots",
            "signal_journal",
            "signal_outcomes",
            "order_idempotency",
            "reconciliation_runs",
            "live_stream_events",
            "live_runtime_heartbeats",
            "gate6_readiness_reports",
        }
        missing = needed - tables
        if missing:
            click.echo(f"✗ missing tables: {missing}", err=True)
        else:
            click.echo(f"✓ all {len(needed)} tables present")
    except Exception as exc:  # noqa: BLE001
        click.echo(f"✗ DB check failed: {exc}", err=True)

    # Config dir
    cfg_dir = Path(os.getenv("CONFIG_DIR", "config"))
    for name in (
        "main.yaml",
        "validator.yaml",
        "risk_gate.yaml",
        "breakers.yaml",
        "sizer.gate1.yaml",
    ):
        p = cfg_dir / name
        click.echo(("✓" if p.exists() else "✗") + f" {p}")

    # Telegram creds (optional)
    tg_token = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    tg_chat = bool(os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_ADMIN_CHAT_IDS"))
    click.echo(
        f"{'✓' if tg_token else '○'} TELEGRAM_BOT_TOKEN "
        f"{'set' if tg_token else 'unset (alerts + bot disabled)'}"
    )
    click.echo(f"{'✓' if tg_chat else '○'} TELEGRAM_CHAT_ID {'set' if tg_chat else 'unset'}")

    # Kill switch
    ks = KillSwitch()
    click.echo(
        f"{'🔴' if ks.is_active() else '🟢'} kill switch "
        f"{'ACTIVE' if ks.is_active() else 'clear'} ({ks.sentinel_path()})"
    )

    click.echo("\nNext steps:")
    click.echo("  1) Point Dark Alpha's POSTBACK_URL at http://127.0.0.1:8765/signal")
    click.echo("  2) poetry run uvicorn signal_adapter.receiver:app --port 8765")
    click.echo("  3) poetry run dark-alpha run")
    click.echo("  4) (optional) poetry run dark-alpha telegram")


if __name__ == "__main__":
    cli()
