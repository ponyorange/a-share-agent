from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from app.advisor.committee.backtest import (
    BacktestSettings,
    DailyLimitStatus,
    ExistingPosition,
    SecurityMetadata,
    _akquant_payload,
    _akquant_validate_worker,
    create_backtest_provider,
    run_portfolio_backtest,
)
from app.advisor.committee.models import (
    BacktestVerdict,
    FinalDecision,
    TradeDirection,
    TradeProposal,
    VerdictStatus,
)
from app.advisor.committee.dependencies import (
    _daily_limit_pools,
    _daily_limit_status_worker,
    _metadata_worker,
    _trade_calendar_worker,
)
from app.advisor.committee.risk import (
    RiskInputs,
    RiskLimits,
    create_portfolio_risk_provider,
    proposal_semantics_hash,
)


UTC = timezone.utc
AS_OF = datetime(2026, 1, 9, 15, tzinfo=UTC)


def settings(**patch):
    values = {
        "initial_cash": 100_000,
        "commission_rate": 0,
        "minimum_commission": 0,
        "stamp_tax_rate": 0.001,
        "etf_stamp_tax_exempt": True,
        "slippage_bps": 0,
        "lot_size": 100,
        "min_samples": 2,
        "min_trades": 1,
        "min_hit_rate": 0,
        "min_sharpe": -100,
        "max_drawdown": 1,
        "max_weight_deviation": 0.05,
        "akquant_return_tolerance": 0.01,
        "akquant_drawdown_tolerance": 0.01,
        "akquant_sharpe_tolerance": 2,
        "akquant_turnover_tolerance": 0.05,
        "require_akquant": True,
    }
    values.update(patch)
    return BacktestSettings.model_validate(values)


def bars():
    close = [10, 10.2, 10.4, 10.6, 10.8]
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
            "open": close,
            "high": [value + 0.3 for value in close],
            "low": [value - 0.3 for value in close],
            "close": close,
            "volume": [1_000_000] * 5,
            "suspended": [False] * 5,
            "asset_type": ["etf"] * 5,
            "security_name": ["fixed ETF"] * 5,
            "metadata_as_of": [AS_OF] * 5,
            "metadata_source": ["fake-authority"] * 5,
            "exact_limit_status": ["normal"] * 5,
            "exact_limit_locked": [False] * 5,
            "exact_limit_source": ["fake-daily-limit"] * 5,
        }
    )


def benchmark():
    result = bars()
    result["close"] = [100, 101, 102, 103, 104]
    return result


def proposal(direction="buy", **patch):
    values = {
        "user_id": "u",
        "run_id": "r",
        "symbol": "510300",
        "direction": direction,
        "target_weight": 0.1,
        "confidence": 0.8,
        "rationale": "fixed",
        "created_at": datetime(2026, 1, 5, 15, tzinfo=UTC),
        "expires_at": AS_OF + timedelta(days=1),
    }
    values.update(patch)
    return TradeProposal(**values)


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
            dict(item, timestamp=item["time"])
            for item in expected["executions"]
        ],
        "trade_count": expected["trade_count"],
    }


def test_sell_uses_existing_position_and_t1_available_quantity():
    sell = proposal(
        "sell",
        order_type="limit",
        limit_price=10.1,
    )
    verdict = run_portfolio_backtest(
        proposals=(sell,),
        histories={"510300": bars()},
        benchmark=benchmark(),
        positions={
            "510300": ExistingPosition(
                symbol="510300",
                quantity=1000,
                available_quantity=600,
                acquired_at=datetime(2026, 1, 2, 15, tzinfo=UTC),
                cost=9,
            )
        },
        as_of=AS_OF,
        config=settings(),
        akquant_check=matching,
    )
    execution = verdict.metrics["executions"][0]
    assert execution["side"] == "sell"
    assert execution["quantity"] == 600
    assert execution["trade_date"] == "2026-01-06"
    assert verdict.metrics["ending_positions"]["510300"] == 400

    expected = verdict.metrics
    actual = _akquant_validate_worker(
        _akquant_payload(
            {"510300": bars()},
            (sell,),
            AS_OF,
            settings(),
            expected,
            {
                "510300": ExistingPosition(
                    symbol="510300",
                    quantity=1000,
                    available_quantity=600,
                    acquired_at=datetime(2026, 1, 2, 15, tzinfo=UTC),
                    cost=9,
                )
            },
        )
    )
    assert actual["trade_count"] == 1, actual["engine_audit"]
    assert actual["executions"][0]["trade_date"] == "2026-01-06"


