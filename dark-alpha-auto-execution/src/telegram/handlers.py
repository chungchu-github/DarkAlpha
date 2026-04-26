"""Command handlers.

Every handler takes (reply: Callable[[str], None], args: list[str]).
Handlers never raise — they catch and format the error into the reply.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from safety.circuit_breaker import CircuitBreaker
from safety.kill_switch import get_kill_switch
from storage.db import get_db

Reply = Callable[[str], None]

HELP_TEXT = (
    "Dark Alpha commands\n"
    "  /status      — kill switch + breakers + open positions + today pnl\n"
    "  /positions   — list open positions\n"
    "  /pnl_today   — today's trade count and net pnl\n"
    "  /breakers    — circuit breaker state\n"
    "  /halt <reason> — activate kill switch\n"
    "  /resume      — deactivate kill switch\n"
    "  /help        — this message"
)


def handle_help(reply: Reply, _args: list[str]) -> None:
    reply(HELP_TEXT)


def handle_status(reply: Reply, _args: list[str]) -> None:
    ks = get_kill_switch()
    cb = CircuitBreaker()
    ks_line = "🔴 ACTIVE" if ks.is_active() else "🟢 clear"

    tripped = [
        f"  🔴 {s.name} → {s.action} (clear_at={s.clear_at or 'manual'})"
        for s in cb.all_states().values()
        if s.status == "tripped"
    ]

    try:
        open_count, today_pnl, today_trades = _open_and_today_pnl()
    except Exception as exc:  # noqa: BLE001
        reply(f"status error: {exc}")
        return

    lines = [
        f"Kill switch: {ks_line}",
        f"Open positions: {open_count}",
        f"Today: {today_trades} trade(s), net {today_pnl:+,.2f} USD",
    ]
    if tripped:
        lines.append("Breakers tripped:")
        lines.extend(tripped)
    else:
        lines.append("Breakers: all clear")
    reply("\n".join(lines))


def handle_halt(reply: Reply, args: list[str]) -> None:
    reason = " ".join(args) if args else "telegram halt"
    ks = get_kill_switch()
    ks.activate(reason=reason)
    reply(f"🛑 Kill switch ACTIVATED\nreason: {reason}\nsentinel: {ks.sentinel_path()}")


def handle_resume(reply: Reply, _args: list[str]) -> None:
    ks = get_kill_switch()
    if not ks.is_active():
        reply("Kill switch already clear — nothing to do.")
        return
    ks.deactivate()
    reply("✅ Kill switch cleared. System will resume on the next signal / tick.")


def handle_positions(reply: Reply, _args: list[str]) -> None:
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT position_id, symbol, direction, quantity, entry_price,
                       stop_price, take_profit_price
                  FROM positions
                 WHERE status='open'
                 ORDER BY opened_at
                """
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        reply(f"positions error: {exc}")
        return
    if not rows:
        reply("No open positions.")
        return
    lines = [f"Open positions ({len(rows)}):"]
    for r in rows:
        lines.append(
            f"  {r['symbol']} {r['direction']} qty={r['quantity']:.4f} "
            f"entry={r['entry_price']:.4f} stop={r['stop_price']:.4f} "
            f"tp={r['take_profit_price'] if r['take_profit_price'] is not None else 'n/a'}"
        )
    reply("\n".join(lines))


def handle_pnl_today(reply: Reply, _args: list[str]) -> None:
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       COALESCE(SUM(gross_pnl_usd), 0) AS gross,
                       COALESCE(SUM(fees_usd), 0)     AS fees,
                       COALESCE(SUM(net_pnl_usd), 0)  AS net
                  FROM positions
                 WHERE status='closed' AND substr(closed_at,1,10)=?
                """,
                (today,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        reply(f"pnl_today error: {exc}")
        return
    reply(
        f"Today ({today} UTC)\n"
        f"  trades: {int(row['n'])}\n"
        f"  gross:  {float(row['gross']):+,.2f}\n"
        f"  fees:   {float(row['fees']):,.2f}\n"
        f"  net:    {float(row['net']):+,.2f}"
    )


def handle_breakers(reply: Reply, _args: list[str]) -> None:
    cb = CircuitBreaker()
    states = cb.all_states()
    if not states:
        reply("No circuit breaker states recorded yet.")
        return
    lines = ["Circuit breakers:"]
    for s in states.values():
        icon = "🔴" if s.status == "tripped" else "🟢"
        line = f"  {icon} {s.name}: {s.status}"
        if s.status == "tripped":
            line += f" → {s.action} (clear_at={s.clear_at or 'manual'})"
        lines.append(line)
    reply("\n".join(lines))


DISPATCH: dict[str, Callable[[Reply, list[str]], None]] = {
    "/help": handle_help,
    "/start": handle_help,
    "/status": handle_status,
    "/halt": handle_halt,
    "/resume": handle_resume,
    "/positions": handle_positions,
    "/pnl_today": handle_pnl_today,
    "/breakers": handle_breakers,
}


def _open_and_today_pnl() -> tuple[int, float, int]:
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    with get_db() as conn:
        open_n = conn.execute("SELECT COUNT(*) AS n FROM positions WHERE status='open'").fetchone()[
            "n"
        ]
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(net_pnl_usd), 0) AS net "
            "FROM positions WHERE status='closed' AND substr(closed_at,1,10)=?",
            (today,),
        ).fetchone()
    return int(open_n), float(row["net"]), int(row["n"])
