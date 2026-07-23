from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import math

import pandas as pd
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ValidationError

import app.advisor.committee.agents as agents
from app.advisor.committee.agents import RoleAgentExecutor, TraderOutput
from app.advisor.committee.backtest import (
    AKQUANT_VERSION,
    BacktestSettings,
    DataValidationError,
    _akquant_payload,
    _akquant_validate_worker,
    create_backtest_provider,
    run_portfolio_backtest,
)
from app.advisor.committee.dependencies import create_production_dependencies
from app.advisor.committee.models import (
    BacktestVerdict,
    RiskVerdict,
    TradeDirection,
    TradeProposal,
    VerdictStatus,
)
from app.advisor.committee.risk import RiskInputs, proposal_semantics_hash
from app.advisor.committee.state import CommitteeState


UTC = timezone.utc
AS_OF = datetime(2026, 1, 12, 15, tzinfo=UTC)


class DefaultAstreamModel(BaseChatModel):
    response_text: str
    call_count: int = 0

    @property
    def _llm_type(self):
        return "default-astream-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.call_count += 1
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=self.response_text))
            ]
        )


class NativeAstreamModel(BaseChatModel):
    chunks: tuple[AIMessageChunk, ...]
    stream_call_count: int = 0

    @property
    def _llm_type(self):
        return "native-astream-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("native stream must not invoke")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.stream_call_count += 1
        for chunk in self.chunks:
            yield ChatGenerationChunk(message=chunk)


def proposal(
    symbol: str = "510300",
    *,
    weight: float = 0.4,
    created_at: datetime = datetime(2026, 1, 5, 15, tzinfo=UTC),
    **patch,
) -> TradeProposal:
    values = {
        "user_id": "u",
        "run_id": "r",
        "symbol": symbol,
        "direction": TradeDirection.BUY,
        "target_weight": weight,
        "confidence": 0.8,
        "rationale": "fixed",
        "order_type": "limit",
        "time_in_force": "day",
        "limit_price": 20,
        "stop_price": 8,
        "expires_at": AS_OF + timedelta(days=1),
        "created_at": created_at,
    }
    values.update(patch)
    return TradeProposal(**values)


def frame(
    symbol: str = "510300",
    closes=(10.0, 10.2, 10.4, 10.6, 10.8, 11.0),
    *,
    asset_type: str = "etf",
    is_st: bool | None = None,
) -> pd.DataFrame:
    dates = pd.to_datetime(
        [
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
            "2026-01-09",
            "2026-01-12",
        ],
        utc=True,
    )
    result = pd.DataFrame(
        {
            "time": dates,
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [1_000_000] * len(dates),
            "suspended": [False] * len(dates),
            "asset_type": [asset_type] * len(dates),
            "security_name": [f"fixed-{symbol}"] * len(dates),
            "metadata_as_of": dates,
            "metadata_source": ["fixed-authority"] * len(dates),
            "exact_limit_status": ["normal"] * len(dates),
            "exact_limit_locked": [False] * len(dates),
            "exact_limit_source": ["fake-daily-limit"] * len(dates),
        }
    )
    if is_st is not None:
        result["is_st"] = is_st
    return result


def config(**patch) -> BacktestSettings:
    values = {
        "initial_cash": 100_000,
        "commission_rate": 0.0003,
        "minimum_commission": 5,
        "stamp_tax_rate": 0.001,
        "etf_stamp_tax_exempt": True,
        "slippage_bps": 0,
        "lot_size": 100,
        "min_samples": 3,
        "min_trades": 1,
        "min_hit_rate": 0,
        "min_sharpe": -100,
        "max_drawdown": 1,
        "max_weight_deviation": 0.02,
        "akquant_return_tolerance": 0.002,
        "akquant_drawdown_tolerance": 0.002,
        "akquant_sharpe_tolerance": 0.5,
        "akquant_turnover_tolerance": 0.02,
        "require_akquant": True,
    }
    values.update(patch)
    return BacktestSettings.model_validate(values)


