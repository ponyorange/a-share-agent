from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import time
from typing import Any

import pytest

from app.advisor.committee.agents import (
    ChatModelRoleRunner,
    ModelResponse,
    RoleAgentExecutor,
    RoleRequest,
    TraderOutput,
)
from app.advisor.committee.chat_stream import message_id_for
from app.advisor.committee.graph import (
    CommitteeDependencies,
    build_committee_graph,
    next_trading_day,
)
from app.advisor.committee.models import (
    AnalystReport,
    AnalystRole,
    BacktestVerdict,
    DebateTurn,
    RiskVerdict,
    VerdictStatus,
)
from app.advisor.committee.snapshot import MarketSnapshot, SnapshotItem
from app.advisor.committee.risk import proposal_semantics_hash
from app.advisor.committee.state import (
    BudgetLimits,
    merge_analyst_reports,
    merge_debate_turns,
    merge_model_calls,
)
from app.advisor.committee.tools import snapshot_view


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        snapshot_id="a" * 64,
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
                content={"510300": {"close": 4.2}},
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
                content={"sessions": ["2026-07-23", "2026-07-24"]},
            ),
            SnapshotItem(
                name="news",
                source="fake",
                critical=False,
                captured_at=NOW,
                data_as_of=NOW,
                content={"headline": "测试新闻"},
            ),
            SnapshotItem(
                name="fundamentals",
                source="fake",
                critical=False,
                captured_at=NOW,
                data_as_of=NOW,
                content={"pe": 10},
            ),
        ),
        created_at=NOW,
    )


class FakeRunner:
    def __init__(
        self,
        *,
        invalid_once: set[str] | None = None,
        fail: set[str] | None = None,
        delay: dict[str, float] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.invalid_once = invalid_once or set()
        self.fail = fail or set()
        self.delay = delay or {}
        self._attempts: Counter[str] = Counter()

    async def __call__(self, request):
        role = request.role
        self._attempts[role] += 1
        self.calls.append(
            {
                "role": role,
                "tier": request.model_tier,
                "idempotency_key": request.idempotency_key,
                "prompt": request.prompt,
                "attempt": request.attempt,
                "round_index": request.round_index,
            }
        )
        await asyncio.sleep(self.delay.get(role, 0))
        if role in self.fail:
            raise RuntimeError(f"{role} failed")
        if role in self.invalid_once and self._attempts[role] == 1:
            return ModelResponse(
                content={"bad": "shape"},
                model_name=f"fake-{request.model_tier}",
                input_tokens=10,
                output_tokens=5,
            )
        evidence_id = f"{'a' * 64}:market"
        if role in {"fundamental", "technical", "news", "quant"}:
            body = {
                "thesis": f"{role} thesis",
                "confidence": 0.6,
                "evidence_ids": [evidence_id],
                "symbols": ["510300"],
            }
        elif role in {"bull", "bear"}:
            body = {
                "argument": f"{role} argument",
                "confidence": 0.6,
                "evidence_ids": [evidence_id],
            }
        elif role == "trader":
            body = {
                "symbol": "510300",
                "direction": "buy",
                "target_weight": 0.2,
                "confidence": 0.6,
                "rationale": "综合报告",
                "evidence_ids": [evidence_id],
            }
        else:
            body = {
                "action": "buy",
                "symbol": "510300",
                "target_weight": 0.2,
                "confidence": 0.6,
                "rationale": "主席裁决",
                "evidence_ids": [evidence_id],
            }
        body["chat_message"] = f"{role}权威消息"
        return ModelResponse(
            content=body,
            model_name=f"fake-{request.model_tier}",
            input_tokens=10,
            output_tokens=5,
            tool_names=("snapshot_view",),
        )


def initial_state(**patch: Any) -> dict[str, Any]:
    state = {
        "run_id": "run-1",
        "user_id": "user-1",
        "snapshot": snapshot(),
        "max_debate_rounds": 2,
        "limits": BudgetLimits(
            max_calls=20,
            max_tokens=10_000,
            node_timeout_seconds=1,
            total_timeout_seconds=5,
        ),
    }
    state.update(patch)
    return state


def build(fake: FakeRunner, **deps: Any):
    executor = RoleAgentExecutor(fake)

    async def default_backtest(proposal, context):
        return BacktestVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            passed=True,
            score=0.7,
            metrics={},
            summary="pass",
            proposal_hash=proposal_semantics_hash(proposal),
        )

    async def default_risk(proposal, backtest, context):
        return RiskVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            status=VerdictStatus.APPROVED,
            max_position=0.3,
            approved_weight=proposal.target_weight,
            confidence=0.8,
            proposal_hash=proposal_semantics_hash(proposal),
        )

    dependencies = CommitteeDependencies(
        role_executor=executor,
        backtest=deps.get("backtest", default_backtest),
        risk=deps.get("risk", default_risk),
        portfolio_backtest=deps.get("portfolio_backtest"),
        portfolio_risk=deps.get("portfolio_risk"),
    )
    return build_committee_graph(dependencies, checkpointer=deps.get("checkpointer"))


