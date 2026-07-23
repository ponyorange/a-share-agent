from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import time

import pandas as pd
import pytest

from app.advisor.committee.backtest import (
    BacktestSettings,
    DataValidationError,
    create_backtest_provider,
    run_portfolio_backtest,
    run_sync_in_killable_process,
)
from app.advisor.committee.models import TradeDirection, TradeProposal


AS_OF = datetime(2026, 1, 8, 15, tzinfo=timezone.utc)


def proposal(symbol: str = "510300", weight: float = 0.5) -> TradeProposal:
    return TradeProposal(
        user_id="u",
        run_id="r",
        symbol=symbol,
        direction=TradeDirection.BUY,
        target_weight=weight,
        confidence=0.8,
        rationale="fixed test signal",
        created_at=datetime(2026, 1, 5, 15, tzinfo=timezone.utc),
    )


def bars(
    closes=(10.0, 10.0, 11.0, 12.0),
    *,
    volumes=(100_000, 100_000, 100_000, 100_000),
) -> pd.DataFrame:
    times = pd.to_datetime(
        ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"],
        utc=True,
    )
    return pd.DataFrame(
        {
            "time": times,
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": volumes,
            "suspended": [False] * len(times),
            "asset_type": ["etf"] * len(times),
            "security_name": ["fixed ETF"] * len(times),
            "metadata_as_of": [AS_OF] * len(times),
            "metadata_source": ["fixed-authority"] * len(times),
            "exact_limit_status": ["normal"] * len(times),
            "exact_limit_locked": [False] * len(times),
            "exact_limit_source": ["fake-daily-limit"] * len(times),
        }
    )


def settings(**patch) -> BacktestSettings:
    values = {
        "initial_cash": 100_000,
        "commission_rate": 0.0003,
        "minimum_commission": 5,
        "stamp_tax_rate": 0.001,
        "etf_stamp_tax_exempt": True,
        "slippage_bps": 10,
        "lot_size": 100,
        "min_samples": 3,
        "min_trades": 1,
        "min_hit_rate": 0,
        "min_sharpe": -99,
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


def akquant_ok(**kwargs):
    expected = kwargs["expected"]
    return {
        "version": "0.3.7",
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
    }


def test_portfolio_backtest_enforces_t1_lot_fees_tax_and_slippage():
    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": bars()},
        benchmark=bars((10, 10.1, 10.2, 10.3)),
        as_of=AS_OF,
        config=settings(etf_stamp_tax_exempt=False),
        akquant_check=akquant_ok,
    )

    metrics = verdict.metrics
    executions = metrics["executions"]
    assert executions[0]["side"] == "buy"
    assert executions[0]["time"].startswith("2026-01-06")
    assert executions[0]["quantity"] % 100 == 0
    assert executions[0]["price"] == pytest.approx(10.01)
    assert executions[0]["commission"] >= 5
    assert executions[-1]["side"] == "sell"
    assert executions[-1]["time"].startswith("2026-01-08")
    assert executions[-1]["stamp_tax"] > 0
    assert verdict.passed is True


def test_etf_stamp_tax_can_be_exempt():
    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": bars()},
        benchmark=None,
        as_of=AS_OF,
        config=settings(),
        akquant_check=akquant_ok,
    )
    assert verdict.metrics["executions"][-1]["stamp_tax"] == 0


def test_future_rows_are_rejected_without_lookahead():
    frame = bars()
    future = frame.iloc[[-1]].copy()
    future["time"] = pd.Timestamp("2026-01-09", tz="UTC")
    future[["open", "high", "low", "close"]] = 999
    with pytest.raises(DataValidationError, match="after as_of"):
        run_portfolio_backtest(
            proposals=(proposal(),),
            histories={"510300": pd.concat([frame, future], ignore_index=True)},
            benchmark=None,
            as_of=AS_OF,
            config=settings(),
            akquant_check=akquant_ok,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda frame: frame.iloc[[1, 0, 2, 3]].reset_index(drop=True),
        lambda frame: pd.concat([frame, frame.iloc[[1]]], ignore_index=True),
        lambda frame: frame.assign(close=[10, 0, 11, 12]),
    ],
)
def test_invalid_order_duplicates_or_prices_fail_closed(mutate):
    with pytest.raises(DataValidationError):
        run_portfolio_backtest(
            proposals=(proposal(),),
            histories={"510300": mutate(bars())},
            benchmark=None,
            as_of=AS_OF,
            config=settings(),
            akquant_check=akquant_ok,
        )


def test_suspension_zero_volume_and_limit_up_are_not_fillable():
    frame = bars()
    frame.loc[1, "volume"] = 0
    frame.loc[2, ["open", "high", "low", "close"]] = 11
    frame.loc[2, "volume"] = 100_000
    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": frame},
        benchmark=None,
        as_of=AS_OF,
        config=settings(),
        akquant_check=akquant_ok,
    )
    assert verdict.metrics["trade_count"] == 0
    assert verdict.passed is False
    assert "insufficient_trades" in verdict.metrics["reason_codes"]
    assert {"zero_volume"} <= {
        item["reason"] for item in verdict.metrics["audit"]["blocked_orders"]
    }