def matching_akquant(**kwargs):
    expected = kwargs["expected"]
    return {
        "version": AKQUANT_VERSION,
        "metrics": {
            "total_return": expected["total_return"],
            "max_drawdown": expected["max_drawdown"],
            "sharpe": expected["sharpe"],
            "turnover": expected["turnover"],
        },
        "executions": [
            dict(item, timestamp=item["time"])
            for item in expected["executions"]
        ],
        "trade_count": expected["trade_count"],
        "mode": "single",
    }


def test_verdict_hashes_are_mandatory_and_expiry_requires_utc():
    with pytest.raises(ValidationError):
        BacktestVerdict(
            user_id="u",
            run_id="r",
            passed=False,
            score=0,
            summary="x",
        )
    with pytest.raises(ValidationError):
        RiskVerdict(
            user_id="u",
            run_id="r",
            status=VerdictStatus.REJECTED,
            max_position=0,
            approved_weight=0,
            confidence=1,
        )
    with pytest.raises(ValidationError):
        proposal(expires_at=datetime(2026, 1, 13, 15))


def test_semantic_hash_covers_all_execution_semantics_and_created_at():
    original = proposal()
    digest = proposal_semantics_hash((original,))
    for patch in (
        {"created_at": original.created_at + timedelta(seconds=1)},
        {"order_type": "market", "limit_price": None},
        {"time_in_force": "gtc"},
        {"symbol": "159915"},
        {"direction": TradeDirection.SELL},
        {"target_weight": 0.2},
        {"limit_price": 19},
        {"stop_price": 7},
        {"expires_at": original.expires_at + timedelta(days=1)},
    ):
        assert proposal_semantics_hash((original.model_copy(update=patch),)) != digest


def test_future_rows_and_future_risk_timestamps_are_rejected():
    future = frame()
    future.loc[len(future)] = future.iloc[-1]
    future.loc[len(future) - 1, "time"] = pd.Timestamp(
        "2026-01-13", tz="UTC"
    )
    with pytest.raises(DataValidationError, match="after as_of"):
        run_portfolio_backtest(
            proposals=(proposal(),),
            histories={"510300": future},
            benchmark=None,
            as_of=AS_OF,
            config=config(),
            akquant_check=matching_akquant,
        )
    with pytest.raises(ValidationError, match="future"):
        RiskInputs(
            as_of=AS_OF,
            current_price=10,
            average_turnover=1,
            annualized_volatility=0.2,
            current_total_exposure=0,
            current_symbol_weight=0,
            sector="ETF",
            sector_exposure=0,
            data_as_of=AS_OF + timedelta(seconds=1),
            evidence_quality=1,
            sellable_quantity=100,
            requested_quantity=100,
        )


def test_signal_executes_next_bar_and_exit_executes_next_bar_open():
    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": frame()},
        benchmark=frame(),
        as_of=AS_OF,
        config=config(),
        akquant_check=matching_akquant,
    )
    executions = verdict.metrics["executions"]
    assert executions[0]["time"].startswith("2026-01-06")
    assert executions[0]["price"] == 10.2
    assert executions[-1]["time"].startswith("2026-01-12")
    assert executions[-1]["price"] == 11.0
    assert executions[-1]["signal_time"].startswith("2026-01-09")
    assert executions[-1]["trade_date"] > executions[0]["trade_date"]


def test_no_future_bar_means_no_fake_liquidation_and_rejection():
    short = frame().iloc[:2].copy()
    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": short},
        benchmark=None,
        as_of=datetime(2026, 1, 6, 15, tzinfo=UTC),
        config=config(min_samples=2),
        akquant_check=matching_akquant,
    )
    assert verdict.passed is False
    assert verdict.metrics["trade_count"] == 0
    assert "open_position_at_as_of" in verdict.metrics["reason_codes"]
    assert len(verdict.metrics["executions"]) == 1