def invoke(graph, state):
    return asyncio.run(graph.ainvoke(state))


def completed_messages(state):
    return [
        event["payload"]
        for event in state["events"]
        if event["event_type"] == "message_completed"
    ]


def test_graph_emits_authoritative_completed_messages_for_visible_nodes():
    result = invoke(build(FakeRunner()), initial_state(attempt=2))
    messages = completed_messages(result)

    assert {message["node"] for message in messages} >= {
        "prepare",
        "fundamental",
        "technical",
        "news",
        "quant",
        "bull",
        "bear",
        "trader",
        "backtest",
        "risk",
        "chair",
    }
    assert all(
        message["status"] == "completed" and message["content"]
        for message in messages
    )
    assert next(
        message for message in messages if message["node"] == "prepare"
    )["content"] == "已冻结 1 个标的的市场快照。"
    assert next(
        message for message in messages if message["node"] == "backtest"
    )["card_kind"] == "backtest_verdict"
    assert next(
        message for message in messages if message["node"] == "chair"
    )["content"] == "chair权威消息"
    assert all(
        event["event_type"] not in {"message_started", "message_delta"}
        for event in result["events"]
    )
    assert next(
        message for message in messages if message["node"] == "technical"
    )["message_id"] == message_id_for("run-1", 2, "technical")
    assert [
        message["round"]
        for message in messages
        if message["node"] == "bull"
    ] == [1, 2]
    assert len(
        {
            message["message_id"]
            for message in messages
            if message["node"] == "bull"
        }
    ) == 2


def test_completed_payloads_are_stable_across_same_message_reexecution():
    first = completed_messages(invoke(build(FakeRunner()), initial_state()))
    second = completed_messages(invoke(build(FakeRunner()), initial_state()))

    assert second == first


def test_parallel_analyst_messages_use_zero_sequence_without_loss():
    messages = completed_messages(
        invoke(build(FakeRunner()), initial_state())
    )
    analyst_messages = [
        message
        for message in messages
        if message["node"] in {"fundamental", "technical", "news", "quant"}
    ]

    assert {message["node"] for message in analyst_messages} == {
        "fundamental",
        "technical",
        "news",
        "quant",
    }
    assert len(analyst_messages) == 4
    assert all(message["sequence"] == 0 for message in analyst_messages)


def test_graph_forwards_business_attempt_and_debate_round_to_llm_calls():
    fake = FakeRunner()
    invoke(build(fake), initial_state(attempt=2))

    assert all(call["attempt"] == 2 for call in fake.calls)
    assert [
        call["round_index"]
        for call in fake.calls
        if call["role"] == "bull"
    ] == [1, 2]
    assert [
        call["round_index"]
        for call in fake.calls
        if call["role"] == "bear"
    ] == [1, 2]


