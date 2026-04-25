"""Unit tests for ProposalCard → SetupEvent translation.

Tests cover all field mappings, edge cases, and invariants.
"""

import json
from pathlib import Path

import pytest

from signal_adapter.schemas import ProposalCardPayload, SetupEvent
from signal_adapter.translator import (
    _map_direction,
    _map_regime,
    _normalize_symbol,
    proposal_card_to_setup_event,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_card(**overrides: object) -> ProposalCardPayload:
    defaults = {
        "symbol": "BTCUSDT",
        "strategy": "vol_breakout_card",
        "side": "LONG",
        "entry": 94500.0,
        "stop": 93200.0,
        "leverage_suggest": 3,
        "position_usdt": 500.0,
        "max_risk_usdt": 25.0,
        "ttl_minutes": 15,
        "rationale": "BTC breakout above compression zone",
        "created_at": "2026-04-18T02:00:00+00:00",
        "priority": 40,
        "confidence": 78.5,
        "oi_status": "fresh",
        "trace_id": "abc123def456",
    }
    defaults.update(overrides)
    return ProposalCardPayload(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _normalize_symbol
# ---------------------------------------------------------------------------


def test_normalize_symbol_adds_perp_suffix() -> None:
    assert _normalize_symbol("BTCUSDT") == "BTCUSDT-PERP"


def test_normalize_symbol_idempotent() -> None:
    assert _normalize_symbol("BTCUSDT-PERP") == "BTCUSDT-PERP"


def test_normalize_symbol_underscore_perp() -> None:
    assert _normalize_symbol("ETHUSDT_PERP") == "ETHUSDT_PERP"


# ---------------------------------------------------------------------------
# _map_direction
# ---------------------------------------------------------------------------


def test_map_direction_long() -> None:
    assert _map_direction("LONG") == "long"


def test_map_direction_short() -> None:
    assert _map_direction("SHORT") == "short"


def test_map_direction_lowercase_long() -> None:
    assert _map_direction("long") == "long"


def test_map_direction_lowercase_short() -> None:
    assert _map_direction("short") == "short"


# ---------------------------------------------------------------------------
# _map_regime
# ---------------------------------------------------------------------------


def test_map_regime_lowercases() -> None:
    assert _map_regime("Vol_Breakout_Card") == "vol_breakout_card"


def test_map_regime_already_lower() -> None:
    assert _map_regime("fake_breakout_reversal") == "fake_breakout_reversal"


# ---------------------------------------------------------------------------
# proposal_card_to_setup_event — field mappings
# ---------------------------------------------------------------------------


def test_event_id_uses_trace_id() -> None:
    card = make_card(trace_id="my-trace-id")
    event = proposal_card_to_setup_event(card)
    assert event.event_id == "my-trace-id"


def test_event_id_generated_when_trace_id_empty() -> None:
    card = make_card(trace_id="")
    event = proposal_card_to_setup_event(card)
    assert len(event.event_id) > 0
    assert event.event_id != ""


def test_timestamp_preserved() -> None:
    card = make_card(created_at="2026-04-18T02:00:00+00:00")
    event = proposal_card_to_setup_event(card)
    assert event.timestamp == "2026-04-18T02:00:00+00:00"


def test_symbol_normalized() -> None:
    card = make_card(symbol="ETHUSDT")
    event = proposal_card_to_setup_event(card)
    assert event.symbol == "ETHUSDT-PERP"


def test_setup_type_always_active() -> None:
    event = proposal_card_to_setup_event(make_card())
    assert event.setup_type == "active"


def test_direction_long() -> None:
    event = proposal_card_to_setup_event(make_card(side="LONG"))
    assert event.direction == "long"


def test_direction_short() -> None:
    event = proposal_card_to_setup_event(make_card(side="SHORT"))
    assert event.direction == "short"


def test_regime_from_strategy() -> None:
    event = proposal_card_to_setup_event(make_card(strategy="Vol_Breakout_Card"))
    assert event.regime == "vol_breakout_card"


def test_today_decision_from_rationale() -> None:
    card = make_card(rationale="Breakout confirmed")
    event = proposal_card_to_setup_event(card)
    assert event.today_decision == "Breakout confirmed"


def test_ranking_score_scaled_from_confidence() -> None:
    event = proposal_card_to_setup_event(make_card(confidence=78.5))
    assert abs(event.ranking_score - 7.85) < 1e-9


def test_ranking_score_clamped_at_10() -> None:
    event = proposal_card_to_setup_event(make_card(confidence=105.0))
    assert event.ranking_score == 10.0


def test_ranking_score_clamped_at_0() -> None:
    event = proposal_card_to_setup_event(make_card(confidence=-5.0))
    assert event.ranking_score == 0.0


def test_trigger_entry_price() -> None:
    event = proposal_card_to_setup_event(make_card(entry=94500.0))
    assert event.trigger is not None
    assert event.trigger.price_level == 94500.0


def test_trigger_default_timeframe() -> None:
    event = proposal_card_to_setup_event(make_card())
    assert event.trigger is not None
    assert event.trigger.timeframe == "15m"


def test_invalidation_stop_price() -> None:
    event = proposal_card_to_setup_event(make_card(stop=93200.0))
    assert event.invalidation is not None
    assert event.invalidation.price_level == 93200.0


def test_metadata_fields_present() -> None:
    card = make_card(
        leverage_suggest=3,
        position_usdt=500.0,
        max_risk_usdt=25.0,
        ttl_minutes=15,
        priority=40,
        oi_status="fresh",
    )
    event = proposal_card_to_setup_event(card)
    assert event.metadata["leverage_suggest"] == 3
    assert event.metadata["position_usdt"] == 500.0
    assert event.metadata["max_risk_usdt"] == 25.0
    assert event.metadata["ttl_minutes"] == 15
    assert event.metadata["priority"] == 40
    assert event.metadata["oi_status"] == "fresh"


def test_returns_setup_event_instance() -> None:
    event = proposal_card_to_setup_event(make_card())
    assert isinstance(event, SetupEvent)


# ---------------------------------------------------------------------------
# Fixture file round-trip
# ---------------------------------------------------------------------------


def test_fixture_file_translates_correctly() -> None:
    raw = json.loads((FIXTURES_DIR / "sample_proposal_card.json").read_text())
    card = ProposalCardPayload(**raw)
    event = proposal_card_to_setup_event(card)

    assert event.event_id == "abc123def456"
    assert event.symbol == "BTCUSDT-PERP"
    assert event.direction == "long"
    assert event.ranking_score == pytest.approx(7.85)
    assert event.trigger is not None
    assert event.trigger.price_level == 94500.0
    assert event.invalidation is not None
    assert event.invalidation.price_level == 93200.0


# ---------------------------------------------------------------------------
# SetupEvent schema validation
# ---------------------------------------------------------------------------


def test_setup_event_rejects_invalid_setup_type() -> None:
    with pytest.raises(ValueError):
        SetupEvent(
            event_id="x",
            timestamp="2026-04-18T00:00:00Z",
            symbol="BTCUSDT-PERP",
            setup_type="invalid_type",
            direction="long",
            regime="vol_breakout",
            today_decision="ok",
            ranking_score=7.0,
            trigger=None,
            invalidation=None,
        )


def test_setup_event_rejects_invalid_direction() -> None:
    with pytest.raises(ValueError):
        SetupEvent(
            event_id="x",
            timestamp="2026-04-18T00:00:00Z",
            symbol="BTCUSDT-PERP",
            setup_type="active",
            direction="sideways",
            regime="vol_breakout",
            today_decision="ok",
            ranking_score=7.0,
            trigger=None,
            invalidation=None,
        )


def test_setup_event_allows_none_direction() -> None:
    event = SetupEvent(
        event_id="x",
        timestamp="2026-04-18T00:00:00Z",
        symbol="BTCUSDT-PERP",
        setup_type="active",
        direction=None,
        regime="no_action",
        today_decision="neutral",
        ranking_score=0.0,
        trigger=None,
        invalidation=None,
    )
    assert event.direction is None