def test_portfolio_metrics_are_equity_curve_not_symbol_average():
    verdict = run_portfolio_backtest(
        proposals=(proposal("510300", 0.5), proposal("159915", 0.5)),
        histories={
            "510300": bars((10, 10, 12, 14)),
            "159915": bars((10, 10, 9.2, 8.4)),
        },
        benchmark=bars((10, 10.1, 10.2, 10.3)),
        as_of=AS_OF,
        config=settings(),
        akquant_check=akquant_ok,
    )
    metrics = verdict.metrics
    assert metrics["sample_count"] == 3
    assert metrics["trade_count"] == 2
    assert 0 < metrics["hit_rate"] < 1
    assert metrics["max_drawdown"] >= 0
    assert isinstance(metrics["sharpe"], float)
    assert metrics["turnover"] > 0
    assert metrics["relative_benchmark_return"] is not None
    assert "per_symbol_average" not in metrics


def test_akquant_failure_is_structured_fail_closed():
    def broken(**_kwargs):
        raise RuntimeError("engine unavailable")

    verdict = run_portfolio_backtest(
        proposals=(proposal(),),
        histories={"510300": bars()},
        benchmark=None,
        as_of=AS_OF,
        config=settings(),
        akquant_check=broken,
    )
    assert verdict.passed is False
    assert "akquant_validation_failed" in verdict.metrics["reason_codes"]
    assert verdict.metrics["akquant"]["status"] == "error"


def test_invalid_or_missing_config_fails_closed():
    for value in (
        {},
        settings().model_dump() | {"lot_size": 0},
        settings().model_dump() | {"commission_rate": -1},
    ):
        with pytest.raises(ValueError):
            BacktestSettings.from_mapping(value)


def _late_file_write(path: str) -> str:
    time.sleep(0.4)
    Path(path).write_text("late", encoding="utf-8")
    return "done"


def test_cancelled_process_has_no_late_side_effect(tmp_path):
    marker = tmp_path / "late.txt"

    async def scenario():
        task = asyncio.create_task(
            run_sync_in_killable_process(_late_file_write, str(marker))
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.45)

    asyncio.run(scenario())
    assert not marker.exists()


def test_async_provider_uses_injected_fake_without_network_or_process():
    async def history_provider(symbol, as_of):
        assert as_of == AS_OF
        return bars()

    async def benchmark_provider(as_of):
        return bars((10, 10.1, 10.2, 10.3))

    async def validator(**kwargs):
        return akquant_ok(**kwargs)

    provider = create_backtest_provider(
        history_provider=history_provider,
        benchmark_provider=benchmark_provider,
        config=settings(),
        akquant_validator=validator,
    )

    class Snapshot:
        as_of = AS_OF

    class Context:
        user_id = "u"
        run_id = "r"
        snapshot = Snapshot()

    verdict = asyncio.run(provider(proposal(), Context()))
    assert verdict.passed is True
    assert verdict.user_id == "u"


def test_legacy_backtest_summary_contract_is_unchanged(monkeypatch):
    from app.advisor import backtest as legacy

    expected = {"engine": "event_study", "n_signals": 7}
    monkeypatch.setattr(
        legacy,
        "iter_backtest_summary_events",
        lambda symbols=None, force=False: iter(
            ({"event": "done", "data": expected},)
        ),
    )
    assert legacy.run_backtest_summary(["510300"], force=True) == expected


def test_legacy_event_study_runs_real_fixed_calculation(monkeypatch):
    from app.advisor import backtest as legacy

    dates = pd.date_range("2025-01-01", periods=65, freq="D", tz="UTC")
    fixed = pd.DataFrame(
        {
            "time": dates,
            "open": range(1, 66),
            "high": range(1, 66),
            "low": range(1, 66),
            "close": range(1, 66),
            "volume": [1000] * 65,
        }
    )
    monkeypatch.setattr(
        legacy,
        "fetch_daily_df",
        lambda symbol: ("fixed", fixed),
    )
    monkeypatch.setattr(
        legacy,
        "_score_at_index",
        lambda frame, benchmark, index: 1.0,
    )
    result = legacy.event_study_symbol(
        "510300",
        lookback=0,
        threshold=0.5,
        bench_df=None,
        sample_step=5,
    )
    assert result["engine"] == "event_study"
    assert result["n_signals"] == 8
    assert result["hit_rate"] == 1


def test_package_exports_async_provider_adapter():
    import app.advisor.committee as committee

    assert committee.create_backtest_provider is create_backtest_provider