def test_degraded_role_emits_safe_nonconclusion_message():
    class InvalidNewsSymbols(FakeRunner):
        async def __call__(self, request):
            response = await super().__call__(request)
            if request.role == "news":
                body = dict(response.content)
                body["symbols"] = [""]
                body["chat_message"] = "不应泄露的角色结论"
                return ModelResponse(
                    content=body,
                    model_name=response.model_name,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
            return response

    result = invoke(build(InvalidNewsSymbols()), initial_state())
    message = next(
        item for item in completed_messages(result) if item["node"] == "news"
    )

    assert message["status"] == "degraded"
    assert message["content"] == "news 节点执行失败，已降级处理。"
    assert "不应泄露" not in message["content"]


def test_failed_role_emits_safe_nonconclusion_message():
    result = invoke(build(FakeRunner(fail={"trader"})), initial_state())
    message = next(
        item for item in completed_messages(result) if item["node"] == "trader"
    )

    assert message["status"] == "failed"
    assert message["content"] == "交易员节点执行失败，未生成交易结论。"
    assert "trader failed" not in message["content"]


def test_parallel_analysts_fan_in_and_two_debate_rounds():
    fake = FakeRunner(delay={role: 0.04 for role in ("fundamental", "technical", "news", "quant")})
    graph = build(fake)

    started = time.monotonic()
    result = invoke(graph, initial_state())
    elapsed = time.monotonic() - started

    assert elapsed < 0.14
    assert tuple(report["role"] for report in result["analyst_reports"]) == (
        "fundamental",
        "technical",
        "news",
        "quant",
    )
    assert result["debate_round"] == 2
    assert [turn["speaker"] for turn in result["debate_turns"]] == [
        "bull",
        "bear",
        "bull",
        "bear",
    ]
    assert result["status"] == "completed"
    assert all(
        call["tier"] == "quick"
        for call in fake.calls
        if call["role"] in {"fundamental", "technical", "news", "quant"}
    )
    assert all(
        call["tier"] == "deep"
        for call in fake.calls
        if call["role"] in {"bull", "bear", "trader", "chair"}
    )


def test_checkpoint_reducers_dedupe_serialized_lists_and_sort_deterministically():
    report = AnalystReport(
        user_id="u",
        run_id="r",
        role=AnalystRole.QUANT,
        thesis="x",
        confidence=0.5,
    )
    fundamental = report.model_copy(
        update={"role": AnalystRole.FUNDAMENTAL, "thesis": "f"}
    )
    merged = merge_analyst_reports(
        [report.model_dump(mode="json")],
        (fundamental.model_dump(mode="json"), report.model_dump(mode="json")),
    )
    assert [item["role"] for item in merged] == ["fundamental", "quant"]

    turns = merge_debate_turns(
        [
            DebateTurn(
                user_id="u",
                run_id="r",
                sequence=2,
                speaker=AnalystRole.BEAR,
                argument="b",
                confidence=0.5,
            ).model_dump(mode="json")
        ],
        (
            DebateTurn(
                user_id="u",
                run_id="r",
                sequence=1,
                speaker=AnalystRole.BULL,
                argument="a",
                confidence=0.5,
            ).model_dump(mode="json"),
        ),
    )
    assert [(turn["sequence"], turn["speaker"]) for turn in turns] == [
        (1, "bull"),
        (2, "bear"),
    ]


def test_checkpoint_reducer_rejects_conflicting_duplicate_role():
    report = AnalystReport(
        user_id="u",
        run_id="r",
        role=AnalystRole.QUANT,
        thesis="x",
        confidence=0.5,
    )
    conflict = report.model_copy(update={"thesis": "different"})
    with pytest.raises(ValueError, match="conflicting analyst role"):
        merge_analyst_reports(
            [report.model_dump(mode="json")],
            [conflict.model_dump(mode="json")],
        )


def test_model_call_reducer_dedupes_cached_replay_record():
    original = {
        "call_id": "r:technical:attempt:1:model",
        "role": "technical",
        "model_tier": "quick",
        "model_name": "fake",
        "elapsed_ms": 5,
        "input_tokens": 2,
        "output_tokens": 1,
        "token_usage_known": True,
        "tool_names": [],
        "evidence_ids": [],
        "attempt": 1,
        "status": "success",
        "error": None,
        "cached": False,
        "schema_version": 1,
    }
    replay = original | {"elapsed_ms": 0, "cached": True}
    assert merge_model_calls([original], (replay,)) == [original]


def test_snapshot_role_view_is_deeply_read_only():
    view = snapshot_view(snapshot(), "technical")
    with pytest.raises(TypeError):
        view.evidence[0].content["510300"]["close"] = 99


def test_structured_output_retries_once_and_records_no_chain_of_thought():
    fake = FakeRunner(invalid_once={"technical"})
    result = invoke(build(fake), initial_state())

    assert Counter(call["role"] for call in fake.calls)["technical"] == 2
    assert result["status"] == "completed"
    assert result["model_calls"]
    assert all("chain" not in str(record).lower() for record in result["model_calls"])
    assert all(
        record["evidence_ids"]
        for record in result["model_calls"]
        if record["status"] == "success"
    )
    assert any(record["status"] == "invalid" for record in result["model_calls"])


def test_runner_failure_with_unknown_usage_aborts_fail_closed():
    result = invoke(build(FakeRunner(fail={"news"})), initial_state())

    assert result["status"] == "aborted"
    assert result["final_decision"]["action"] == "hold"
    assert any(error["node"] == "news" and not error["critical"] for error in result["errors"])


def test_critical_failure_aborts_conservatively():
    result = invoke(build(FakeRunner(fail={"trader"})), initial_state())

    assert result["status"] == "aborted"
    assert result["final_decision"]["action"] == "hold"
    assert any(error["node"] == "trader" and error["critical"] for error in result["errors"])


def test_risk_veto_cannot_be_overridden_by_chair():
    async def veto(proposal, backtest, context):
        return RiskVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            status=VerdictStatus.REJECTED,
            max_position=0,
            approved_weight=0,
            confidence=1,
            reasons=("veto",),
            proposal_hash=proposal_semantics_hash(proposal),
        )

    result = invoke(build(FakeRunner(), risk=veto), initial_state())

    assert result["status"] == "completed"
    assert result["final_decision"]["action"] == "hold"
    assert result["final_decision"]["target_weight"] == 0
    assert result["final_decision"]["risk_status"] == "rejected"


