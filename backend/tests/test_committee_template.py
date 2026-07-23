from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.advisor.committee.backtest import (
    AccountState,
    BacktestSettings,
    ExistingPosition,
    StrategyTemplate,
    _akquant_payload,
    _akquant_validate_worker,
    _blocked_reason,
    create_backtest_provider,
    run_portfolio_backtest,
)
from app.advisor.committee.models import TradeDirection, TradeProposal
from app.advisor.committee.dependencies import _account_from_snapshot


UTC = timezone.utc
AS_OF = datetime(2026, 1, 9, 15, tzinfo=UTC)


def config():
    return BacktestSettings(
        initial_cash=100_000,
        commission_rate=0,
        minimum_commission=0,
        stamp_tax_rate=0.001,
        etf_stamp_tax_exempt=True,
        slippage_bps=0,
        lot_size=100,
        min_samples=2,
        min_trades=1,
        min_hit_rate=0,
        min_sharpe=-100,
        max_drawdown=1,
        max_weight_deviation=0.1,
        akquant_return_tolerance=0.1,
        akquant_drawdown_tolerance=0.1,
        akquant_sharpe_tolerance=10,
        akquant_turnover_tolerance=0.1,
        require_akquant=True,
    )


def bars():
    values = [10, 10.2, 10.4, 10.6, 10.8]
    return pd.DataFrame(
        {
            "time": pd.to_datetime(
                [
                    "2026-01-05",
                    "2026-01-06",
                    "2026-01-07",
                    "2026-01-08",
                    "2026-01-09",
                ],
                utc=True,
            ),
            "open": values,
            "high": [value + 0.2 for value in values],
            "low": [value - 0.2 for value in values],
            "close": values,
            "volume": [100_000] * 5,
            "exact_limit_status": ["normal"] * 5,
            "exact_limit_locked": [False] * 5,
            "exact_limit_source": ["fake-daily-limit"] * 5,
        }
    )


def proposal():
    return TradeProposal(
        user_id="u",
        run_id="r",
        symbol="600000",
        direction=TradeDirection.BUY,
        target_weight=0.2,
        confidence=0.8,
        rationale="live",
        strategy_id="score-v2",
        strategy_version="2.0",
        created_at=datetime(2026, 7, 22, 8, tzinfo=UTC),
    )


def matching(**kwargs):
    expected = kwargs["expected"]
    return {
        "version": "0.3.7",
        "metrics": {
            key: expected[key]
            for key in (
                "total_return",
                "max_drawdown",
                "sharpe",
                "turnover",
            )
        },
        "executions": [
            dict(item, timestamp=item["time"], submit_at=item["submit_at"])
            for item in expected["executions"]
        ],
        "trade_count": expected["trade_count"],
    }


def test_live_proposal_is_validated_by_audited_historical_template():
    history = bars()

    async def history_provider(symbol, as_of):
        return history

    async def benchmark_provider(as_of):
        return history

    async def template_provider(proposals, histories, benchmark, as_of):
        return StrategyTemplate(
            strategy_id="score-v2",
            strategy_version="2.0",
            as_of=as_of,
            signals={"600000": (history.iloc[0]["time"].to_pydatetime(),)},
            source="fixed-score-template",
        )

    async def account_provider(context):
        return AccountState(
            cash=100_000,
            equity=100_000,
            positions={},
            as_of=AS_OF,
            source="snapshot:account",
        )

    async def validator(**kwargs):
        return matching(**kwargs)

    provider = create_backtest_provider(
        history_provider=history_provider,
        benchmark_provider=benchmark_provider,
        strategy_template_provider=template_provider,
        account_provider=account_provider,
        config=config(),
        akquant_validator=validator,
    )

    class Snapshot:
        as_of = AS_OF
        strategy_id = "score-v2"
        strategy_version = "2.0"

    class Context:
        snapshot = Snapshot()

    verdict = asyncio.run(provider((proposal(),), Context()))
    assert verdict.passed is True
    assert verdict.metrics["akquant"]["status"] == "ok"
    assert verdict.metrics["strategy_template"]["source"] == "fixed-score-template"
    assert verdict.metrics["executions"]
    execution = verdict.metrics["executions"][0]
    assert execution["signal_at_close"].startswith("2026-01-05")
    assert execution["submit_at"].startswith("2026-01-05")
    assert execution["time"].startswith("2026-01-06")
    assert execution["time"] <= AS_OF.isoformat()