@pytest.mark.parametrize(
    ("symbol", "asset_type", "is_st", "expected"),
    [
        ("510300", "etf", None, 0.10),
        ("600000", "stock", True, 0.05),
        ("600000", "stock", False, 0.10),
        ("300001", "stock", False, 0.20),
        ("688001", "stock", False, 0.20),
        ("830001", "stock", False, 0.30),
    ],
)
def test_price_limit_inference_is_audited(
    symbol, asset_type, is_st, expected
):
    history = frame(symbol, asset_type=asset_type, is_st=is_st)
    verdict = run_portfolio_backtest(
        proposals=(proposal(symbol),),
        histories={symbol: history},
        benchmark=None,
        as_of=AS_OF,
        config=config(),
        akquant_check=matching_akquant,
    )
    assert verdict.metrics["audit"]["limit_pct"][symbol] is None


def test_missing_historical_status_uses_observed_ohlcv_without_board_guess():
    no_suspension = frame().drop(columns=["suspended"])
    unknown = frame("900001", asset_type="stock", is_st=False)
    for symbol, history in (
        ("510300", no_suspension),
        ("900001", unknown),
    ):
        verdict = run_portfolio_backtest(
            proposals=(proposal(symbol),),
            histories={symbol: history},
            benchmark=None,
            as_of=AS_OF,
            config=config(),
            akquant_check=matching_akquant,
        )
        assert verdict.metrics["audit"]["limit_pct"][symbol] is None


def test_shared_cash_target_weights_and_deviation_are_enforced():
    with pytest.raises(ValueError, match="weight sum"):
        run_portfolio_backtest(
            proposals=(proposal(weight=0.7), proposal("159915", weight=0.6)),
            histories={"510300": frame(), "159915": frame("159915")},
            benchmark=None,
            as_of=AS_OF,
            config=config(),
            akquant_check=matching_akquant,
        )
    expensive = frame(closes=(1000, 1000, 1000, 1000, 1000, 1000))
    verdict = run_portfolio_backtest(
        proposals=(proposal(weight=0.01, limit_price=2000),),
        histories={"510300": expensive},
        benchmark=None,
        as_of=AS_OF,
        config=config(max_weight_deviation=0.001),
        akquant_check=matching_akquant,
    )
    assert verdict.passed is False
    assert "target_weight_deviation" in verdict.metrics["reason_codes"]


def test_akquant_wrong_version_missing_details_or_mismatch_rejects():
    bad_results = (
        matching_akquant,
        lambda **kwargs: matching_akquant(**kwargs) | {"version": "0.3.6"},
        lambda **kwargs: matching_akquant(**kwargs) | {"executions": []},
        lambda **kwargs: matching_akquant(**kwargs)
        | {
            "metrics": matching_akquant(**kwargs)["metrics"]
            | {"total_return": 0.5}
        },
    )
    assert run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": frame()},
        benchmark=frame(),
        as_of=AS_OF,
        config=config(),
        akquant_check=bad_results[0],
    ).passed
    for checker in bad_results[1:]:
        verdict = run_portfolio_backtest(
            proposals=(proposal(),),
            histories={"510300": frame()},
            benchmark=frame(),
            as_of=AS_OF,
            config=config(),
            akquant_check=checker,
        )
        assert verdict.passed is False
        assert "akquant_validation_failed" in verdict.metrics["reason_codes"]


def test_real_akquant_fixed_data_has_metrics_and_filled_executions():
    proposals = (proposal(weight=0.1),)
    payload = _akquant_payload(
        {"510300": frame()},
        proposals,
        AS_OF,
        config(),
    )
    result = _akquant_validate_worker(payload)
    assert result["version"] == AKQUANT_VERSION
    assert result["trade_count"] > 0
    assert result["executions"]
    assert all(
        result["metrics"][key] is not None
        for key in ("total_return", "max_drawdown", "sharpe", "turnover")
    )


