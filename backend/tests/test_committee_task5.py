from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.advisor.committee.approval import (
    ApprovalRejected,
    execute_approval_once,
    validate_approval,
)
from app.advisor.committee.models import (
    BacktestVerdict,
    CommitteeRun,
    FinalDecision,
    RiskVerdict,
    RunStatus,
    TradeDirection,
    TradeProposal,
    VerdictStatus,
)
from app.advisor.committee.risk import proposal_semantics_hash
from app.advisor.committee.routes import CommitteeRunCreateBody
from app.advisor.committee.service import deterministic_job_id, merged_event_history


NOW = datetime(2026, 7, 22, 1, tzinfo=timezone.utc)


def _approved_artifacts(*, expires_at=NOW + timedelta(hours=1)):
    proposal = TradeProposal(
        user_id="u",
        run_id="r",
        strategy_id="advisor-score-v2",
        strategy_version="v1",
        symbol="510300",
        direction=TradeDirection.BUY,
        target_weight=0.2,
        confidence=0.8,
        rationale="通过",
        order_type="limit",
        limit_price=10,
        expires_at=expires_at,
        created_at=NOW - timedelta(minutes=5),
    )
    digest = proposal_semantics_hash((proposal,))
    backtest = BacktestVerdict(
        user_id="u",
        run_id="r",
        passed=True,
        score=0.8,
        metrics={"sample_count": 50, "trade_count": 5, "max_drawdown": 0.1},
        summary="通过",
        proposal_hash=digest,
        created_at=NOW,
    )
    risk = RiskVerdict(
        user_id="u",
        run_id="r",
        status=VerdictStatus.APPROVED,
        max_position=0.2,
        approved_weight=0.2,
        confidence=1,
        proposal_hash=digest,
        created_at=NOW,
    )
    decision = FinalDecision(
        user_id="u",
        run_id="r",
        action=TradeDirection.BUY,
        symbol="510300",
        target_weight=0.2,
        confidence=0.8,
        rationale="批准",
        risk_status=VerdictStatus.APPROVED,
        proposals=(proposal,),
        orders=(proposal,),
        proposal_hash=digest,
        created_at=NOW,
    )
    return proposal, backtest, risk, decision


def test_create_body_is_restricted_and_job_id_is_stable():
    body = CommitteeRunCreateBody(
        symbols=["510300"],
        boards=["etf"],
        horizon="next_day",
        strategy_version="v1",
    )
    assert body.symbols == ("510300",)
    assert body.boards == ("etf",)
    assert deterministic_job_id("u", "same-key") == deterministic_job_id(
        "u", "same-key"
    )
    assert deterministic_job_id("u", "same-key") != deterministic_job_id(
        "other", "same-key"
    )
    with pytest.raises(ValidationError):
        CommitteeRunCreateBody(
            symbols=["bad"],
            horizon="next_week",
            strategy_version="v1",
        )


def test_api_lifecycle_statuses_are_available_without_removing_graph_phases():
    assert RunStatus.CREATED.value == "created"
    assert RunStatus.QUEUED.value == "queued"
    assert RunStatus.RUNNING.value == "running"
    run = CommitteeRun(
        user_id="u",
        run_id="r",
        status=RunStatus.CREATED,
        strategy_version="v1",
        universe=("510300",),
        as_of=NOW,
        created_at=NOW,
        updated_at=NOW,
        idempotency_key="request-1",
    )
    assert run.idempotency_key == "request-1"


def test_event_history_resumes_and_deduplicates_mongo_fallback():
    mongo = [
        {"event_id": "1-0", "event_type": "created", "payload": {}},
        {"event_id": "2-0", "event_type": "running", "payload": {}},
    ]
    redis = [
        {"event_id": "2-0", "event_type": "running", "payload": {}},
        {"event_id": "3-0", "event_type": "completed", "payload": {}},
    ]
    assert [row["event_id"] for row in merged_event_history(mongo, redis, "1-0")] == [
        "2-0",
        "3-0",
    ]


