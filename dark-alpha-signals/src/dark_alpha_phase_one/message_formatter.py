from __future__ import annotations

from html import escape
from typing import Any


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _to_int(value: Any) -> int | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _price_decimals(payload: dict[str, Any]) -> int:
    precision = _to_int(payload.get("price_precision"))
    if precision is not None and precision >= 0:
        return precision

    tick_size = _to_float(payload.get("tick_size"))
    if tick_size is not None and tick_size > 0:
        as_text = f"{tick_size:.16f}".rstrip("0")
        if "." in as_text:
            return len(as_text.split(".", maxsplit=1)[1])
        return 0

    return 2


def _format_number(value: Any, decimals: int = 2) -> str:
    parsed = _to_float(value)
    if parsed is None:
        return "na"
    text = f"{parsed:,.{max(decimals, 0)}f}"
    if decimals == 0:
        text = text.split(".", maxsplit=1)[0]
    return text


def _format_percent(value: Any) -> str:
    parsed = _to_float(value)
    if parsed is None:
        return "na"
    clamped = max(0.0, min(100.0, parsed))
    if abs(clamped - int(clamped)) < 1e-9:
        return f"{int(clamped)}%"
    return f"{clamped:.2f}%"


def _is_test_payload(payload: dict[str, Any]) -> bool:
    strategy = str(payload.get("strategy") or "").lower()
    priority = _to_int(payload.get("priority")) or 0
    return "test" in strategy or "dryrun" in strategy or priority >= 9000


def _data_health(payload: dict[str, Any]) -> tuple[str, str]:
    raw = payload.get("data_health")
    if not isinstance(raw, dict):
        return "na", "na"
    status = str(raw.get("status") or "na")
    reason = str(raw.get("reason") or "na")
    return status, reason


def _label_side(side: str) -> tuple[str, str]:
    side_upper = side.upper()
    if side_upper == "LONG":
        return "🟢", "做多信號"
    if side_upper == "SHORT":
        return "🔴", "做空信號"
    return "⚪", "交易信號"


def tradingview_url(symbol: str, exchange: str = "BINANCE") -> str:
    return f"https://www.tradingview.com/symbols/{symbol.upper()}/?exchange={exchange.upper()}"


def parse_callback_data(callback_data: str) -> tuple[str, str, str] | None:
    parts = callback_data.split(":", maxsplit=2)
    if len(parts) != 3:
        return None
    action, symbol, trace_id = parts
    if action not in {"copy_levels", "detail"}:
        return None
    return action, symbol.upper(), trace_id


def build_signal_keyboard(payload: dict[str, Any]) -> dict[str, object]:
    symbol = str(payload.get("symbol") or "NA").upper()
    exchange = str(payload.get("exchange") or "BINANCE")
    trace_id = str(payload.get("trace_id") or "na")

    copy_data = f"copy_levels:{symbol}:{trace_id}"[:64]
    detail_data = f"detail:{symbol}:{trace_id}"[:64]

    return {
        "inline_keyboard": [
            [
                {"text": "📈 TradingView", "url": tradingview_url(symbol=symbol, exchange=exchange)},
                {"text": "📋 複製入場/止損", "callback_data": copy_data},
            ],
            [
                {"text": "🧾 詳細資料", "callback_data": detail_data},
            ],
        ]
    }