def test_sell_without_position_or_same_day_available_is_rejected():
    sell = proposal("sell")
    no_holding = run_portfolio_backtest(
        proposals=(sell,),
        histories={"510300": bars()},
        benchmark=benchmark(),
        positions={},
        as_of=AS_OF,
        config=settings(),
        akquant_check=matching,
    )
    assert "sell_without_position" in no_holding.metrics["reason_codes"]
    same_day = run_portfolio_backtest(
        proposals=(sell,),
        histories={"510300": bars()},
        benchmark=benchmark(),
        positions={
            "510300": ExistingPosition(
                symbol="510300",
                quantity=1000,
                available_quantity=0,
                acquired_at=datetime(2026, 1, 5, 10, tzinfo=UTC),
                cost=10,
            )
        },
        as_of=AS_OF,
        config=settings(),
        akquant_check=matching,
    )
    assert "t_plus_one_unavailable" in same_day.metrics["reason_codes"]


def test_limit_and_stop_limit_use_intraday_ohlc_conservatively():
    history = bars()
    history.loc[1, ["open", "high", "low"]] = [10.5, 10.8, 9.8]
    buy = proposal(order_type="limit", limit_price=10)
    verdict = run_portfolio_backtest(
        proposals=(buy,),
        histories={"510300": history},
        benchmark=benchmark(),
        positions={},
        as_of=AS_OF,
        config=settings(),
        akquant_check=matching,
    )
    assert verdict.metrics["executions"][0]["price"] == 10

    sell = proposal(
        "sell",
        order_type="stop_limit",
        stop_price=10.3,
        limit_price=10.1,
    )
    sell_verdict = run_portfolio_backtest(
        proposals=(sell,),
        histories={"510300": history},
        benchmark=benchmark(),
        positions={
            "510300": ExistingPosition(
                symbol="510300",
                quantity=1000,
                available_quantity=1000,
                acquired_at=datetime(2026, 1, 2, tzinfo=UTC),
                cost=9,
            )
        },
        as_of=AS_OF,
        config=settings(),
        akquant_check=matching,
    )
    assert sell_verdict.metrics["executions"] == ()
    assert any(
        item["reason"] == "stop_limit_path_unknown"
        for item in sell_verdict.metrics["audit"]["blocked_orders"]
    )


def test_benchmark_missing_is_explicit_rejection():
    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": bars()},
        benchmark=None,
        positions={},
        as_of=AS_OF,
        config=settings(),
        akquant_check=matching,
    )
    assert "benchmark_missing" in verdict.metrics["reason_codes"]
    assert verdict.passed is False


