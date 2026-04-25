from __future__ import annotations

from dark_alpha_phase_one.message_formatter import (
    build_signal_keyboard,
    format_signal_message,
    parse_callback_data,
)


def test_format_signal_message_test_payload_contains_required_fields() -> None:
    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry": 68139.75,
        "stop": 68003.47,
        "leverage_suggest": 50,
        "position_usdt": 5000,
        "max_risk_usdt": 10,
        "ttl_minutes": 5,
        "confidence": 100,
        "rationale": "TEST DRYRUN emit for pipeline verification",
        "strategy": "test_emit_dryrun",
        "priority": 10000,
    }

    text, parse_mode = format_signal_message(payload)

    assert parse_mode == "HTML"
    assert "🟢" in text
    assert "BTCUSDT" in text
    assert "68,139.75" in text
    assert "68,003.47" in text
    assert "50x" in text
    assert "#TEST #DRYRUN" in text


def test_format_signal_message_missing_fields_does_not_crash() -> None:
    text, parse_mode = format_signal_message({"symbol": "ETHUSDT"})

    assert parse_mode == "HTML"
    assert "ETHUSDT" in text
    assert "na" in text


def test_build_signal_keyboard_has_required_structure() -> None:
    payload = {"symbol": "BTCUSDT", "trace_id": "abc", "exchange": "BINANCE"}
    keyboard = build_signal_keyboard(payload)

    assert "inline_keyboard" in keyboard
    rows = keyboard["inline_keyboard"]
    assert len(rows) == 2
    assert len(rows[0]) == 2
    assert rows[0][0]["url"] == "https://www.tradingview.com/symbols/BTCUSDT/?exchange=BINANCE"
    assert rows[0][1]["callback_data"] == "copy_levels:BTCUSDT:abc"
    assert rows[1][0]["callback_data"] == "detail:BTCUSDT:abc"


def test_parse_callback_data_copy_levels() -> None:
    action, symbol, trace_id = parse_callback_data("copy_levels:BTCUSDT:abc") or (None, None, None)
    assert action == "copy_levels"
    assert symbol == "BTCUSDT"
    assert trace_id == "abc"