def format_signal_message(payload: dict[str, Any]) -> tuple[str, str]:
    symbol = escape(str(payload.get("symbol") or "na").upper())
    side = str(payload.get("side") or "").upper()
    emoji, side_text = _label_side(side)
    is_test = _is_test_payload(payload)
    test_suffix = "（測試）" if is_test else ""

    decimals = _price_decimals(payload)
    entry = _format_number(payload.get("entry"), decimals=decimals)
    stop = _format_number(payload.get("stop"), decimals=decimals)
    take_profit = _format_number(payload.get("take_profit"), decimals=decimals)

    leverage = _to_int(payload.get("leverage_suggest"))
    leverage_text = f"{leverage}x" if leverage is not None else "na"
    position = _format_number(payload.get("position_usdt"), decimals=2)
    risk = _format_number(payload.get("max_risk_usdt"), decimals=2)
    ttl = _to_int(payload.get("ttl_minutes"))
    ttl_text = f"{ttl} 分鐘" if ttl is not None else "na"
    confidence = _format_percent(payload.get("confidence"))
    health_status, health_reason = _data_health(payload)
    risk_level = escape(str(payload.get("risk_level") or "na"))
    invalid_condition = escape(str(payload.get("invalid_condition") or "na"))

    rationale = str(payload.get("rationale") or "na").strip() or "na"
    rationale = rationale[:160] + ("…" if len(rationale) > 160 else "")
    rationale = escape(rationale)

    lines = [
        f"<b>{emoji} {symbol} {side_text}{test_suffix}</b>",
        f"📍 <b>入場：</b>{entry}",
        f"🛑 <b>止損：</b>{stop}",
        f"🎯 <b>止盈：</b>{take_profit}",
        f"⚡ <b>槓桿：</b>{escape(leverage_text)}",
        "",
        f"💰 <b>倉位：</b>{position} USDT" if position != "na" else "💰 <b>倉位：</b>na",
        f"🎯 <b>最大風險：</b>{risk} USDT" if risk != "na" else "🎯 <b>最大風險：</b>na",
        f"⏳ <b>有效：</b>{ttl_text}",
        f"📊 <b>信心：</b>{confidence}",
        f"⚠️ <b>風險：</b>{risk_level}",
        f"🩺 <b>資料：</b>{escape(health_status)} / {escape(health_reason)}",
        f"🚫 <b>失效：</b>{invalid_condition}",
        "",
        "🧠 <b>理由：</b>",
        rationale,
        "",
        "──────────────",
    ]

    if is_test:
        lines.append("#TEST #DRYRUN")

    return "\n".join(lines), "HTML"


def format_copy_levels_message(payload: dict[str, Any]) -> str:
    symbol = str(payload.get("symbol") or "na").upper()
    side = str(payload.get("side") or "na").upper()
    decimals = _price_decimals(payload)
    entry = _format_number(payload.get("entry"), decimals=decimals)
    stop = _format_number(payload.get("stop"), decimals=decimals)
    take_profit = _format_number(payload.get("take_profit"), decimals=decimals)
    return f"<code>{escape(symbol)} {escape(side)}\nENTRY {entry}\nSTOP {stop}\nTAKE_PROFIT {take_profit}</code>"


def format_detail_message(payload: dict[str, Any]) -> str:
    decimals = _price_decimals(payload)
    lines = [
        "<b>🧾 詳細資料</b>",
        f"strategy: {escape(str(payload.get('strategy') or 'na'))}",
        f"side: {escape(str(payload.get('side') or 'na'))}",
        f"entry: {_format_number(payload.get('entry'), decimals=decimals)}",
        f"stop: {_format_number(payload.get('stop'), decimals=decimals)}",
        f"take_profit: {_format_number(payload.get('take_profit'), decimals=decimals)}",
        f"leverage: {escape(str(payload.get('leverage_suggest') or 'na'))}",
        f"position: {_format_number(payload.get('position_usdt'), decimals=2)}",
        f"risk: {_format_number(payload.get('max_risk_usdt'), decimals=2)}",
        f"ttl: {escape(str(payload.get('ttl_minutes') or 'na'))}",
        f"confidence: {_format_percent(payload.get('confidence'))}",
        f"risk_level: {escape(str(payload.get('risk_level') or 'na'))}",
        f"invalid_condition: {escape(str(payload.get('invalid_condition') or 'na'))}",
        f"oi_status: {escape(str(payload.get('oi_status') or 'na'))}",
    ]
    data_health = payload.get("data_health")
    if isinstance(data_health, dict):
        lines.extend(
            [
                f"data_health.status: {escape(str(data_health.get('status') or 'na'))}",
                f"data_health.reason: {escape(str(data_health.get('reason') or 'na'))}",
                f"price_age_ms: {escape(str(data_health.get('price_raw_age_ms') or 'na'))}",
                f"kline_recv_age_ms: {escape(str(data_health.get('kline_recv_raw_age_ms') or 'na'))}",
                f"funding_age_ms: {escape(str(data_health.get('funding_raw_age_ms') or 'na'))}",
                f"oi_age_ms: {escape(str(data_health.get('oi_raw_age_ms') or 'na'))}",
            ]
        )
    return "\n".join(lines)