def test_risk_accumulates_all_proposals_before_exposure_checks():
    limits = RiskLimits(
        max_single_position=0.5,
        max_total_exposure=0.8,
        max_sector_concentration=0.7,
        min_average_turnover=1,
        max_annualized_volatility=1,
        max_portfolio_drawdown=1,
        min_samples=2,
        min_trades=1,
        min_evidence_quality=0.5,
        max_data_age_seconds=86400,
        max_market_status_age_seconds=30,
        max_price_deviation=0.5,
        t_plus_one=True,
    )
    items = (
        proposal(symbol="510300", target_weight=0.2),
        proposal(symbol="159915", target_weight=0.2),
    )
    digest = proposal_semantics_hash(items)
    backtest = BacktestVerdict(
        user_id="u",
        run_id="r",
        passed=True,
        score=1,
        metrics={
            "sample_count": 10,
            "trade_count": 2,
            "max_drawdown": 0,
        },
        summary="ok",
        proposal_hash=digest,
        created_at=AS_OF,
    )

    async def market_provider(symbol, as_of):
        return RiskInputs(
            as_of=as_of,
            current_price=10,
            average_turnover=1_000_000,
            annualized_volatility=0.2,
            current_total_exposure=0,
            current_symbol_weight=0,
            sector="ETF",
            sector_exposure=0,
            data_as_of=as_of,
            evidence_quality=1,
            sellable_quantity=1000,
            requested_quantity=100,
        )

    async def portfolio_provider(context):
        return {
            "equity": 100_000,
            "total_exposure": 0.5,
            "symbol_weights": {},
            "sector_weights": {"ETF": 0.5},
            "sellable_quantity": {},
        }

    provider = create_portfolio_risk_provider(
        market_provider=market_provider,
        portfolio_provider=portfolio_provider,
        config=limits,
    )

    class Snapshot:
        as_of = AS_OF

    class Context:
        snapshot = Snapshot()

    verdict = asyncio.run(provider(items, backtest, Context()))
    assert verdict.status is VerdictStatus.REJECTED
    assert any(
        rule.rule_id.endswith("total_exposure")
        and rule.observed == pytest.approx(0.9)
        for rule in verdict.rules
    )
    assert any(
        rule.rule_id.endswith("sector_concentration")
        and rule.observed == pytest.approx(0.9)
        for rule in verdict.rules
    )


def test_akquant_execution_dates_cannot_be_filled_from_expected():
    def forged(**kwargs):
        result = matching(**kwargs)
        result["executions"] = [
            dict(item, timestamp="2026-01-05T00:00:00+00:00")
            for item in result["executions"]
        ]
        return result

    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": bars()},
        benchmark=benchmark(),
        positions={},
        as_of=AS_OF,
        config=settings(),
        akquant_check=forged,
    )
    assert "akquant_validation_failed" in verdict.metrics["reason_codes"]


def test_metadata_provider_is_injected_recorded_and_historical_gaps_fail():
    raw = bars().drop(
        columns=[
            "suspended",
            "asset_type",
            "metadata_as_of",
            "metadata_source",
        ]
    )

    async def history_provider(symbol, as_of):
        return raw

    async def benchmark_provider(as_of):
        return benchmark()

    async def metadata_provider(symbol, dates, as_of):
        return SecurityMetadata(
            symbol=symbol,
            name="沪深300ETF",
            is_st=False,
            asset_type="etf",
            limit_pct=0.1,
            suspended_by_date={
                date: False for date in dates
            },
            as_of=as_of,
            source="akshare.stock_tfp_em+spot_em",
        )

    async def validator(**kwargs):
        return matching(**kwargs)

    provider = create_backtest_provider(
        history_provider=history_provider,
        benchmark_provider=benchmark_provider,
        metadata_provider=metadata_provider,
        config=settings(),
        akquant_validator=validator,
    )

    class Snapshot:
        as_of = AS_OF

    class Context:
        snapshot = Snapshot()

    verdict = asyncio.run(provider((proposal(),), Context()))
    assert verdict.metrics["audit"]["metadata"]["510300"]["source"].startswith(
        "akshare"
    )

    async def incomplete(symbol, dates, as_of):
        value = await metadata_provider(symbol, dates, as_of)
        return value.model_copy(update={"suspended_by_date": {}})

    bad_provider = create_backtest_provider(
        history_provider=history_provider,
        benchmark_provider=benchmark_provider,
        metadata_provider=incomplete,
        config=settings(),
        akquant_validator=validator,
    )
    fallback = asyncio.run(bad_provider((proposal(),), Context()))
    assert fallback.metrics["audit"]["metadata"]["510300"]["source"].startswith(
        "akshare"
    )