def test_chair_cannot_change_risk_reviewed_trade_semantics():
    class MutatingChair(FakeRunner):
        async def __call__(self, request):
            response = await super().__call__(request)
            if request.role == "chair":
                body = dict(response.content)
                body.update(
                    {
                        "symbol": "159915",
                        "action": "sell",
                        "target_weight": 0.1,
                    }
                )
                return ModelResponse(
                    content=body,
                    model_name=response.model_name,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
            return response

    result = invoke(build(MutatingChair()), initial_state())

    assert result["status"] == "aborted"
    assert result["final_decision"]["action"] == "hold"
    assert result["final_decision"]["symbol"] == "510300"
    assert any(error["code"] == "chair_trade_semantics_changed" for error in result["errors"])


def test_final_decision_locks_entire_reviewed_portfolio():
    class PortfolioTrader(FakeRunner):
        async def __call__(self, request):
            response = await super().__call__(request)
            if request.role != "trader":
                return response
            evidence_id = f"{'a' * 64}:market"
            return ModelResponse(
                content={
                    "chat_message": "组合交易方案",
                    "trade_proposals": [
                        {
                            "symbol": "510300",
                            "direction": "buy",
                            "target_weight": 0.2,
                            "confidence": 0.8,
                            "rationale": "a",
                            "evidence_ids": [evidence_id],
                        },
                        {
                            "symbol": "159915",
                            "direction": "buy",
                            "target_weight": 0.1,
                            "confidence": 0.7,
                            "rationale": "b",
                            "evidence_ids": [evidence_id],
                        },
                    ]
                },
                model_name=response.model_name,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )

    async def portfolio_backtest(items, context):
        return BacktestVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            passed=True,
            score=1,
            metrics={},
            summary="portfolio",
            proposal_hash=proposal_semantics_hash(items),
        )

    async def portfolio_risk(items, backtest, context):
        return RiskVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            status=VerdictStatus.APPROVED,
            max_position=0.3,
            approved_weight=0.3,
            confidence=1,
            proposal_hash=proposal_semantics_hash(items),
        )

    result = invoke(
        build(
            PortfolioTrader(),
            portfolio_backtest=portfolio_backtest,
            portfolio_risk=portfolio_risk,
        ),
        initial_state(),
    )
    assert len(result["trade_proposals"]) == 2
    assert len(result["final_decision"]["proposals"]) == 2
    assert len(result["final_decision"]["orders"]) == 2
    assert result["final_decision"]["symbol"] == "510300"


def test_proposal_and_decision_preserve_frozen_evidence_refs():
    result = invoke(build(FakeRunner()), initial_state())

    proposal_ids = {ref["evidence_id"] for ref in result["trade_proposal"]["evidence_refs"]}
    decision_ids = {ref["evidence_id"] for ref in result["final_decision"]["evidence_refs"]}
    assert proposal_ids == {f"{'a' * 64}:market"}
    assert proposal_ids <= decision_ids