def test_real_akquant_fixed_data_reconciles_with_internal_portfolio():
    history = frame()

    async def history_provider(symbol, as_of):
        return history

    async def benchmark_provider(as_of):
        return None

    provider = create_backtest_provider(
        history_provider=history_provider,
        benchmark_provider=benchmark_provider,
        config=config(
            akquant_return_tolerance=0.01,
            akquant_drawdown_tolerance=0.01,
            akquant_sharpe_tolerance=2,
            akquant_turnover_tolerance=0.02,
        ),
    )

    class Snapshot:
        as_of = AS_OF

    class Context:
        snapshot = Snapshot()
        user_id = "u"
        run_id = "r"

    verdict = asyncio.run(provider((proposal(weight=0.1),), Context()))
    assert verdict.metrics["akquant"]["status"] == "ok", verdict.metrics[
        "akquant"
    ]
    assert verdict.metrics["akquant"]["version"] == AKQUANT_VERSION


def test_production_factory_is_lazy_and_strict(monkeypatch):
    calls = []

    class Runner:
        async def __call__(self, request):
            raise AssertionError("not called at construction")

    executor = RoleAgentExecutor(Runner())
    valid = {
        "backtest": config().model_dump(),
        "risk_limits": {
            "max_single_position": 0.25,
            "max_total_exposure": 0.8,
            "max_sector_concentration": 0.4,
            "min_average_turnover": 1,
            "max_annualized_volatility": 0.5,
            "max_portfolio_drawdown": 0.2,
            "min_samples": 3,
            "min_trades": 1,
            "min_evidence_quality": 0.8,
            "max_data_age_seconds": 86400,
            "max_market_status_age_seconds": 30,
            "max_price_deviation": 0.03,
            "t_plus_one": True,
        },
    }
    monkeypatch.setattr(
        "app.advisor.features.fetch_daily_df",
        lambda symbol: calls.append(symbol),
    )
    dependencies = create_production_dependencies(executor, valid)
    assert dependencies.portfolio_backtest is not None
    assert dependencies.portfolio_risk is not None
    assert calls == []
    for invalid in ({}, valid | {"backtest": {}}, valid | {"risk_limits": {}}):
        with pytest.raises((ValueError, ValidationError)):
            create_production_dependencies(executor, invalid)


def test_chat_model_runner_streams_decoded_chat_message(monkeypatch):
    sink_events = []
    fake = NativeAstreamModel(
        chunks=(
            AIMessageChunk(content='{"chat_message":"先看'),
            AIMessageChunk(
                content=[
                    {
                        "type": "text",
                        "text": (
                            '盈利","thesis":"完整结论","confidence":0.7,'
                            '"evidence_ids":[],"symbols":[]}'
                        ),
                    }
                ],
                usage_metadata={
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "total_tokens": 10,
                },
            ),
        )
    )

    async def sink(event):
        sink_events.append(event)

    monkeypatch.setattr(agents, "build_chat_model", lambda *args, **kwargs: fake)
    runner = agents.ChatModelRoleRunner({}, stream_sink=sink)
    response = asyncio.run(runner(_role_request(generation=1)))
    assert json.loads(response.content)["thesis"] == "完整结论"
    assert [event.event_type for event in sink_events] == [
        "message_started",
        "message_delta",
        "message_delta",
    ]
    assert "".join(
        event.payload.get("delta", "") for event in sink_events
    ) == "先看盈利"
    assert [event.payload["offset"] for event in sink_events] == [0, 0, 2]
    assert response.input_tokens == 7
    assert response.output_tokens == 3
    assert fake.stream_call_count == 1


def test_default_astream_falls_back_once_without_temporary_events(monkeypatch):
    sink_events = []
    fake = DefaultAstreamModel(
        response_text=(
            '{"chat_message":"降级完成","thesis":"结论","confidence":0.7,'
            '"evidence_ids":[],"symbols":[]}'
        )
    )

    async def sink(event):
        sink_events.append(event)

    monkeypatch.setattr(agents, "build_chat_model", lambda *args, **kwargs: fake)
    response = asyncio.run(
        agents.ChatModelRoleRunner({}, stream_sink=sink)(_role_request())
    )
    assert json.loads(response.content)["chat_message"] == "降级完成"
    assert fake.call_count == 1
    assert sink_events == []


