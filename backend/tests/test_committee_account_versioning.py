from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.advisor.committee.backtest import AccountState, ExistingPosition
from app.advisor.committee.backtest import (
    BacktestSettings,
    StrategyTemplate,
    run_portfolio_backtest,
)
from app.advisor.committee.models import TradeDirection, TradeProposal
import pandas as pd
from app.advisor.committee.snapshot import (
    CriticalDataError,
    FutureDataError,
    SnapshotBuilder,
    default_collector_specs,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 21, 8, tzinfo=UTC)


def position(**patch):
    values = {
        "symbol": "510300",
        "quantity": 100,
        "available_quantity": 100,
        "acquired_at": AS_OF - timedelta(days=2),
        "cost": 9,
        "last_price": 10,
        "market_value": 1000,
        "price_as_of": AS_OF,
    }
    values.update(patch)
    return ExistingPosition(**values)


def test_account_state_requires_version_market_value_and_consistency():
    account = AccountState(
        cash=9000,
        equity=10_000,
        positions={"510300": position()},
        version="v7",
        as_of=AS_OF,
        source="archive:v7",
    )
    assert account.positions["510300"].market_value == 1000
    for patch in (
        {"version": ""},
        {"equity": 20_000},
        {
            "positions": {
                "510300": position(
                    price_as_of=AS_OF - timedelta(days=2)
                )
            }
        },
    ):
        with pytest.raises(ValidationError):
            AccountState.model_validate(account.model_dump() | patch)


def test_historical_account_collector_rejects_current_relabel():
    def current_source(*, user_id, as_of):
        return {
            "cash": 10_000,
            "equity": 10_000,
            "positions": [],
            "version": "current-v1",
            "data_as_of": AS_OF + timedelta(days=1),
        }

    spec = next(
        item
        for item in default_collector_specs(account_source=current_source)
        if item.name == "portfolio_account"
    )
    with pytest.raises((CriticalDataError, FutureDataError)):
        SnapshotBuilder((spec,)).build(
            user_id="u",
            as_of=AS_OF,
            strategy_version="v1",
            horizon="next_day",
            universe=("510300",),
        )


def test_historical_archived_account_preserves_true_data_as_of():
    archived_at = AS_OF - timedelta(hours=1)

    def archive_source(*, user_id, as_of):
        return {
            "cash": 10_000,
            "equity": 10_000,
            "positions": [],
            "version": "archive-42",
            "data_as_of": archived_at,
        }

    spec = next(
        item
        for item in default_collector_specs(account_source=archive_source)
        if item.name == "portfolio_account"
    )
    snapshot = SnapshotBuilder((spec,)).build(
        user_id="u",
        as_of=AS_OF,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
    )
    assert snapshot.items[0].data_as_of == archived_at
    assert snapshot.items[0].content["version"] == "archive-42"


def test_current_buy_cash_is_checked_for_all_proposals_with_costs():
    dates = pd.to_datetime(
        ["2026-07-17", "2026-07-20", "2026-07-21"], utc=True
    )
    frame = pd.DataFrame(
        {
            "time": dates,
            "open": [10, 10, 10],
            "high": [10.1, 10.1, 10.1],
            "low": [9.9, 9.9, 9.9],
            "close": [10, 10, 10],
            "volume": [100_000] * 3,
            "exact_limit_status": ["normal"] * 3,
            "exact_limit_locked": [False] * 3,
            "exact_limit_source": ["fake-daily-limit"] * 3,
        }
    )
    proposals = tuple(
        TradeProposal(
            user_id="u",
            run_id="r",
            strategy_id="s",
            strategy_version="1",
            symbol=symbol,
            direction=TradeDirection.BUY,
            target_weight=0.02,
            confidence=1,
            rationale="x",
            created_at=AS_OF,
        )
        for symbol in ("510300", "159915")
    )
    template = StrategyTemplate(
        strategy_id="s",
        strategy_version="1",
        as_of=AS_OF,
        signals={
            symbol: (
                datetime(2026, 7, 17, 15, tzinfo=UTC),
            )
            for symbol in ("510300", "159915")
        },
        source="fixed",
    )
    config = BacktestSettings(
        initial_cash=100_000,
        commission_rate=0.0003,
        minimum_commission=5,
        stamp_tax_rate=0.001,
        etf_stamp_tax_exempt=True,
        slippage_bps=10,
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
    account = AccountState(
        cash=3000,
        equity=100_000,
        positions={
            "000001": ExistingPosition(
                symbol="000001",
                quantity=9700,
                available_quantity=9700,
                acquired_at=AS_OF - timedelta(days=2),
                cost=9,
                last_price=10,
                market_value=97_000,
                price_as_of=AS_OF,
            )
        },
        version="v1",
        as_of=AS_OF,
        source="snapshot",
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
                dict(
                    item,
                    timestamp=item["time"],
                    submit_at=item["submit_at"],
                )
                for item in expected["executions"]
            ],
            "trade_count": expected["trade_count"],
        }

    verdict = run_portfolio_backtest(
        proposals=proposals,
        histories={"510300": frame, "159915": frame},
        benchmark=frame,
        account=account,
        template=template,
        as_of=AS_OF,
        config=config,
        akquant_check=matching,
    )
    assert "insufficient_cash" in verdict.metrics["reason_codes"]
    assert verdict.metrics["current_order_cash_required"] > account.cash
