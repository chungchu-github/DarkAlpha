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
    """Show current kill switch and circuit breaker state."""
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

    click.echo()


@cli.command()
def flatten() -> None:
    """Force-close all open positions (Phase 5+ only — requires live broker)."""
    click.echo("⚠️  flatten is not yet implemented. Live broker is Phase 5.")
    click.echo("   To manually close positions, log into your exchange directly.")
    sys.exit(1)


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
    click.echo(f"  trades={stats.trade_count} net_pnl={stats.net_pnl:+,.2f} "
               f"equity={stats.ending_equity:,.2f}")


@report.command("weekly")
@click.option("--end", "end_str", default=None, help="Week ending ISO date (UTC); default = today")
def report_weekly(end_str: str | None) -> None:
    """Generate the weekly Markdown report."""
    from datetime import date as date_cls

    from reporting.weekly import generate

    end = date_cls.fromisoformat(end_str) if end_str else None
    path = generate(end)
    click.echo(f"✓ weekly report written → {path}")


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
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        needed = {"setup_events", "execution_tickets", "positions",
                  "daily_snapshots", "audit_log", "circuit_breaker_state",
                  "equity_snapshots"}
        missing = needed - tables
        if missing:
            click.echo(f"✗ missing tables: {missing}", err=True)
        else:
            click.echo(f"✓ all {len(needed)} tables present")
    except Exception as exc:  # noqa: BLE001
        click.echo(f"✗ DB check failed: {exc}", err=True)

    # Config dir
    cfg_dir = Path(os.getenv("CONFIG_DIR", "config"))
    for name in ("main.yaml", "validator.yaml", "risk_gate.yaml",
                 "breakers.yaml", "sizer.gate1.yaml"):
        p = cfg_dir / name
        click.echo(("✓" if p.exists() else "✗") + f" {p}")

    # Telegram creds (optional)
    tg_token = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    tg_chat = bool(os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_ADMIN_CHAT_IDS"))
    click.echo(f"{'✓' if tg_token else '○'} TELEGRAM_BOT_TOKEN "
               f"{'set' if tg_token else 'unset (alerts + bot disabled)'}")
    click.echo(f"{'✓' if tg_chat else '○'} TELEGRAM_CHAT_ID "
               f"{'set' if tg_chat else 'unset'}")

    # Kill switch
    ks = KillSwitch()
    click.echo(f"{'🔴' if ks.is_active() else '🟢'} kill switch "
               f"{'ACTIVE' if ks.is_active() else 'clear'} ({ks.sentinel_path()})")

    click.echo("\nNext steps:")
    click.echo("  1) Point Dark Alpha's POSTBACK_URL at http://127.0.0.1:8765/signal")
    click.echo("  2) poetry run uvicorn signal_adapter.receiver:app --port 8765")
    click.echo("  3) poetry run dark-alpha run")
    click.echo("  4) (optional) poetry run dark-alpha telegram")


if __name__ == "__main__":
    cli()