def test_production_provider_rejects_missing_snapshot_account():
    async def history_provider(symbol, as_of):
        return bars()

    async def benchmark_provider(as_of):
        return bars()

    async def template_provider(proposals, histories, benchmark, as_of):
        return StrategyTemplate(
            strategy_id="score-v2",
            strategy_version="2.0",
            as_of=as_of,
            signals={"600000": ()},
            source="fixed",
        )

    async def missing_account(context):
        raise ValueError("frozen snapshot account evidence missing")

    provider = create_backtest_provider(
        history_provider=history_provider,
        benchmark_provider=benchmark_provider,
        strategy_template_provider=template_provider,
        account_provider=missing_account,
        config=config(),
        akquant_validator=lambda **kwargs: matching(**kwargs),
    )

    class Snapshot:
        as_of = AS_OF

    class Context:
        snapshot = Snapshot()

    with pytest.raises(ValueError, match="snapshot account"):
        asyncio.run(provider((proposal(),), Context()))

    with pytest.raises(ValueError, match="snapshot account"):
        _account_from_snapshot(Context())


def test_account_cash_not_config_cash_controls_execution():
    account = AccountState(
        cash=100,
        equity=100_000,
        positions={
            "000001": ExistingPosition(
                symbol="000001",
                quantity=9990,
                available_quantity=9990,
                acquired_at=AS_OF - timedelta(days=2),
                cost=9,
                last_price=10,
                market_value=99_900,
                price_as_of=AS_OF,
            )
        },
        version="v1",
        as_of=AS_OF,
        source="snapshot:account",
    )
    template = StrategyTemplate(
        strategy_id="score-v2",
        strategy_version="2.0",
        as_of=AS_OF,
        signals={
            "600000": (
                datetime(2026, 1, 5, 15, tzinfo=UTC),
            )
        },
        source="fixed",
    )
    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"600000": bars()},
        benchmark=bars(),
        account=account,
        template=template,
        as_of=AS_OF,
        config=config(),
        akquant_check=matching,
    )
    assert "insufficient_cash" in verdict.metrics["reason_codes"]


def test_stop_limit_unknown_intraday_path_is_not_filled_same_day():
    value = proposal().model_copy(
        update={
            "order_type": "stop_limit",
            "stop_price": 10.1,
            "limit_price": 10.0,
        }
    )
    template = StrategyTemplate(
        strategy_id="score-v2",
        strategy_version="2.0",
        as_of=AS_OF,
        signals={
            "600000": (
                datetime(2026, 1, 5, 15, tzinfo=UTC),
            )
        },
        source="fixed",
    )
    verdict = run_portfolio_backtest(
        proposals=(value,),
        histories={"600000": bars()},
        benchmark=bars(),
        account=AccountState(
            cash=100_000,
            equity=100_000,
            positions={},
            as_of=AS_OF,
            source="snapshot:account",
        ),
        template=template,
        as_of=AS_OF,
        config=config(),
        akquant_check=matching,
    )
    assert verdict.metrics["executions"] == ()
    assert any(
        item["reason"] == "stop_limit_path_unknown"
        for item in verdict.metrics["audit"]["blocked_orders"]
    )


def test_historical_one_price_limit_lock_is_rejected_without_st_guess():
    history = bars()
    history.loc[1, ["open", "high", "low", "close"]] = 11
    template = StrategyTemplate(
        strategy_id="score-v2",
        strategy_version="2.0",
        as_of=AS_OF,
        signals={
            "600000": (
                datetime(2026, 1, 5, 15, tzinfo=UTC),
            )
        },
        source="fixed",
    )
    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"600000": history},
        benchmark=bars(),
        account=AccountState(
            cash=100_000,
            equity=100_000,
            positions={},
            as_of=AS_OF,
            source="snapshot:account",
        ),
        template=template,
        as_of=AS_OF,
        config=config(),
        akquant_check=matching,
    )
    assert any(
        item["reason"] == "locked_limit_up"
        for item in verdict.metrics["audit"]["blocked_orders"]
    )


