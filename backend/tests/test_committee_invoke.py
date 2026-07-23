from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

from langgraph.checkpoint.memory import InMemorySaver
import pytest

import app.advisor.committee as committee
from app.advisor.committee.agents import ModelResponse, RoleAgentExecutor
from app.advisor.committee.graph import CommitteeDependencies
from app.advisor.committee.models import BacktestVerdict, RiskVerdict, VerdictStatus
from app.advisor.committee.snapshot import MarketSnapshot, SnapshotItem
from app.advisor.committee.risk import proposal_semantics_hash
from app.advisor.committee.state import BudgetLimits


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def frozen_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        snapshot_id="b" * 64,
        as_of=NOW,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
        items=(
            SnapshotItem(
                name="kline",
                source="fake",
                critical=True,
                captured_at=NOW,
                data_as_of=NOW,
                content={"510300": {"close": 4}},
            ),
            SnapshotItem(
                name="market",
                source="fake",
                critical=True,
                captured_at=NOW,
                data_as_of=NOW,
                content={"score": 0.5},
            ),
            SnapshotItem(
                name="trading_calendar",
                source="fixed",
                critical=True,
                captured_at=NOW,
                data_as_of=NOW,
                content={"sessions": ["2026-07-22", "2026-07-23"]},
            ),
        ),
        created_at=NOW,
    )


class AsyncRunner:
    def __init__(self):
        self.calls = 0

    async def __call__(self, request):
        self.calls += 1
        evidence_id = f"{'b' * 64}:market"
        if request.role in {"fundamental", "technical", "news", "quant"}:
            body = {
                "thesis": request.role,
                "confidence": 0.5,
                "evidence_ids": [evidence_id],
                "symbols": ["510300"],
            }
        elif request.role in {"bull", "bear"}:
            body = {
                "argument": request.role,
                "confidence": 0.5,
                "evidence_ids": [evidence_id],
            }
        elif request.role == "trader":
            body = {
                "symbol": "510300",
                "direction": "buy",
                "target_weight": 0.2,
                "confidence": 0.5,
                "rationale": "trade",
                "evidence_ids": [evidence_id],
            }
        else:
            body = {
                "symbol": "510300",
                "action": "buy",
                "target_weight": 0.2,
                "confidence": 0.5,
                "rationale": "chair",
                "evidence_ids": [evidence_id],
            }
        body["chat_message"] = f"{request.role}观点"
        return ModelResponse(
            content=body,
            model_name="fake",
            input_tokens=2,
            output_tokens=1,
        )


def dependencies(runner):
    async def backtest(proposal, context):
        return BacktestVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            passed=True,
            score=1,
            summary="pass",
            proposal_hash=proposal_semantics_hash(proposal),
        )

    async def risk(proposal, backtest, context):
        return RiskVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            status=VerdictStatus.APPROVED,
            max_position=0.2,
            approved_weight=0.2,
            confidence=1,
            proposal_hash=proposal_semantics_hash(proposal),
        )

    return CommitteeDependencies(
        role_executor=RoleAgentExecutor(runner),
        backtest=backtest,
        risk=risk,
    )


def initial_state():
    return {
        "user_id": "u",
        "run_id": "r",
        "snapshot": frozen_snapshot(),
        "max_debate_rounds": 1,
        "limits": BudgetLimits(
            max_calls=20,
            max_tokens=100_000,
            node_timeout_seconds=1,
            total_timeout_seconds=5,
        ),
    }


def completed_messages(state):
    return [
        event["payload"]
        for event in state["events"]
        if event["event_type"] == "message_completed"
    ]


def test_checkpoint_replay_does_not_duplicate_completed_messages():
    runner = AsyncRunner()
    invoker = committee.create_committee_invoker(
        dependencies=dependencies(runner),
        checkpointer=InMemorySaver(),
    )

    first = committee.invoke_committee(initial_state(), invoker=invoker)
    second = committee.invoke_committee(
        {"user_id": "u", "run_id": "r"},
        invoker=invoker,
    )

    first_messages = completed_messages(first)
    second_messages = completed_messages(second)
    assert first_messages
    assert second_messages == first_messages
    assert len(second_messages) == len(
        {message["message_id"] for message in second_messages}
    )


