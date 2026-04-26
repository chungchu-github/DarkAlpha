"""Tests for executable Gate 2.5 -> Gate 6 safety checks."""

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


def test_gate25_fill_lifecycle_check_passes() -> None:
    report = run_gate25_fill_lifecycle()

    assert report.status == "ok"
    assert [step.name for step in report.steps] == [
        "entry partial fill updates local position",
        "entry full fill opens local live position",
        "stop fill closes local live position",
        "take-profit fill closes local live position",
        "cancel/sync/reconcile leaves no orphan orders",
        "emergency flatten submits reduce-only market close",
    ]


def test_gate3_restart_safety_check_passes() -> None:
    report = run_gate3_restart_safety()

    assert report.status == "ok"
    assert any(
        step.name == "duplicate live ticket would be blocked before broker" for step in report.steps
    )


def test_gate35_risk_matrix_check_passes() -> None:
    report = run_gate35_risk_matrix()

    assert report.status == "ok"
    assert any(
        step.name == "mainnet notional cap rejects oversized ticket" for step in report.steps
    )


def test_gate5_mainnet_preflight_check_passes() -> None:
    assert run_gate5_mainnet_preflight().status == "ok"


def test_gate6_micro_live_canary_scaffold_check_passes() -> None:
    assert run_gate6_micro_live_canary_scaffold().status == "ok"


def test_gate64_user_stream_ingestion_check_passes() -> None:
    assert run_gate64_user_stream_ingestion().status == "ok"


def test_gate66_event_driven_risk_check_passes() -> None:
    assert run_gate66_event_driven_risk().status == "ok"


def test_gate68_readiness_review_check_passes() -> None:
    assert run_gate68_readiness_review().status == "ok"
