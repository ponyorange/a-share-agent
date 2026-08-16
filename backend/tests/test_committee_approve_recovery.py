from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from tests.committee_http_app import leftover_committee_app
from app.advisor import paper
from app.advisor.config_loader import load_config
from app.advisor.committee import routes
from app.advisor.committee.approval import (
    approval_plan_hash,
    validate_approval,
)
from app.advisor.committee.models import RunStatus
from tests.test_committee_task5 import NOW, _approved_artifacts
from tests.test_paper_atomic_flow import Database, _run


def _setup(monkeypatch):
    database = Database()
    database.paper_trades.fail_next_update = True
    with pytest.raises(RuntimeError, match="simulated"):
        _run(
            monkeypatch,
            database,
            key="approve-key",
            orders=[
                {
                    "symbol": "510300",
                    "side": "buy",
                    "qty": 2000,
                    "price": 10,
                    "name": "ETF",
                }
            ],
        )
    proposal, backtest, risk, decision = _approved_artifacts(
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
    )
    plan = validate_approval(
        run_status=RunStatus.COMPLETED,
        decision=decision,
        backtest=backtest,
        risk=risk,
        current_quotes={"510300": 10},
        current_market_status={
            "510300": {
                "quote": 10,
                "volume": 1000,
                "suspended": False,
                "limit_up": False,
                "limit_down": False,
                "locked": False,
                "source": "fixed",
                "as_of": datetime.now(timezone.utc),
            }
        },
        frozen_account_version=0,
        current_account={
            "account_version": 0,
            "cash": 100_000,
            "equity": 100_000,
            "positions": [],
        },
        now=NOW,
        max_price_deviation=0.03,
        execution_settings=dict(
            (load_config().get("committee") or {}).get("backtest") or {}
        ),
    )
    database.committee_approvals.insert_one(
        {
            "user_id": "u",
            "idempotency_key": "approve-key",
            "run_id": "r",
            "status": "failed",
            "decision_hash": plan.decision_hash,
            "proposal_hash": plan.proposal_hash,
            "account_version": plan.account_version,
            "plan_hash": approval_plan_hash(plan),
        }
    )
    artifacts = [
        {"kind": "final_decision", "payload": decision.model_dump(mode="json")},
        {"kind": "backtest_verdict", "payload": backtest.model_dump(mode="json")},
        {"kind": "risk_verdict", "payload": risk.model_dump(mode="json")},
        {
            "kind": "snapshot",
            "payload": {
                "items": [
                    {
                        "name": "portfolio_account",
                        "content": {"account_version": 0},
                    },
                    {
                        "name": "kline",
                        "content": {
                            "510300": {"bars": [{"close": 10}]}
                        },
                    },
                ]
            },
        },
        {
            "kind": "order_preview",
            "artifact_id": "preview",
            "payload": plan.model_dump(mode="json"),
        },
    ]

    class Repository:
        def get_detail(self, user_id, run_id):
            assert (user_id, run_id) == ("u", "r")
            return SimpleNamespace(
                run=SimpleNamespace(status=RunStatus.COMPLETED),
                artifacts=artifacts,
            )

    monkeypatch.setattr(routes, "_repository", lambda: Repository())
    monkeypatch.setattr(routes, "get_db", lambda: database)
    monkeypatch.setattr(
        routes,
        "_current_market_status",
        lambda symbol: {
            "quote": 10,
            "volume": 1000,
            "suspended": False,
            "limit_up": False,
            "limit_down": False,
            "locked": False,
            "source": "fixed",
            "as_of": datetime.now(timezone.utc),
        },
    )
    monkeypatch.setattr(paper, "get_db", lambda: database)
    monkeypatch.setattr(routes, "get_account_snapshot_atomic", paper.get_account_snapshot_atomic)
    test_app = leftover_committee_app()
    test_app.dependency_overrides[get_current_user] = lambda: {
        "id": "u",
        "username": "u",
    }
    return database, plan, test_app


def _approve(plan, test_app):
    return TestClient(test_app).post(
        "/api/advisor/committee/runs/r/approve",
        headers={"Idempotency-Key": "approve-key"},
        json={
            "preview_id": "preview",
            "decision_hash": plan.decision_hash,
            "proposal_hash": plan.proposal_hash,
            "account_version": plan.account_version,
            "confirm": True,
        },
    )


def test_approve_api_recovers_same_key_without_duplicate(monkeypatch):
    database, plan, test_app = _setup(monkeypatch)
    try:
        response = _approve(plan, test_app)
        replay = _approve(plan, test_app)
    finally:
        test_app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    assert replay.status_code == 200
    assert response.json()["approval"] == replay.json()["approval"]
    assert len(
        [item for item in database.paper_trades.docs if not item.get("voided")]
    ) == 1


@pytest.mark.parametrize("kind", ["lineage", "state", "quote"])
def test_approve_recovery_rejects_mismatch(monkeypatch, kind):
    database, plan, test_app = _setup(monkeypatch)
    if kind == "lineage":
        mutation = database.paper_mutations.find_one(
            {"external_idempotency_key": "approve-key"}
        )
        database.paper_mutations.update_one(
            {"mutation_id": mutation["mutation_id"]},
            {"$set": {"type": "other-run"}},
        )
    elif kind == "state":
        database.paper_accounts.update_one(
            {"user_id": "u"},
            {"$set": {"cash": 99_999}},
        )
    else:
        monkeypatch.setattr(
            routes,
            "_current_market_status",
            lambda symbol: {
                "quote": 11,
                "volume": 1000,
                "suspended": False,
                "limit_up": False,
                "limit_down": False,
                "locked": False,
                "source": "fixed",
                "as_of": datetime.now(timezone.utc),
            },
        )
    try:
        response = _approve(plan, test_app)
    finally:
        test_app.dependency_overrides.clear()
    assert response.status_code == 409
