"""Lazy production dependency wiring for committee validation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from .agents import RoleAgentExecutor
from .backtest import (
    AccountState,
    BacktestSettings,
    ExistingPosition,
    DailyLimitStatus,
    SecurityMetadata,
    StrategyTemplate,
    create_backtest_provider,
    run_sync_in_killable_process,
)
from .graph import CommitteeDependencies
from .risk import (
    RiskInputs,
    RiskLimits,
    create_portfolio_risk_provider,
)


def _history_worker(symbol: str) -> pd.DataFrame:
    from ..features import fetch_daily_df

    _name, frame = fetch_daily_df(symbol)
    return frame.copy()


@lru_cache(maxsize=8)
def _trade_calendar_worker(as_of_iso: str) -> dict[str, Any]:
    import akshare as ak

    frame = ak.tool_trade_date_hist_sina()
    if "trade_date" not in frame:
        raise ValueError("AKShare trade calendar schema is unsupported")
    sessions = sorted(
        {
            pd.Timestamp(value).date().isoformat()
            for value in frame["trade_date"].dropna()
        }
    )
    as_of = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
    if not sessions or not any(value > as_of.date().isoformat() for value in sessions):
        raise ValueError("authoritative trade calendar has no future session")
    return {
        "sessions": sessions,
        "source": "AKShare.tool_trade_date_hist_sina",
        "data_as_of": as_of.isoformat(),
    }


def _metadata_worker(
    symbol: str,
    dates: tuple[str, ...],
    as_of_iso: str,
) -> dict[str, Any]:
    import akshare as ak

    asset_type = (
        "etf" if symbol.startswith(("5", "15", "16")) else "stock"
    )
    if asset_type == "etf":
        limit_pct = 0.10
    elif symbol.startswith(("300", "301", "688", "689")):
        limit_pct = 0.20
    elif symbol.startswith(("4", "8")):
        limit_pct = 0.30
    elif symbol.startswith(
        ("000", "001", "002", "003", "600", "601", "603", "605")
    ):
        limit_pct = 0.10
    else:
        raise ValueError(f"unknown exchange board for {symbol}")
    suspended_by_date: dict[str, bool] = {}
    for date in dates:
        suspended = ak.stock_tfp_em(date=date.replace("-", ""))
        suspended_code = next(
            (
                column
                for column in ("代码", "股票代码", "证券代码")
                if column in suspended
            ),
            None,
        )
        if suspended_code is None:
            raise ValueError("AKShare suspension schema is unsupported")
        suspended_by_date[date] = bool(
            (
                suspended[suspended_code]
                .astype(str)
                .str.zfill(6)
                == symbol
            ).any()
        )
    return {
        "symbol": symbol,
        "name": symbol,
        # Historical ST identity is intentionally not inferred from current spot.
        # Exact daily limit pools remain authoritative for fill restrictions.
        "is_st": False,
        "asset_type": asset_type,
        "limit_pct": limit_pct,
        "suspended_by_date": suspended_by_date,
        "as_of": as_of_iso,
        "source": "stable_code_rules+AKShare.stock_tfp_em",
    }


@lru_cache(maxsize=64)
def _daily_limit_pools(date: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import akshare as ak

    up = ak.stock_zt_pool_em(date=date.replace("-", ""))
    down = ak.stock_zt_pool_dtgc_em(date=date.replace("-", ""))
    return (
        up.to_dict(orient="records"),
        down.to_dict(orient="records"),
    )


def _daily_limit_status_worker(
    symbols: tuple[str, ...],
    dates: tuple[str, ...],
) -> list[dict[str, Any]]:
    output = []
    for date in sorted(set(dates)):
        up_rows, down_rows = _daily_limit_pools(date)
        for symbol in symbols:
            matched = None
            status = "normal"
            for candidate_status, rows in (
                ("limit_up", up_rows),
                ("limit_down", down_rows),
            ):
                for row in rows:
                    code = str(
                        row.get("代码")
                        or row.get("股票代码")
                        or row.get("证券代码")
                        or ""
                    ).zfill(6)
                    if code == symbol:
                        matched = row
                        status = candidate_status
                        break
                if matched is not None:
                    break
            latest = (
                float(matched.get("最新价"))
                if matched is not None
                and matched.get("最新价") not in (None, "", "-")
                else None
            )
            opened = (
                int(matched.get("开板次数") or 0)
                if matched is not None
                else 0
            )
            output.append(
                {
                    "symbol": symbol,
                    "trade_date": date,
                    "limit_up_price": (
                        latest if status == "limit_up" else None
                    ),
                    "limit_down_price": (
                        latest if status == "limit_down" else None
                    ),
                    "locked": matched is not None and opened == 0,
                    "status": status,
                    "source": (
                        "AKShare.stock_zt_pool_em"
                        "+stock_zt_pool_dtgc_em"
                    ),
                    "data_as_of": f"{date}T00:00:00+00:00",
                }
            )
    return output


def _benchmark_worker() -> pd.DataFrame:
    from ..features import load_benchmark

    return load_benchmark()


def _strategy_template_worker(payload: dict[str, Any]) -> dict[str, Any]:
    from ..backtest import _score_at_index

    benchmark = pd.DataFrame(payload["benchmark"])
    if "time" not in benchmark.columns:
        raise ValueError("benchmark history missing time column")
    benchmark["time"] = pd.to_datetime(benchmark["time"], utc=True)
    signals: dict[str, list[str]] = {}
    directions: dict[str, list[str]] = {}
    for symbol, records in payload["histories"].items():
        frame = pd.DataFrame(records)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        generated = []
        generated_directions = []
        for index in range(24, len(frame) - 1):
            score = _score_at_index(frame, benchmark, index)
            if score is not None and score >= payload["threshold"]:
                generated.append(frame.iloc[index]["time"].isoformat())
                generated_directions.append("buy")
            elif score is not None and score <= payload["sell_threshold"]:
                generated.append(frame.iloc[index]["time"].isoformat())
                generated_directions.append("sell")
        signals[symbol] = generated
        directions[symbol] = generated_directions
    return {
        "strategy_id": payload["strategy_id"],
        "strategy_version": payload["strategy_version"],
        "as_of": payload["as_of"],
        "signals": signals,
        "directions": directions,
        "source": "advisor.backtest._score_at_index",
    }


def _account_from_snapshot(context: Any) -> AccountState:
    for item in getattr(context.snapshot, "items", ()):
        if item.name not in {"account", "portfolio_account"}:
            continue
        content = dict(item.content)
        positions = {}
        for raw in content.get("positions") or ():
            value = dict(raw)
            symbol = str(value["symbol"])
            positions[symbol] = ExistingPosition(
                symbol=symbol,
                quantity=float(value.get("quantity", value.get("qty"))),
                available_quantity=float(
                    value.get(
                        "available_quantity",
                        value.get("available_qty"),
                    )
                ),
                acquired_at=value["acquired_at"],
                cost=float(value["cost"]),
                last_price=float(value["last_price"]),
                market_value=float(value["market_value"]),
                price_as_of=value["price_as_of"],
            )
        return AccountState(
            cash=float(content["cash"]),
            equity=float(content["equity"]),
            positions=positions,
            version=str(content["version"]),
            as_of=item.data_as_of or context.snapshot.as_of,
            source=f"snapshot:{item.source}:{item.name}",
        )
    raise ValueError("frozen snapshot account evidence missing")


def _ensure_utc(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _portfolio_worker(
    user_id: str,
    as_of_iso: str,
) -> dict[str, Any]:
    from ..paper import (
        get_account_snapshot_atomic,
        recover_stale_pending_mutations,
    )
    as_of = _ensure_utc(
        datetime.fromisoformat(as_of_iso.replace("Z", "+00:00")),
        field_name="as_of",
    )
    recover_stale_pending_mutations(300, user_id=user_id)
    account = get_account_snapshot_atomic(user_id, as_of=as_of)
    equity = float(account["equity"])
    if equity <= 0:
        raise ValueError("portfolio equity must be positive")
    positions = {
        str(item["symbol"]): item
        for item in account.get("positions", [])
    }
    symbol_weights = {
        symbol: float(item.get("market_value") or 0) / equity
        for symbol, item in positions.items()
    }
    total_exposure = sum(symbol_weights.values())
    sector_weights: dict[str, float] = {}
    for symbol, weight in symbol_weights.items():
        sector = (
            "ETF"
            if symbol.startswith(("5", "15", "16"))
            else "STOCK"
        )
        sector_weights[sector] = sector_weights.get(sector, 0) + weight
    sellable_quantity = {
        symbol: float(item["available_quantity"])
        for symbol, item in positions.items()
    }
    backtest_positions = {}
    for symbol, item in positions.items():
        backtest_positions[symbol] = {
            "symbol": symbol,
            "quantity": float(item["quantity"]),
            "available_quantity": sellable_quantity[symbol],
            "acquired_at": _ensure_utc(
                item["acquired_at"], field_name="acquired_at"
            ),
            "cost": float(item["cost"]),
            "last_price": float(item["last_price"]),
            "market_value": float(item["market_value"]),
            "price_as_of": _ensure_utc(
                item["price_as_of"], field_name="price_as_of"
            ),
        }
    return {
        "cash": float(account["cash"]),
        "equity": equity,
        "version": str(account["version"]),
        "account_version": int(account["account_version"]),
        "data_as_of": _ensure_utc(
            account["data_as_of"], field_name="data_as_of"
        ),
        "total_exposure": total_exposure,
        "symbol_weights": symbol_weights,
        "sector_weights": sector_weights,
        "sellable_quantity": sellable_quantity,
        "positions": backtest_positions,
    }


def create_production_dependencies(
    role_executor: RoleAgentExecutor,
    committee_config: Mapping[str, Any],
) -> CommitteeDependencies:
    """Parse all validation config now; defer every data call until invocation."""
    backtest_config = BacktestSettings.from_mapping(
        committee_config.get("backtest") or {}
    )
    risk_config = RiskLimits.from_mapping(
        committee_config.get("risk_limits") or {}
    )

    async def history_provider(
        symbol: str,
        as_of: datetime,
    ) -> pd.DataFrame:
        frame = await run_sync_in_killable_process(
            _history_worker,
            symbol,
        )
        return frame

    async def benchmark_provider(
        as_of: datetime,
    ) -> pd.DataFrame:
        del as_of
        return await run_sync_in_killable_process(_benchmark_worker)

    async def metadata_provider(
        symbol: str,
        dates: tuple[str, ...],
        as_of: datetime,
    ) -> SecurityMetadata:
        raw = await run_sync_in_killable_process(
            _metadata_worker,
            symbol,
            dates,
            as_of.isoformat(),
        )
        return SecurityMetadata.model_validate(raw)

    async def strategy_template_provider(
        proposals,
        histories,
        benchmark,
        as_of,
    ) -> StrategyTemplate:
        strategy_ids = {item.strategy_id for item in proposals}
        strategy_versions = {item.strategy_version for item in proposals}
        if (
            None in strategy_ids
            or None in strategy_versions
            or len(strategy_ids) != 1
            or len(strategy_versions) != 1
        ):
            raise ValueError("production proposal strategy template is missing")
        raw = await run_sync_in_killable_process(
            _strategy_template_worker,
            {
                "strategy_id": next(iter(strategy_ids)),
                "strategy_version": next(iter(strategy_versions)),
                "as_of": as_of.isoformat(),
                "threshold": 0.55,
                "sell_threshold": 0.35,
                "histories": {
                    symbol: frame.to_dict(orient="records")
                    for symbol, frame in histories.items()
                },
                "benchmark": benchmark.to_dict(orient="records"),
            },
        )
        return StrategyTemplate.model_validate(raw)

    async def daily_limit_provider(
        symbols: tuple[str, ...],
        dates: tuple[str, ...],
    ) -> tuple[DailyLimitStatus, ...]:
        raw = await run_sync_in_killable_process(
            _daily_limit_status_worker,
            symbols,
            dates,
        )
        return tuple(DailyLimitStatus.model_validate(item) for item in raw)

    async def market_provider(
        symbol: str,
        as_of: datetime,
    ) -> RiskInputs:
        frame = await history_provider(symbol, as_of)
        work = frame.copy()
        work["time"] = pd.to_datetime(work["time"], utc=True)
        eligible = work.loc[work["time"] <= pd.Timestamp(as_of)]
        if eligible.empty:
            raise ValueError(f"no risk data for {symbol}")
        close = pd.to_numeric(eligible["close"], errors="raise")
        volume = pd.to_numeric(eligible["volume"], errors="raise")
        returns = close.pct_change().dropna()
        volatility = (
            0.0
            if len(returns) < 2
            else float(returns.std(ddof=1) * np.sqrt(252))
        )
        latest = eligible.iloc[-1]
        return RiskInputs(
            as_of=as_of,
            current_price=float(latest["close"]),
            average_turnover=float(
                (close * volume).tail(20).mean()
            ),
            annualized_volatility=volatility,
            current_total_exposure=0,
            current_symbol_weight=0,
            sector=(
                "ETF"
                if symbol.startswith(("5", "15", "16"))
                else "STOCK"
            ),
            sector_exposure=0,
            data_as_of=latest["time"].to_pydatetime(),
            captured_at=latest["time"].to_pydatetime(),
            evidence_quality=1,
            sellable_quantity=0,
            requested_quantity=backtest_config.lot_size,
        )

    async def account_provider(context: Any) -> AccountState:
        return _account_from_snapshot(context)

    async def portfolio_provider(context: Any) -> Mapping[str, Any]:
        account = await account_provider(context)
        weights = {
            symbol: float(position.market_value) / account.equity
            for symbol, position in account.positions.items()
        }
        sector_weights: dict[str, float] = {}
        for symbol, weight in weights.items():
            sector = (
                "ETF"
                if symbol.startswith(("5", "15", "16"))
                else "STOCK"
            )
            sector_weights[sector] = (
                sector_weights.get(sector, 0) + weight
            )
        return {
            "equity": account.equity,
            "total_exposure": max(
                0.0, (account.equity - account.cash) / account.equity
            ),
            "symbol_weights": weights,
            "sector_weights": sector_weights,
            "sellable_quantity": {
                symbol: position.available_quantity
                for symbol, position in account.positions.items()
            },
            "positions": account.positions,
        }

    async def positions_provider(
        context: Any,
    ) -> Mapping[str, ExistingPosition]:
        return (await account_provider(context)).positions

    return CommitteeDependencies(
        role_executor=role_executor,
        portfolio_backtest=create_backtest_provider(
            history_provider=history_provider,
            benchmark_provider=benchmark_provider,
            metadata_provider=metadata_provider,
            positions_provider=positions_provider,
            strategy_template_provider=strategy_template_provider,
            account_provider=account_provider,
            daily_limit_provider=daily_limit_provider,
            config=backtest_config,
        ),
        portfolio_risk=create_portfolio_risk_provider(
            market_provider=market_provider,
            config=risk_config,
            portfolio_provider=portfolio_provider,
        ),
    )