def test_approval_rechecks_hash_expiry_price_and_account_version():
    proposal, backtest, risk, decision = _approved_artifacts()
    plan = validate_approval(
        run_status=RunStatus.COMPLETED,
        decision=decision,
        backtest=backtest,
        risk=risk,
        current_quotes={"510300": 10.1},
        frozen_account_version=7,
        current_account={
            "account_version": 7,
            "cash": 100_000,
            "equity": 100_000,
            "positions": [],
        },
        now=NOW,
        max_price_deviation=0.03,
    )
    assert plan.proposal_hash == proposal_semantics_hash((proposal,))
    assert plan.orders[0].symbol == "510300"

    with pytest.raises(ApprovalRejected, match="价格偏离"):
        validate_approval(
            run_status=RunStatus.COMPLETED,
            decision=decision,
            backtest=backtest,
            risk=risk,
            current_quotes={"510300": 11},
            frozen_account_version=7,
            current_account={"account_version": 7, "cash": 100_000, "equity": 100_000, "positions": []},
            now=NOW,
            max_price_deviation=0.03,
        )
    with pytest.raises(ApprovalRejected, match="账户版本"):
        validate_approval(
            run_status=RunStatus.COMPLETED,
            decision=decision,
            backtest=backtest,
            risk=risk,
            current_quotes={"510300": 10},
            frozen_account_version=7,
            current_account={"account_version": 8, "cash": 100_000, "equity": 100_000, "positions": []},
            now=NOW,
            max_price_deviation=0.03,
        )

    _, expired_backtest, expired_risk, expired = _approved_artifacts(
        expires_at=NOW - timedelta(seconds=1)
    )
    with pytest.raises(ApprovalRejected, match="过期"):
        validate_approval(
            run_status=RunStatus.COMPLETED,
            decision=expired,
            backtest=expired_backtest,
            risk=expired_risk,
            current_quotes={"510300": 10},
            frozen_account_version=7,
            current_account={"account_version": 7, "cash": 100_000, "equity": 100_000, "positions": []},
            now=NOW,
            max_price_deviation=0.03,
        )


def test_approval_rejects_hash_tampering_and_noncompleted_run():
    proposal, backtest, risk, decision = _approved_artifacts()
    tampered = proposal.model_copy(update={"target_weight": 0.1})
    tampered_decision = decision.model_copy(
        update={"proposals": (tampered,), "orders": (tampered,)}
    )
    with pytest.raises(ApprovalRejected, match="哈希"):
        validate_approval(
            run_status=RunStatus.COMPLETED,
            decision=tampered_decision,
            backtest=backtest,
            risk=risk,
            current_quotes={"510300": 10},
            frozen_account_version=7,
            current_account={"account_version": 7, "cash": 100_000, "equity": 100_000, "positions": []},
            now=NOW,
            max_price_deviation=0.03,
        )
    with pytest.raises(ApprovalRejected, match="完成"):
        validate_approval(
            run_status=RunStatus.FAILED,
            decision=decision,
            backtest=backtest,
            risk=risk,
            current_quotes={"510300": 10},
            frozen_account_version=7,
            current_account={"account_version": 7, "cash": 100_000, "equity": 100_000, "positions": []},
            now=NOW,
            max_price_deviation=0.03,
        )


def test_approval_journal_returns_same_batch_result_on_retry():
    class Journals:
        def __init__(self):
            self.doc = None

        def insert_one(self, document):
            if self.doc is not None:
                from pymongo.errors import DuplicateKeyError

                raise DuplicateKeyError("duplicate")
            self.doc = dict(document)

        def find_one(self, query):
            if self.doc and all(self.doc.get(k) == v for k, v in query.items()):
                return dict(self.doc)
            return None

        def find_one_and_update(self, query, update, **_kwargs):
            if not self.doc or any(self.doc.get(k) != v for k, v in query.items()):
                return None
            self.doc.update(update.get("$set", {}))
            return dict(self.doc)

    class DB:
        committee_approvals = Journals()

    calls = []

    def executor(**kwargs):
        calls.append(kwargs)
        return {"trades": [{"symbol": "510300"}], "account": {"cash": 80_000}}

    _proposal, _backtest, _risk, decision = _approved_artifacts()
    plan = validate_approval(
        run_status=RunStatus.COMPLETED,
        decision=decision,
        backtest=_backtest,
        risk=_risk,
        current_quotes={"510300": 10},
        frozen_account_version=7,
        current_account={
            "account_version": 7,
            "cash": 100_000,
            "equity": 100_000,
            "positions": [],
        },
        now=NOW,
        max_price_deviation=0.03,
    )
    first = execute_approval_once(
        DB(),
        user_id="u",
        run_id="r",
        idempotency_key="approve-1",
        plan=plan,
        executor=executor,
    )
    second = execute_approval_once(
        DB(),
        user_id="u",
        run_id="r",
        idempotency_key="approve-1",
        plan=plan,
        executor=executor,
    )
    assert first == second
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ({"suspended": True, "volume": 0}, "停牌"),
        (
            {
                "suspended": False,
                "volume": 100,
                "limit_up": True,
                "limit_down": False,
                "locked": True,
            },
            "涨停",
        ),
    ],
)
def test_approval_fails_closed_for_suspended_or_locked_market(status, message):
    _proposal, backtest, risk, decision = _approved_artifacts()
    market = {
        "quote": 10,
        "source": "fixed",
        "as_of": NOW,
        "limit_up": False,
        "limit_down": False,
        "locked": False,
        **status,
    }
    with pytest.raises(ApprovalRejected, match=message):
        validate_approval(
            run_status=RunStatus.COMPLETED,
            decision=decision,
            backtest=backtest,
            risk=risk,
            current_quotes={"510300": 10},
            current_market_status={"510300": market},
            frozen_account_version=0,
            current_account={
                "account_version": 0,
                "cash": 100_000,
                "equity": 100_000,
                "positions": [],
            },
            now=NOW,
            max_price_deviation=0.03,
        )