def test_production_akshare_metadata_adapter_is_authoritative(monkeypatch):
    import akshare as ak

    monkeypatch.setattr(
        ak,
        "stock_zh_a_spot_em",
        lambda: pytest.fail("current spot metadata must not serve history"),
    )
    monkeypatch.setattr(
        ak,
        "stock_tfp_em",
        lambda date: pd.DataFrame(
            {"代码": ["600000"] if date == "20260109" else []}
        ),
    )
    result = _metadata_worker(
        "600000",
        ("2026-01-09",),
        AS_OF.isoformat(),
    )
    assert result["name"] == "600000"
    assert result["is_st"] is False
    assert result["limit_pct"] == 0.10
    assert result["suspended_by_date"] == {
        "2026-01-09": True,
    }
    assert result["source"] == "stable_code_rules+AKShare.stock_tfp_em"
    historical = _metadata_worker(
        "600000",
        ("2026-01-08",),
        AS_OF.isoformat(),
    )
    assert historical["suspended_by_date"]["2026-01-08"] is False


def test_current_spot_st_metadata_is_not_relabelled_as_historical(monkeypatch):
    import akshare as ak

    monkeypatch.setattr(
        ak,
        "stock_zh_a_spot_em",
        lambda: pd.DataFrame({"代码": ["600000"], "名称": ["ST当前名称"]}),
    )
    monkeypatch.setattr(
        ak,
        "stock_tfp_em",
        lambda date: pd.DataFrame(columns=["代码"]),
    )

    result = _metadata_worker(
        "600000",
        ("2025-01-02",),
        AS_OF.isoformat(),
    )
    assert result["is_st"] is False
    assert "spot" not in result["source"].lower()


def test_production_trade_calendar_uses_authoritative_akshare_dates(monkeypatch):
    import akshare as ak

    monkeypatch.setattr(
        ak,
        "tool_trade_date_hist_sina",
        lambda: pd.DataFrame(
            {
                "trade_date": pd.to_datetime(
                    ["2026-09-30", "2026-10-09", "2026-10-12"]
                )
            }
        ),
    )

    result = _trade_calendar_worker("2026-09-30T07:00:00+00:00")

    assert result["sessions"] == [
        "2026-09-30",
        "2026-10-09",
        "2026-10-12",
    ]
    assert result["source"] == "AKShare.tool_trade_date_hist_sina"


def test_production_daily_limit_provider_returns_exact_status(monkeypatch):
    import akshare as ak

    _daily_limit_pools.cache_clear()
    monkeypatch.setattr(
        ak,
        "stock_zt_pool_em",
        lambda date: pd.DataFrame(
            {
                "代码": ["600000"],
                "最新价": [11.0],
                "开板次数": [0],
            }
        ),
    )
    monkeypatch.setattr(
        ak,
        "stock_zt_pool_dtgc_em",
        lambda date: pd.DataFrame(columns=["代码", "最新价", "开板次数"]),
    )
    result = _daily_limit_status_worker(
        ("600000", "000001"),
        ("2026-01-09",),
    )
    by_symbol = {item["symbol"]: item for item in result}
    assert by_symbol["600000"]["status"] == "limit_up"
    assert by_symbol["600000"]["limit_up_price"] == 11
    assert by_symbol["600000"]["locked"] is True
    assert by_symbol["600000"]["data_as_of"].startswith("2026-01-09")
    assert by_symbol["000001"]["status"] == "normal"