def test_chat_model_runner_aggregates_usage_across_chunks(monkeypatch):
    fake = NativeAstreamModel(
        chunks=(
            AIMessageChunk(
                content='{"chat_message":"聚合',
                usage_metadata={
                    "input_tokens": 7,
                    "output_tokens": 0,
                    "total_tokens": 7,
                },
            ),
            AIMessageChunk(
                content=(
                    '成功","thesis":"结论","confidence":0.7,'
                    '"evidence_ids":[],"symbols":[]}'
                ),
                usage_metadata={
                    "input_tokens": 0,
                    "output_tokens": 3,
                    "total_tokens": 3,
                },
            ),
        )
    )
    monkeypatch.setattr(agents, "build_chat_model", lambda *args, **kwargs: fake)
    response = asyncio.run(agents.ChatModelRoleRunner({})(_role_request()))
    assert response.input_tokens == 7
    assert response.output_tokens == 3


def test_chat_model_runner_ignores_non_text_content_blocks(monkeypatch):
    sink_events = []
    fake = NativeAstreamModel(
        chunks=(
            AIMessageChunk(
                content=[
                    {"type": "reasoning", "text": "隐藏推理"},
                    {"type": "text", "text": '{"chat_message":"可见'},
                ]
            ),
            AIMessageChunk(
                content=[
                    {"type": "tool_call", "text": "隐藏工具"},
                    {
                        "type": "text",
                        "text": (
                            '回答","thesis":"结论","confidence":0.7,'
                            '"evidence_ids":[],"symbols":[]}'
                        ),
                    },
                ]
            ),
        )
    )

    async def sink(event):
        sink_events.append(event)

    monkeypatch.setattr(agents, "build_chat_model", lambda *args, **kwargs: fake)
    response = asyncio.run(
        agents.ChatModelRoleRunner({}, stream_sink=sink)(_role_request())
    )
    assert json.loads(response.content)["chat_message"] == "可见回答"
    deltas = "".join(
        event.payload.get("delta", "") for event in sink_events
    )
    assert deltas == "可见回答"
    assert "隐藏推理" not in response.content
    assert "隐藏工具" not in response.content


def test_chat_model_runner_rejects_synchronous_sink():
    with pytest.raises(TypeError, match="async"):
        agents.ChatModelRoleRunner({}, stream_sink=lambda event: None)


def _role_request(*, generation=1):
    return agents.RoleRequest(
        user_id="u",
        run_id="r",
        role="technical",
        prompt="prompt",
        output_schema=agents.AnalystOutput,
        model_tier="quick",
        idempotency_key="r:technical:attempt:1",
        timeout_seconds=1,
        deadline_at=9999999999,
        message_id="message",
        generation=generation,
        round_index=None,
        attempt=1,
    )


def test_execution_default_uses_production_factory(monkeypatch):
    from app.advisor.committee import execution
    from app.advisor.committee.graph import CommitteeDependencies

    captured = []

    def factory(executor, committee_config):
        captured.append(committee_config)
        return CommitteeDependencies(role_executor=executor)

    monkeypatch.setattr(
        execution,
        "create_production_dependencies",
        factory,
    )
    execution.create_committee_invoker(
        committee_config={
            "budget": {
                "max_calls": 20,
                "max_tokens": 1000,
                "node_timeout_seconds": 1,
                "total_timeout_seconds": 2,
            }
        }
    )
    assert len(captured) == 1


def test_state_exposes_portfolio_and_single_proposal_compatibility_views():
    assert "trade_proposals" in CommitteeState.__annotations__
    assert "trade_proposal" in CommitteeState.__annotations__


def test_trader_output_accepts_portfolio_or_legacy_single_view():
    portfolio = TraderOutput.model_validate(
        {
            "chat_message": "组合交易方案",
            "trade_proposals": [
                {
                    "symbol": "510300",
                    "direction": "buy",
                    "target_weight": 0.4,
                    "confidence": 0.8,
                    "rationale": "a",
                    "evidence_ids": [],
                    "order_type": "limit",
                    "limit_price": 10,
                },
                {
                    "symbol": "159915",
                    "direction": "buy",
                    "target_weight": 0.3,
                    "confidence": 0.7,
                    "rationale": "b",
                    "evidence_ids": [],
                    "order_type": "market",
                },
            ]
        }
    )
    assert len(portfolio.trade_proposals) == 2
    legacy = TraderOutput.model_validate(
        {
            "chat_message": "单笔交易方案",
            "symbol": "510300",
            "direction": "hold",
            "target_weight": 0,
            "confidence": 1,
            "rationale": "legacy",
            "evidence_ids": [],
        }
    )
    assert legacy.symbol == "510300"


