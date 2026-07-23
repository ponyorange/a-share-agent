from __future__ import annotations

from datetime import datetime, timezone
import math

import pytest
from pydantic import ValidationError

from app.advisor.committee.models import (
    AnalystReport,
    AnalystRole,
    BacktestVerdict,
    CommitteeRun,
    EvidenceRef,
    FinalDecision,
    RunStatus,
    TradeDirection,
    TradeProposal,
    VerdictStatus,
)


UTC_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def test_committee_run_requires_utc_identity_and_valid_version():
    run = CommitteeRun(
        user_id="user-1",
        run_id="run-1",
        strategy_version="v2",
        universe=("510300",),
        as_of=UTC_NOW,
        created_at=UTC_NOW,
        updated_at=UTC_NOW,
    )

    assert run.status is RunStatus.PENDING
    assert run.version == 1
    assert run.horizon.value == "next_day"

    for patch in (
        {"user_id": ""},
        {"version": 0},
        {"as_of": datetime(2026, 7, 21, 12, 0)},
        {"universe": ()},
    ):
        with pytest.raises(ValidationError):
            CommitteeRun.model_validate(run.model_dump() | patch)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_domain_models_reject_non_finite_numbers(value):
    with pytest.raises(ValidationError):
        AnalystReport(
            user_id="u",
            run_id="r",
            role=AnalystRole.QUANT,
            thesis="valid thesis",
            confidence=value,
            created_at=UTC_NOW,
        )

    with pytest.raises(ValidationError):
        BacktestVerdict(
            user_id="u",
            run_id="r",
            passed=True,
            score=0.5,
            metrics={"sharpe": value},
            summary="ok",
            created_at=UTC_NOW,
        )


def test_trade_proposal_uses_strong_direction_and_unit_interval():
    proposal = TradeProposal(
        user_id="u",
        run_id="r",
        symbol="510300",
        direction=TradeDirection.BUY,
        target_weight=0.25,
        confidence=1,
        rationale="trend",
        created_at=UTC_NOW,
    )
    assert proposal.direction is TradeDirection.BUY

    with pytest.raises(ValidationError):
        TradeProposal.model_validate(
            proposal.model_dump() | {"target_weight": 1.01}
        )
    with pytest.raises(ValidationError):
        TradeProposal.model_validate(
            proposal.model_dump() | {"direction": "long"}
        )


def test_trade_artifacts_reject_evidence_from_another_run():
    evidence = EvidenceRef(
        user_id="other",
        run_id="other-run",
        evidence_id="snapshot:item",
        source="fake",
        captured_at=UTC_NOW,
    )
    with pytest.raises(ValidationError):
        TradeProposal(
            user_id="u",
            run_id="r",
            symbol="510300",
            direction=TradeDirection.BUY,
            target_weight=0.2,
            confidence=0.5,
            rationale="x",
            evidence_refs=(evidence,),
        )
    with pytest.raises(ValidationError):
        FinalDecision(
            user_id="u",
            run_id="r",
            action=TradeDirection.HOLD,
            symbol="510300",
            target_weight=0,
            confidence=1,
            rationale="x",
            risk_status=VerdictStatus.REJECTED,
            evidence_refs=(evidence,),
        )


def test_committee_run_enforces_cross_field_state_invariants():
    base = {
        "user_id": "u",
        "run_id": "r",
        "strategy_version": "v1",
        "universe": ("510300",),
        "as_of": UTC_NOW,
        "created_at": UTC_NOW,
        "updated_at": UTC_NOW,
    }
    invalid_states = (
        {"status": RunStatus.COLLECTING},
        {
            "status": RunStatus.ANALYZING,
            "started_at": UTC_NOW,
        },
        {
            "status": RunStatus.COMPLETED,
            "started_at": UTC_NOW,
            "completed_at": UTC_NOW,
        },
        {
            "status": RunStatus.FAILED,
            "completed_at": UTC_NOW,
        },
        {
            "status": RunStatus.PENDING,
            "started_at": UTC_NOW,
        },
        {
            "status": RunStatus.PENDING,
            "snapshot_id": "a" * 64,
        },
    )
    for state in invalid_states:
        with pytest.raises(ValidationError):
            CommitteeRun(**base, **state)

    completed = CommitteeRun(
        **base,
        status=RunStatus.COMPLETED,
        started_at=UTC_NOW,
        completed_at=UTC_NOW,
        snapshot_id="a" * 64,
    )
    assert completed.status is RunStatus.COMPLETED


def test_open_metrics_container_is_deeply_immutable_and_json_serializable():
    metrics = {"segments": [{"sharpe": 1.2}]}
    verdict = BacktestVerdict(
        user_id="u",
        run_id="r",
        passed=True,
        score=0.5,
        metrics=metrics,
        summary="ok",
        proposal_hash="a" * 64,
        created_at=UTC_NOW,
    )
    metrics["segments"][0]["sharpe"] = math.nan

    assert verdict.metrics["segments"][0]["sharpe"] == 1.2
    with pytest.raises(TypeError):
        verdict.metrics["segments"][0]["sharpe"] = math.nan
    assert (
        verdict.model_dump(mode="json")["metrics"]["segments"][0]["sharpe"]
        == 1.2
    )