def test_one_price_limit_lock_is_side_specific():
    up = pd.Series(
        {
            "open": 11,
            "high": 11,
            "low": 11,
            "volume": 100,
            "suspended": pd.NA,
            "exact_limit_status": "limit_up",
            "exact_limit_locked": True,
            "exact_limit_source": "fake-daily-limit",
        }
    )
    down = up.copy()
    down[["open", "high", "low"]] = 9
    down["exact_limit_status"] = "limit_down"
    assert (
        _blocked_reason(up, 10, "buy", None)
        == "authoritative_locked_limit_up"
    )
    assert _blocked_reason(up, 10, "sell", None) is None
    assert (
        _blocked_reason(down, 10, "sell", None)
        == "authoritative_locked_limit_down"
    )
    assert _blocked_reason(down, 10, "buy", None) is None


def test_multiple_historical_sell_events_are_independent_of_live_position():
    sell = proposal().model_copy(
        update={"direction": TradeDirection.SELL}
    )
    template = StrategyTemplate(
        strategy_id="score-v2",
        strategy_version="2.0",
        as_of=AS_OF,
        signals={
            "600000": (
                datetime(2026, 1, 6, 15, tzinfo=UTC),
                datetime(2026, 1, 8, 15, tzinfo=UTC),
            )
        },
        directions={
            "600000": (
                TradeDirection.SELL,
                TradeDirection.SELL,
            )
        },
        source="fixed",
    )
    account = AccountState(
        cash=5_000,
        equity=500_000,
        positions={
            "600000": ExistingPosition(
                symbol="600000",
                quantity=10_000,
                available_quantity=10_000,
                acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
                cost=99,
                last_price=49.5,
                market_value=495_000,
                price_as_of=AS_OF,
            )
        },
        version="v1",
        as_of=AS_OF,
        source="snapshot:account",
    )
    verdict = run_portfolio_backtest(
        proposals=(sell,),
        histories={"600000": bars()},
        benchmark=bars(),
        account=account,
        template=template,
        as_of=AS_OF,
        config=config(),
        akquant_check=matching,
    )
    sells = [
        item
        for item in verdict.metrics["executions"]
        if item["side"] == "sell"
    ]
    assert len(sells) == 2
    assert len(verdict.metrics["synthetic_sell_entries"]) == 2
    assert verdict.metrics["starting_equity"] == config().initial_cash
    assert {item["signal_key"] for item in sells} == {
        "600000:2026-01-06T15:00:00+00:00",
        "600000:2026-01-08T15:00:00+00:00",
    }
    actual = _akquant_validate_worker(
        _akquant_payload(
            {"600000": bars()},
            (sell,),
            AS_OF,
            config(),
            verdict.metrics,
            account=account,
            template=template,
        )
    )
    assert actual["trade_count"] == 2
    assert len(actual["executions"]) == 2


def test_strategy_template_worker_parses_benchmark_time_strings():
    from app.advisor.committee.dependencies import _strategy_template_worker

    times = [
        datetime(2025, 12, 1, 15, tzinfo=UTC) + timedelta(days=offset)
        for offset in range(40)
    ]
    history = [
        {
            "time": stamp.isoformat(),
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0 + (index % 5) * 0.1,
            "volume": 100_000,
            "amount": 1_000_000,
        }
        for index, stamp in enumerate(times)
    ]
    benchmark = [
        {
            "time": stamp.isoformat(),
            "close": 100.0 + index * 0.01,
        }
        for index, stamp in enumerate(times)
    ]
    result = _strategy_template_worker(
        {
            "strategy_id": "advisor-score-v2",
            "strategy_version": "default",
            "as_of": AS_OF.isoformat(),
            "threshold": 0.55,
            "sell_threshold": 0.35,
            "histories": {"510300": history},
            "benchmark": benchmark,
        }
    )
    assert result["strategy_id"] == "advisor-score-v2"
    assert "510300" in result["signals"]
    assert len(result["signals"]["510300"]) == len(result["directions"]["510300"])