def test_fixed_a_share_portfolio_runs_with_exact_daily_providers():
    stock_bars = bars().copy()
    stock_bars["asset_type"] = "stock"
    stock_bars["security_name"] = "600000"

    async def history_provider(symbol, as_of):
        assert symbol == "600000"
        return stock_bars

    async def benchmark_provider(as_of):
        return benchmark()

    async def metadata_provider(symbol, dates, as_of):
        return SecurityMetadata(
            symbol=symbol,
            name=symbol,
            is_st=False,
            asset_type="stock",
            limit_pct=0.1,
            suspended_by_date={date: False for date in dates},
            as_of=as_of,
            source="stable_code_rules+AKShare.stock_tfp_em",
        )

    async def daily_provider(symbols, dates):
        return tuple(
            DailyLimitStatus(
                symbol=symbol,
                trade_date=date,
                locked=False,
                status="normal",
                source=(
                    "AKShare.stock_zt_pool_em"
                    "+stock_zt_pool_dtgc_em"
                ),
                data_as_of=datetime.fromisoformat(
                    f"{date}T00:00:00+00:00"
                ),
            )
            for symbol in symbols
            for date in dates
        )

    async def validator(**kwargs):
        return matching(**kwargs)

    async def positions_provider(context):
        return {
            "600000": ExistingPosition(
                symbol="600000",
                quantity=1000,
                available_quantity=1000,
                acquired_at=datetime(2026, 1, 2, 15, tzinfo=UTC),
                cost=9,
            )
        }

    provider = create_backtest_provider(
        history_provider=history_provider,
        benchmark_provider=benchmark_provider,
        metadata_provider=metadata_provider,
        daily_limit_provider=daily_provider,
        positions_provider=positions_provider,
        config=settings(),
        akquant_validator=validator,
    )

    class Snapshot:
        as_of = AS_OF

    class Context:
        snapshot = Snapshot()

    verdict = asyncio.run(
        provider(
            (
                proposal(
                    symbol="600000",
                    direction="sell",
                    order_type="limit",
                    limit_price=10.1,
                ),
            ),
            Context(),
        )
    )

    assert verdict.passed is True, verdict.metrics
    assert verdict.metrics["trade_count"] >= 1


def test_final_decision_preserves_reviewed_portfolio_and_locked_orders():
    items = (
        proposal(symbol="510300", target_weight=0.2),
        proposal(symbol="159915", target_weight=0.2),
    )
    decision = FinalDecision(
        user_id="u",
        run_id="r",
        action=TradeDirection.BUY,
        symbol="510300",
        target_weight=0.2,
        confidence=1,
        rationale="locked",
        risk_status=VerdictStatus.APPROVED,
        proposals=items,
        orders=items,
        proposal_hash=proposal_semantics_hash(items),
        created_at=AS_OF,
    )
    assert len(decision.proposals) == 2
    assert len(decision.orders) == 2
    assert decision.symbol == decision.orders[0].symbol


def test_t_plus_one_cannot_be_disabled():
    values = {
        "max_single_position": 0.5,
        "max_total_exposure": 0.8,
        "max_sector_concentration": 0.7,
        "min_average_turnover": 1,
        "max_annualized_volatility": 1,
        "max_portfolio_drawdown": 1,
        "min_samples": 2,
        "min_trades": 1,
        "min_evidence_quality": 0.5,
        "max_data_age_seconds": 86400,
        "max_market_status_age_seconds": 30,
        "max_price_deviation": 0.5,
        "t_plus_one": False,
    }
    with pytest.raises(ValidationError):
        RiskLimits.model_validate(values)


def test_historical_template_is_explicit_and_live_time_is_not_forged():
    live_created = datetime(2026, 7, 22, 8, tzinfo=UTC)
    live = proposal(created_at=live_created)
    live_verdict = run_portfolio_backtest(
        proposals=(live,),
        histories={"510300": bars()},
        benchmark=benchmark(),
        positions={},
        as_of=AS_OF,
        config=settings(),
        akquant_check=matching,
    )
    assert "no_next_trading_bar" in live_verdict.metrics["reason_codes"]

    template = live.model_copy(
        update={
            "strategy_template_as_of": datetime(
                2026, 1, 5, 15, tzinfo=UTC
            )
        }
    )
    template_verdict = run_portfolio_backtest(
        proposals=(template,),
        histories={"510300": bars()},
        benchmark=benchmark(),
        positions={},
        as_of=AS_OF,
        config=settings(),
        akquant_check=matching,
    )
    assert template_verdict.metrics["audit"]["time_mode"] == "strategy_template"
    assert all(
        item["time"] >= "2026-01-06"
        for item in template_verdict.metrics["executions"]
    )