@pytest.mark.parametrize(
    ("limits", "delay"),
    [
        (
            BudgetLimits(
                max_calls=2,
                max_tokens=10_000,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            {},
        ),
        (
            BudgetLimits(
                max_calls=20,
                max_tokens=10_000,
                node_timeout_seconds=0.01,
                total_timeout_seconds=5,
            ),
            {"fundamental": 0.05},
        ),
    ],
)
def test_budget_or_timeout_aborts_conservatively(limits, delay):
    result = invoke(build(FakeRunner(delay=delay)), initial_state(limits=limits))
    assert result["status"] == "aborted"
    assert result["final_decision"]["action"] == "hold"


def test_injected_backtest_obeys_node_timeout():
    async def slow_backtest(proposal, context):
        await asyncio.sleep(0.05)
        return BacktestVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            passed=True,
            score=1,
            summary="late",
            proposal_hash=proposal_semantics_hash(proposal),
        )

    limits = BudgetLimits(
        max_calls=20,
        max_tokens=10_000,
        node_timeout_seconds=0.01,
        total_timeout_seconds=5,
    )
    result = invoke(
        build(FakeRunner(), backtest=slow_backtest),
        initial_state(limits=limits),
    )
    assert result["status"] == "aborted"
    assert any(error["node"] == "backtest" for error in result["errors"])


def test_sync_provider_is_rejected_before_side_effect():
    side_effects = []

    def sync_backtest(proposal, context):
        side_effects.append("executed")
        return BacktestVerdict(
            user_id=context.user_id,
            run_id=context.run_id,
            passed=True,
            score=1,
            summary="must not run",
            proposal_hash=proposal_semantics_hash(proposal),
        )

    result = invoke(build(FakeRunner(), backtest=sync_backtest), initial_state())
    assert result["status"] == "aborted"
    assert side_effects == []
    assert any(error["node"] == "backtest" for error in result["errors"])


def test_invalid_noncritical_analyst_symbols_degrade_without_escaping():
    class InvalidNewsSymbols(FakeRunner):
        async def __call__(self, request):
            response = await super().__call__(request)
            if request.role == "news":
                body = dict(response.content)
                body["symbols"] = [""]
                return ModelResponse(
                    content=body,
                    model_name=response.model_name,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
            return response

    result = invoke(build(InvalidNewsSymbols()), initial_state())
    assert result["status"] == "completed"
    assert result["degraded"] is True
    assert all(report["role"] != "news" for report in result["analyst_reports"])
    assert any(error["node"] == "news" for error in result["errors"])


def test_compile_receives_optional_checkpointer(monkeypatch):
    captured = {}

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph

    original = StateGraph.compile

    def spy(self, *args, **kwargs):
        captured["checkpointer"] = kwargs.get("checkpointer")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(StateGraph, "compile", spy)
    marker = InMemorySaver()
    build(FakeRunner(), checkpointer=marker)
    assert captured["checkpointer"] is marker


def test_default_chat_runner_forwards_committee_model_tier_config(monkeypatch):
    captured = {}

    class Message:
        content = '{"symbol":"510300","direction":"hold","target_weight":0,"confidence":1,"rationale":"保守","evidence_ids":[]}'
        usage_metadata = {}
        response_metadata = {"model_name": "fast"}

    class Model:
        model_name = "fast"

        def bind(self, **kwargs):
            return self

        async def ainvoke(self, messages):
            return Message()

    def fake_builder(user_id, **kwargs):
        captured.update(user_id=user_id, **kwargs)
        return Model()

    monkeypatch.setattr("app.advisor.committee.agents.build_chat_model", fake_builder)
    runner = ChatModelRoleRunner(
        committee_config={"models": {"quick": "fast", "deep": "reasoner"}}
    )
    asyncio.run(
        runner(
            RoleRequest(
                user_id="u",
                run_id="r",
                role="trader",
                prompt="prompt",
                output_schema=TraderOutput,
                model_tier="quick",
                idempotency_key="r:trader",
                timeout_seconds=1,
                deadline_at=time.time() + 1,
            )
        )
    )
    assert captured["tier"] == "quick"
    assert captured["committee_config"]["models"]["deep"] == "reasoner"


def test_next_trading_day_skips_weekend_and_injected_holiday():
    friday = datetime(2026, 7, 24, 7, tzinfo=timezone.utc)
    assert next_trading_day(friday) == datetime(
        2026, 7, 27, 7, tzinfo=timezone.utc
    )
    assert next_trading_day(
        friday,
        holidays={datetime(2026, 7, 27, tzinfo=timezone.utc).date()},
    ) == datetime(2026, 7, 28, 7, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="calendar"):
        next_trading_day(friday, sessions=frozenset())