def test_package_invoke_uses_stable_thread_and_replays_without_model_calls():
    runner = AsyncRunner()
    invoker = committee.create_committee_invoker(
        dependencies=dependencies(runner),
        checkpointer=InMemorySaver(),
    )

    first = committee.invoke_committee(initial_state(), invoker=invoker)
    call_count = runner.calls
    second = committee.invoke_committee(
        {"user_id": "u", "run_id": "r"},
        invoker=invoker,
    )

    assert first["status"] == "completed"
    assert second == first
    assert runner.calls == call_count
    assert committee.committee_thread_id("u", "r") == "committee:u:r"
    json.dumps(first, ensure_ascii=False, allow_nan=False)


def test_terminal_checkpoint_rejects_replacement_initial_fields():
    runner = AsyncRunner()
    invoker = committee.create_committee_invoker(
        dependencies=dependencies(runner),
        checkpointer=InMemorySaver(),
    )
    committee.invoke_committee(initial_state(), invoker=invoker)
    call_count = runner.calls

    for replacement in (
        {"snapshot": frozen_snapshot()},
        {"snapshot_request": {"symbol": "other"}},
        {"limits": BudgetLimits()},
        {"max_debate_rounds": 2},
    ):
        with pytest.raises(ValueError, match="checkpoint recovery"):
            committee.invoke_committee(
                {"user_id": "u", "run_id": "r", **replacement},
                invoker=invoker,
            )
    assert runner.calls == call_count


def test_nonterminal_checkpoint_rejects_replacement_before_resume():
    class Snapshot:
        values = {"user_id": "u", "run_id": "r", "status": "running"}

    class Graph:
        invoked = False

        async def aget_state(self, config):
            return Snapshot()

        async def ainvoke(self, payload, config):
            self.invoked = True
            return payload

    graph = Graph()
    invoker = committee.CommitteeInvoker(
        graph=graph,
        has_checkpointer=True,
        default_limits=BudgetLimits(),
        default_debate_rounds=2,
    )
    with pytest.raises(ValueError, match="checkpoint recovery"):
        committee.invoke_committee(
            {
                "user_id": "u",
                "run_id": "r",
                "limits": BudgetLimits(max_calls=10),
            },
            invoker=invoker,
        )
    assert graph.invoked is False


def test_package_ainvoke_returns_json_safe_state():
    runner = AsyncRunner()
    invoker = committee.create_committee_invoker(dependencies=dependencies(runner))
    result = asyncio.run(
        committee.ainvoke_committee(initial_state(), invoker=invoker)
    )
    assert result["final_decision"]["symbol"] == "510300"
    assert result["final_decision"]["action"] == "buy"
    assert result["budget"]["calls"] == runner.calls
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_invoker_applies_configured_default_debate_rounds():
    runner = AsyncRunner()
    invoker = committee.create_committee_invoker(
        dependencies=dependencies(runner),
        committee_config={
            "budget": {
                "max_calls": 20,
                "max_tokens": 100_000,
                "node_timeout_seconds": 1,
                "total_timeout_seconds": 5,
            },
            "max_debate_rounds": 1,
        },
    )
    state = initial_state()
    state.pop("max_debate_rounds")
    result = committee.invoke_committee(state, invoker=invoker)
    assert result["debate_round"] == 1


@pytest.mark.parametrize(
    "forbidden",
    [
        "status",
        "final_decision",
        "risk_verdict",
        "backtest_verdict",
        "trade_proposal",
        "deadline_at_epoch",
        "analyst_reports",
        "unexpected",
    ],
)
def test_public_invoke_rejects_derived_or_unknown_initial_fields(forbidden):
    runner = AsyncRunner()
    invoker = committee.create_committee_invoker(
        dependencies=dependencies(runner)
    )
    state = initial_state() | {forbidden: "forged"}
    with pytest.raises(ValueError, match="initial committee input"):
        committee.invoke_committee(state, invoker=invoker)
    assert runner.calls == 0


def test_invalid_snapshot_becomes_structured_abort():
    runner = AsyncRunner()
    invoker = committee.create_committee_invoker(
        dependencies=dependencies(runner)
    )
    state = initial_state() | {"snapshot": {"invalid": "snapshot"}}
    result = committee.invoke_committee(state, invoker=invoker)
    assert result["status"] == "aborted"
    assert result["final_decision"]["action"] == "hold"
    assert any(error["node"] == "snapshot" for error in result["errors"])
    assert runner.calls == 0