def test_hand_calculated_equity_drawdown_sharpe_turnover_and_excess():
    history = frame().iloc[:4].copy()
    history["open"] = [10, 10, 9, 11]
    history["close"] = [10, 10, 9, 11]
    history["high"] = history["close"] * 1.01
    history["low"] = history["close"] * 0.99
    benchmark = history.copy()
    benchmark["close"] = [99, 100, 101, 102]
    verdict = run_portfolio_backtest(
        proposals=(proposal(weight=0.1),),
        histories={"510300": history},
        benchmark=benchmark,
        as_of=history.iloc[-1]["time"].to_pydatetime(),
        config=config(
            commission_rate=0,
            minimum_commission=0,
            stamp_tax_rate=0,
            min_samples=3,
        ),
        akquant_check=matching_akquant,
    )
    metrics = verdict.metrics
    daily_returns = [-0.01, 101_000 / 99_000 - 1]
    expected_sharpe = (
        sum(daily_returns)
        / 2
        / pd.Series(daily_returns).std(ddof=1)
        * math.sqrt(252)
    )
    assert metrics["equity_curve"][-3:] == (
        {
            "time": "2026-01-06T00:00:00+00:00",
            "equity": 100_000,
        },
        {
            "time": "2026-01-07T00:00:00+00:00",
            "equity": 99_000,
        },
        {
            "time": "2026-01-08T00:00:00+00:00",
            "equity": 101_000,
        },
    )
    assert metrics["total_return"] == pytest.approx(0.01)
    assert metrics["max_drawdown"] == pytest.approx(0.01)
    assert metrics["sharpe"] == pytest.approx(expected_sharpe)
    assert metrics["turnover"] == pytest.approx(0.21)
    assert metrics["benchmark_return"] == pytest.approx(0.02)
    assert metrics["relative_benchmark_return"] == pytest.approx(-0.01)


def test_hand_calculated_mixed_stock_etf_fees():
    etf = frame().iloc[:4].copy()
    stock = frame(
        "600000",
        asset_type="stock",
        is_st=False,
    ).iloc[:4].copy()
    for history in (etf, stock):
        history["open"] = [10, 10, 10.5, 11]
        history["close"] = [10, 10, 10.5, 11]
        history["high"] = history["close"] * 1.01
        history["low"] = history["close"] * 0.99
    proposals = (
        proposal("510300", weight=0.1),
        proposal("600000", weight=0.1),
    )
    verdict = run_portfolio_backtest(
        proposals=proposals,
        histories={"510300": etf, "600000": stock},
        benchmark=None,
        as_of=etf.iloc[-1]["time"].to_pydatetime(),
        config=config(slippage_bps=0),
        akquant_check=matching_akquant,
    )
    sells = {
        item["symbol"]: item
        for item in verdict.metrics["executions"]
        if item["side"] == "sell"
    }
    assert sells["510300"]["commission"] == 5
    assert sells["510300"]["stamp_tax"] == 0
    assert sells["600000"]["commission"] == 5
    assert sells["600000"]["stamp_tax"] == 11
    assert verdict.metrics["ending_equity"] == pytest.approx(101_969)

    akquant = _akquant_validate_worker(
        _akquant_payload(
            {"510300": etf, "600000": stock},
            proposals,
            etf.iloc[-1]["time"].to_pydatetime(),
            config(slippage_bps=0),
        )
    )
    assert akquant["mode"] == "single_portfolio_per_order_fee"
    assert akquant["trade_count"] == 2
    assert len(akquant["executions"]) == 4
