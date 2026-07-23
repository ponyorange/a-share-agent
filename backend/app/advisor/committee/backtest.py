"""Strict portfolio backtesting and AKQuant 0.3.7 reconciliation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
import math
import multiprocessing as mp
from multiprocessing.connection import Connection
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import BacktestVerdict, TradeDirection, TradeProposal
from .risk import proposal_semantics_hash


AKQUANT_VERSION = "0.3.7"


class DataValidationError(ValueError):
    pass


class BacktestSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_cash: float = Field(gt=0)
    commission_rate: float = Field(ge=0, le=0.1)
    minimum_commission: float = Field(ge=0)
    stamp_tax_rate: float = Field(ge=0, le=0.1)
    etf_stamp_tax_exempt: bool
    slippage_bps: float = Field(ge=0, le=1000)
    lot_size: int = Field(ge=1)
    min_samples: int = Field(ge=2)
    min_trades: int = Field(ge=1)
    min_hit_rate: float = Field(ge=0, le=1)
    min_sharpe: float
    max_drawdown: float = Field(gt=0, le=1)
    max_weight_deviation: float = Field(ge=0, le=0.25)
    akquant_return_tolerance: float = Field(ge=0, le=0.1)
    akquant_drawdown_tolerance: float = Field(ge=0, le=0.1)
    akquant_sharpe_tolerance: float = Field(ge=0, le=10)
    akquant_turnover_tolerance: float = Field(ge=0, le=0.25)
    require_akquant: Literal[True]
    historical_limit_model: Literal[
        "asset_specific"
    ] = "asset_specific"
    exact_historical_limit_rules: Literal[True] = True
    stock_historical_limit_model: Literal[
        "exact_daily_limit_pool"
    ] = "exact_daily_limit_pool"
    etf_historical_limit_model: Literal[
        "ohlcv_locked_board"
    ] = "ohlcv_locked_board"
    exact_etf_historical_limit_rules: Literal[False] = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> BacktestSettings:
        missing = {
            name
            for name, field in cls.model_fields.items()
            if field.is_required()
        }.difference(value)
        if missing:
            raise ValueError(
                "missing committee.backtest settings: "
                + ", ".join(sorted(missing))
            )
        return cls.model_validate(value)


class ExistingPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    available_quantity: float = Field(ge=0)
    acquired_at: datetime
    cost: float = Field(gt=0)
    last_price: float | None = Field(default=None, gt=0)
    market_value: float | None = Field(default=None, ge=0)
    price_as_of: datetime | None = None

    @field_validator("acquired_at", "price_as_of")
    @classmethod
    def acquired_at_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _as_utc(value)

    @model_validator(mode="after")
    def available_not_above_total(self) -> ExistingPosition:
        if self.available_quantity > self.quantity:
            raise ValueError("available quantity exceeds position")
        return self


class SecurityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(min_length=1)
    name: str = Field(min_length=1)
    is_st: bool
    asset_type: Literal["stock", "etf"]
    limit_pct: float = Field(gt=0, le=1)
    suspended_by_date: Mapping[str, bool]
    as_of: datetime
    source: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def metadata_as_of_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)


class DailyLimitStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    trade_date: str
    limit_up_price: float | None = Field(default=None, gt=0)
    limit_down_price: float | None = Field(default=None, gt=0)
    locked: bool
    status: Literal["normal", "limit_up", "limit_down"]
    source: str = Field(min_length=1)
    data_as_of: datetime | None = None

    @field_validator("data_as_of")
    @classmethod
    def status_as_of_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _as_utc(value)


class AccountState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cash: float = Field(ge=0)
    equity: float = Field(gt=0)
    positions: Mapping[
        str, ExistingPosition | Mapping[str, Any]
    ] = Field(default_factory=dict)
    version: str = Field(default="test-v1", min_length=1)
    as_of: datetime
    source: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def account_as_of_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @model_validator(mode="after")
    def validate_account(self) -> AccountState:
        if self.cash > self.equity + 1e-9:
            raise ValueError("account cash cannot exceed equity")
        positions = {
            symbol: ExistingPosition.model_validate(value)
            for symbol, value in self.positions.items()
        }
        market_value = 0.0
        for symbol, position in positions.items():
            if (
                position.last_price is None
                or position.market_value is None
                or position.price_as_of is None
            ):
                raise ValueError(
                    f"position valuation is incomplete: {symbol}"
                )
            expected = position.quantity * position.last_price
            if abs(position.market_value - expected) > max(
                0.01, expected * 1e-6
            ):
                raise ValueError(
                    f"position market value is inconsistent: {symbol}"
                )
            if (
                position.price_as_of > self.as_of
                or (self.as_of - position.price_as_of).total_seconds()
                > 86_400
            ):
                raise ValueError(f"position price is stale: {symbol}")
            market_value += position.market_value
        if abs(self.cash + market_value - self.equity) > max(
            0.01, self.equity * 1e-6
        ):
            raise ValueError("account equity is inconsistent")
        return self


class StrategyTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    as_of: datetime
    signals: Mapping[str, tuple[datetime, ...]]
    directions: Mapping[
        str, tuple[TradeDirection, ...]
    ] = Field(default_factory=dict)
    source: str = Field(min_length=1)

    @field_validator("as_of")
    @classmethod
    def template_as_of_utc(cls, value: datetime) -> datetime:
        return _as_utc(value)

    @field_validator("signals")
    @classmethod
    def signal_times_utc(
        cls,
        value: Mapping[str, tuple[datetime, ...]],
    ) -> Mapping[str, tuple[datetime, ...]]:
        return {
            symbol: tuple(_as_utc(item) for item in items)
            for symbol, items in value.items()
        }

    @model_validator(mode="after")
    def direction_lengths_match(self) -> StrategyTemplate:
        for symbol, directions in self.directions.items():
            if len(directions) != len(self.signals.get(symbol, ())):
                raise ValueError("template signal directions length mismatch")
        return self


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataValidationError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _prepare_frame(
    frame: pd.DataFrame,
    *,
    as_of: datetime,
    label: str,
    benchmark: bool = False,
) -> pd.DataFrame:
    required = {"time", "close"}
    if not benchmark:
        required |= {
            "open",
            "high",
            "low",
            "volume",
        }
    missing = required.difference(frame.columns)
    if missing:
        raise DataValidationError(f"{label} missing columns: {sorted(missing)}")
    work = frame.copy()
    try:
        work["time"] = pd.to_datetime(work["time"], utc=True)
    except Exception as exc:
        raise DataValidationError(f"{label} has invalid time") from exc
    if work["time"].duplicated().any():
        raise DataValidationError(f"{label} has duplicate time")
    dates = work["time"].dt.normalize()
    if dates.duplicated().any():
        raise DataValidationError(f"{label} has more than one bar per day")
    if not work["time"].is_monotonic_increasing:
        raise DataValidationError(f"{label} must be sorted by time")
    if (work["time"] > pd.Timestamp(as_of)).any():
        raise DataValidationError(f"{label} contains rows after as_of")
    price_columns = ("close",) if benchmark else ("open", "high", "low", "close")
    for column in price_columns:
        numeric = pd.to_numeric(work[column], errors="coerce")
        if (~np.isfinite(numeric)).any() or (numeric <= 0).any():
            raise DataValidationError(f"{label} has invalid {column}")
        work[column] = numeric.astype(float)
    if not benchmark:
        volume = pd.to_numeric(work["volume"], errors="coerce")
        if (~np.isfinite(volume)).any() or (volume < 0).any():
            raise DataValidationError(f"{label} has invalid volume")
        work["volume"] = volume.astype(float)
        if "suspended" not in work:
            work["suspended"] = pd.NA
        if "asset_type" not in work:
            work["asset_type"] = "unknown"
        if "metadata_as_of" in work:
            metadata_times = pd.to_datetime(
                work["metadata_as_of"], utc=True, errors="coerce"
            )
            if metadata_times.isna().any() or (
                metadata_times > pd.Timestamp(as_of)
            ).any():
                raise DataValidationError(
                    f"{label} has invalid metadata_as_of"
                )
    if work.empty:
        raise DataValidationError(f"{label} has no eligible rows")
    return work.reset_index(drop=True)


def _is_etf(symbol: str, frame: pd.DataFrame) -> bool:
    values = {str(value).lower() for value in frame["asset_type"].dropna()}
    if len(values) != 1:
        raise DataValidationError(f"{symbol} has ambiguous asset_type")
    return values == {"etf"}


def _limit_pct(symbol: str, frame: pd.DataFrame) -> float | None:
    if "historical_limit_pct" in frame:
        values = {
            float(value)
            for value in frame["historical_limit_pct"].dropna()
        }
        if len(values) != 1 or not 0 < next(iter(values), 0) <= 1:
            raise DataValidationError(f"{symbol} has invalid limit_pct")
        return next(iter(values))
    return None


def _blocked_reason(
    row: pd.Series,
    previous_close: float,
    side: str,
    limit_pct: float | None,
) -> str | None:
    suspended = row.get("suspended")
    if pd.notna(suspended) and bool(suspended):
        return "suspended"
    if float(row["volume"]) <= 0:
        return "zero_volume"
    exact_status = row.get("exact_limit_status")
    if pd.isna(exact_status):
        return "exact_historical_limit_status_missing"
    if (
        str(exact_status) == "limit_up"
        and bool(row.get("exact_limit_locked", False))
        and side == "buy"
    ):
        return "authoritative_locked_limit_up"
    if (
        str(exact_status) == "limit_down"
        and bool(row.get("exact_limit_locked", False))
        and side == "sell"
    ):
        return "authoritative_locked_limit_down"
    if (
        abs(float(row["high"]) - float(row["low"])) <= 1e-10
        and abs(float(row["high"]) / previous_close - 1) >= 0.03
    ):
        if float(row["high"]) > previous_close and side == "buy":
            return "locked_limit_up"
        if float(row["high"]) < previous_close and side == "sell":
            return "locked_limit_down"
    price = float(row["open"])
    if (
        limit_pct is not None
        and side == "buy"
        and price >= previous_close * (1 + limit_pct) - 1e-10
    ):
        return "limit_up"
    if (
        limit_pct is not None
        and side == "sell"
        and price <= previous_close * (1 - limit_pct) + 1e-10
    ):
        return "limit_down"
    return None


def _tradeability_source(row: pd.Series, reason: str | None) -> str:
    if reason and reason.startswith("authoritative_locked"):
        return str(row.get("exact_limit_source") or "daily_limit_provider")
    if reason in {"locked_limit_up", "locked_limit_down"}:
        return "unadjusted_ohlcv_one_price_lock"
    if reason == "zero_volume":
        return "ohlcv_zero_volume"
    suspended = row.get("suspended")
    if pd.isna(suspended):
        return "derived_from_ohlcv"
    return "authoritative_daily_status"


def _commission(notional: float, config: BacktestSettings) -> float:
    return max(notional * config.commission_rate, config.minimum_commission)


def _attach_metadata(
    frame: pd.DataFrame,
    metadata: SecurityMetadata,
) -> pd.DataFrame:
    work = frame.copy()
    times = pd.to_datetime(work["time"], utc=True)
    dates = [value.date().isoformat() for value in times]
    work["suspended"] = [
        (
            bool(metadata.suspended_by_date[date])
            if date in metadata.suspended_by_date
            else pd.NA
        )
        for date in dates
    ]
    work["asset_type"] = metadata.asset_type
    work["security_name"] = metadata.name
    work["metadata_as_of"] = metadata.as_of
    work["metadata_source"] = metadata.source
    return work


def _trigger_price(
    proposal: TradeProposal,
    row: pd.Series,
) -> tuple[float | None, str | None]:
    side = proposal.direction
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    if proposal.order_type == "market":
        return open_price, None
    limit = proposal.limit_price
    if limit is None:
        return None, "missing_limit_price"
    if proposal.order_type == "limit":
        if side is TradeDirection.BUY:
            if low > limit:
                return None, "limit_price_not_reached"
            return min(open_price, limit), None
        if high < limit:
            return None, "limit_price_not_reached"
        return max(open_price, limit), None
    stop = proposal.stop_price
    if stop is None:
        return None, "missing_stop_price"
    if side is TradeDirection.BUY:
        if high < stop:
            return None, "stop_limit_not_triggered"
        return None, "stop_limit_path_unknown"
    if low > stop:
        return None, "stop_limit_not_triggered"
    return None, "stop_limit_path_unknown"


def _validate_proposals(proposals: Sequence[TradeProposal]) -> tuple[TradeProposal, ...]:
    items = tuple(proposals)
    if not items:
        raise ValueError("at least one proposal is required")
    scope = {(item.user_id, item.run_id) for item in items}
    if len(scope) != 1:
        raise ValueError("portfolio proposals must share run scope")
    symbols = [item.symbol for item in items]
    if len(symbols) != len(set(symbols)):
        raise ValueError("portfolio proposals must have unique symbols")
    buy_weight = sum(
        item.target_weight
        for item in items
        if item.direction is TradeDirection.BUY
    )
    if buy_weight > 1 + 1e-12:
        raise ValueError("portfolio target weight sum exceeds 1")
    return items


def _simulate(
    *,
    proposals: Sequence[TradeProposal],
    histories: Mapping[str, pd.DataFrame],
    benchmark: pd.DataFrame | None,
    positions: Mapping[str, ExistingPosition | Mapping[str, Any]] | None,
    account: AccountState | None,
    template: StrategyTemplate | None,
    as_of: datetime,
    config: BacktestSettings,
) -> dict[str, Any]:
    items = _validate_proposals(proposals)
    cutoff = _as_utc(as_of)
    prepared = {
        symbol: _prepare_frame(
            frame,
            as_of=cutoff,
            label=symbol,
        )
        for symbol, frame in histories.items()
    }
    missing = {item.symbol for item in items}.difference(prepared)
    if missing:
        raise DataValidationError(f"missing histories: {sorted(missing)}")
    benchmark_frame = (
        None
        if benchmark is None
        else _prepare_frame(
            benchmark,
            as_of=cutoff,
            label="benchmark",
            benchmark=True,
        )
    )
    account_positions = account.positions if account is not None else positions or {}
    live_existing = {
        symbol: ExistingPosition.model_validate(value)
        for symbol, value in account_positions.items()
    }
    if any(position.symbol != symbol for symbol, position in live_existing.items()):
        raise ValueError("position symbol key mismatch")
    existing = {} if template is not None else live_existing
    limits = {
        symbol: _limit_pct(symbol, prepared[symbol])
        for symbol in {item.symbol for item in items}
    }
    audit: dict[str, Any] = {
        "blocked_orders": [],
        "tradeability_checks": [],
        "historical_limit_model": (
            f"{config.historical_limit_model};"
            "exact_daily_limit_status_required;"
            "unadjusted_ohlcv_one_price_cross_check;"
            "source_failure_fail_closed"
        ),
        "historical_limit_models_by_asset": {
            "stock": config.stock_historical_limit_model,
            "etf": config.etf_historical_limit_model,
        },
        "limit_pct": limits,
        "metadata": {
            symbol: {
                "source": str(
                    frame.iloc[-1].get(
                        "metadata_source", "historical_ohlcv_only"
                    )
                ),
                "as_of": (
                    pd.Timestamp(
                        frame.iloc[-1]["metadata_as_of"]
                    ).isoformat()
                    if "metadata_as_of" in frame
                    else None
                ),
                "name": str(
                    frame.iloc[-1].get("security_name", symbol)
                ),
            }
            for symbol, frame in prepared.items()
        },
        "time_mode": (
            "strategy_template"
            if template is not None
            or any(item.strategy_template_as_of is not None for item in items)
            else "live_proposal"
        ),
    }
    if account is not None and account.as_of > cutoff:
        raise DataValidationError("account evidence is after as_of")
    cash = (
        config.initial_cash
        if template is not None
        else account.cash
        if account is not None
        else config.initial_cash
    )
    executions: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    requested_weights: dict[str, float] = {}
    position_quantities = {
        symbol: position.quantity
        for symbol, position in existing.items()
    }
    starting_equity = (
        config.initial_cash
        if template is not None
        else account.equity
        if account is not None
        else config.initial_cash
        + sum(
            position.quantity
            * float(prepared[symbol].iloc[0]["close"])
            for symbol, position in existing.items()
            if symbol in prepared
        )
    )
    if template is not None:
        current_order_cash_required = 0.0
        if account is not None:
            for item in items:
                if item.direction is not TradeDirection.BUY:
                    continue
                notional = (
                    account.equity
                    * item.target_weight
                    * (1 + config.slippage_bps / 10_000)
                )
                current_order_cash_required += (
                    notional + _commission(notional, config)
                )
            if current_order_cash_required > account.cash + 1e-9:
                reason_codes.append("insufficient_cash")
        for item in items:
            if (
                item.strategy_id != template.strategy_id
                or item.strategy_version != template.strategy_version
            ):
                reason_codes.append("strategy_template_mismatch")
        audit["strategy_template"] = {
            "strategy_id": template.strategy_id,
            "strategy_version": template.strategy_version,
            "source": template.source,
            "as_of": template.as_of.isoformat(),
            "signals": {
                symbol: [value.isoformat() for value in values]
                for symbol, values in template.signals.items()
            },
            "directions": {
                symbol: [value.value for value in values]
                for symbol, values in template.directions.items()
            },
        }
    if benchmark is None:
        reason_codes.append("benchmark_missing")
    if template is None or account is None:
        current_order_cash_required = 0.0
    synthetic_entries: list[dict[str, Any]] = []

    execution_specs: list[tuple[TradeProposal, datetime | None]] = []
    for item in items:
        if template is None:
            execution_specs.append((item, None))
            continue
        signals = tuple(template.signals.get(item.symbol, ()))
        directions = tuple(template.directions.get(item.symbol, ()))
        if directions:
            signals = tuple(
                signal
                for signal, direction in zip(signals, directions)
                if direction is item.direction
            )
        if not signals:
            reason_codes.append("strategy_template_no_support")
        execution_specs.extend((item, signal) for signal in signals)

    for item, template_signal in execution_specs:
        if item.direction is TradeDirection.HOLD or item.target_weight <= 0:
            continue
        requested_weights[item.symbol] = item.target_weight
        frame = prepared[item.symbol]
        if template_signal is not None:
            signal_time = template_signal
        else:
            signal_time = item.strategy_template_as_of or item.created_at
        candidates = frame.index[
            frame["time"] > pd.Timestamp(signal_time)
        ].tolist()
        if not candidates:
            reason_codes.append("no_next_trading_bar")
            continue
        buy_index = candidates[0]
        buy_row = frame.iloc[buy_index]
        if item.expires_at is not None and buy_row["time"] > pd.Timestamp(item.expires_at):
            reason_codes.append("proposal_expired_before_execution")
            continue
        if buy_index == 0:
            reason_codes.append("missing_previous_close")
            continue
        blocked = _blocked_reason(
            buy_row,
            float(frame.iloc[buy_index - 1]["close"]),
            "buy",
            limits[item.symbol],
        )
        audit["tradeability_checks"].append(
            {
                "symbol": item.symbol,
                "time": buy_row["time"].isoformat(),
                "source": _tradeability_source(buy_row, blocked),
                "result": blocked or "tradable",
            }
        )
        raw_price, trigger_error = _trigger_price(item, buy_row)
        if trigger_error is not None:
            blocked = trigger_error
        if blocked:
            audit["blocked_orders"].append(
                {
                    "symbol": item.symbol,
                    "side": "buy",
                    "time": buy_row["time"].isoformat(),
                    "reason": blocked,
                }
            )
            reason_codes.append("unfilled_target")
            continue
        assert raw_price is not None
        if item.direction is TradeDirection.SELL:
            live_holding = live_existing.get(item.symbol)
            if live_holding is None:
                reason_codes.append("sell_without_position")
            elif live_holding.available_quantity <= 0:
                reason_codes.append("t_plus_one_unavailable")
            if template is not None:
                entry_row = frame.iloc[max(0, buy_index - 2)]
                entry_price = float(entry_row["close"])
                quantity = (
                    math.floor(
                        config.initial_cash
                        * item.target_weight
                        / entry_price
                        / config.lot_size
                    )
                    * config.lot_size
                )
                if quantity < config.lot_size:
                    reason_codes.append("template_sell_size_too_small")
                    continue
                synthetic_entries.append(
                    {
                        "symbol": item.symbol,
                        "time": entry_row["time"].isoformat(),
                        "quantity": quantity,
                        "price": entry_price,
                        "notional": quantity * entry_price,
                        "signal_key": (
                            f"{item.symbol}:{signal_time.isoformat()}"
                        ),
                    }
                )
                cash -= quantity * entry_price
                holding = ExistingPosition(
                    symbol=item.symbol,
                    quantity=quantity,
                    available_quantity=quantity,
                    acquired_at=entry_row["time"].to_pydatetime(),
                    cost=entry_price,
                )
            else:
                holding = live_holding
                if (
                    holding is None
                    or position_quantities.get(item.symbol, 0) <= 0
                ):
                    continue
                if (
                    buy_row["time"].date() <= holding.acquired_at.date()
                    or holding.available_quantity <= 0
                ):
                    continue
            price = raw_price * (1 - config.slippage_bps / 10_000)
            requested_quantity = (
                math.floor(
                    starting_equity
                    * item.target_weight
                    / price
                    / config.lot_size
                )
                * config.lot_size
            )
            quantity = (
                holding.quantity
                if template is not None
                else min(
                    holding.available_quantity,
                    position_quantities[item.symbol],
                    requested_quantity,
                )
            )
            quantity = (
                math.floor(quantity / config.lot_size)
                * config.lot_size
            )
            if quantity < config.lot_size:
                reason_codes.append("t_plus_one_unavailable")
                continue
            notional = quantity * price
            fee = _commission(notional, config)
            stamp_tax = (
                0.0
                if config.etf_stamp_tax_exempt
                and _is_etf(item.symbol, frame)
                else notional * config.stamp_tax_rate
            )
            cash += notional - fee - stamp_tax
            if template is None:
                position_quantities[item.symbol] -= quantity
            executions.append(
                {
                    "symbol": item.symbol,
                    "side": "sell",
                    "signal_time": signal_time.isoformat(),
                    "signal_at_close": signal_time.isoformat(),
                    "submit_at": signal_time.isoformat(),
                    "proposal_created_at": item.created_at.isoformat(),
                    "time": buy_row["time"].isoformat(),
                    "trade_date": buy_row["time"].date().isoformat(),
                    "quantity": quantity,
                    "price": price,
                    "notional": notional,
                    "commission": fee,
                    "stamp_tax": stamp_tax,
                    "total_fee": fee + stamp_tax,
                    "starting_cost": holding.cost,
                    "signal_key": f"{item.symbol}:{signal_time.isoformat()}",
                    "synthetic_entry_time": holding.acquired_at.isoformat(),
                    "order_type": item.order_type,
                    "limit_price": item.limit_price,
                    "stop_price": item.stop_price,
                }
            )
            continue
        price = raw_price * (1 + config.slippage_bps / 10_000)
        budget = starting_equity * item.target_weight
        quantity = math.floor(budget / price / config.lot_size) * config.lot_size
        if quantity < config.lot_size:
            reason_codes.extend(("insufficient_cash", "target_weight_deviation"))
            continue
        notional = quantity * price
        fee = _commission(notional, config)
        if notional + fee > cash + 1e-9:
            affordable = math.floor(
                (cash - config.minimum_commission)
                / price
                / config.lot_size
            ) * config.lot_size
            quantity = max(0, affordable)
            reason_codes.append("insufficient_cash")
            if quantity < config.lot_size:
                reason_codes.append("target_weight_deviation")
                continue
            notional = quantity * price
            fee = _commission(notional, config)
        actual_weight = notional / starting_equity
        if abs(actual_weight - item.target_weight) > config.max_weight_deviation:
            reason_codes.append("target_weight_deviation")
        cash -= notional + fee
        position_quantities[item.symbol] = (
            position_quantities.get(item.symbol, 0) + quantity
        )
        executions.append(
            {
                "symbol": item.symbol,
                "side": "buy",
                "signal_time": signal_time.isoformat(),
                "signal_at_close": signal_time.isoformat(),
                "submit_at": signal_time.isoformat(),
                "proposal_created_at": item.created_at.isoformat(),
                "time": buy_row["time"].isoformat(),
                "trade_date": buy_row["time"].date().isoformat(),
                "quantity": quantity,
                "price": price,
                "notional": notional,
                "commission": fee,
                "stamp_tax": 0.0,
                "total_fee": fee,
                "target_weight": item.target_weight,
                "actual_weight": actual_weight,
                "order_type": item.order_type,
                "limit_price": item.limit_price,
                "stop_price": item.stop_price,
            }
        )

        stop_signal_index: int | None = None
        if item.stop_price is not None:
            for index in range(buy_index, len(frame) - 1):
                if float(frame.iloc[index]["close"]) <= item.stop_price:
                    stop_signal_index = index
                    break
        signal_index = (
            buy_index
            if template is not None
            else stop_signal_index
            if stop_signal_index is not None
            else len(frame) - 2
        )
        if signal_index < buy_index:
            reason_codes.append("open_position_at_as_of")
            continue
        sell_index = signal_index + 1
        if sell_index >= len(frame):
            reason_codes.append("open_position_at_as_of")
            continue
        sell_row = frame.iloc[sell_index]
        if sell_row["time"].date() <= buy_row["time"].date():
            reason_codes.append("t_plus_one_violation")
            continue
        blocked = _blocked_reason(
            sell_row,
            float(frame.iloc[sell_index - 1]["close"]),
            "sell",
            limits[item.symbol],
        )
        audit["tradeability_checks"].append(
            {
                "symbol": item.symbol,
                "time": sell_row["time"].isoformat(),
                "source": _tradeability_source(sell_row, blocked),
                "result": blocked or "tradable",
            }
        )
        if blocked:
            audit["blocked_orders"].append(
                {
                    "symbol": item.symbol,
                    "side": "sell",
                    "time": sell_row["time"].isoformat(),
                    "reason": blocked,
                }
            )
            reason_codes.append("open_position_at_as_of")
            continue
        sell_price = float(sell_row["open"]) * (
            1 - config.slippage_bps / 10_000
        )
        sell_notional = quantity * sell_price
        sell_fee = _commission(sell_notional, config)
        stamp_tax = (
            0.0
            if config.etf_stamp_tax_exempt
            and _is_etf(item.symbol, frame)
            else sell_notional * config.stamp_tax_rate
        )
        cash += sell_notional - sell_fee - stamp_tax
        position_quantities[item.symbol] -= quantity
        executions.append(
            {
                "symbol": item.symbol,
                "side": "sell",
                "signal_time": frame.iloc[signal_index]["time"].isoformat(),
                "signal_at_close": frame.iloc[signal_index]["time"].isoformat(),
                "submit_at": frame.iloc[signal_index]["time"].isoformat(),
                "proposal_created_at": item.created_at.isoformat(),
                "time": sell_row["time"].isoformat(),
                "trade_date": sell_row["time"].date().isoformat(),
                "quantity": quantity,
                "price": sell_price,
                "notional": sell_notional,
                "commission": sell_fee,
                "stamp_tax": stamp_tax,
                "total_fee": sell_fee + stamp_tax,
                "order_type": "market",
                "limit_price": None,
                "stop_price": None,
            }
        )

    all_dates = sorted(
        {
            timestamp.normalize()
            for frame in prepared.values()
            for timestamp in frame["time"]
        }
    )
    replay_cash = (
        config.initial_cash
        if template is not None
        else account.cash
        if account is not None
        else config.initial_cash
    )
    replay_positions: dict[str, float] = {
        symbol: position.quantity
        for symbol, position in existing.items()
    }
    execution_by_date: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for execution in executions:
        date_key = pd.Timestamp(execution["time"]).normalize()
        execution_by_date.setdefault(date_key, []).append(execution)
    entries_by_date: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for entry in synthetic_entries:
        entries_by_date.setdefault(
            pd.Timestamp(entry["time"]).normalize(), []
        ).append(entry)
    equity_curve: list[dict[str, Any]] = []
    last_close: dict[str, float] = {}
    for date_key in all_dates:
        for symbol, frame in prepared.items():
            rows = frame.loc[frame["time"].dt.normalize() == date_key]
            if not rows.empty:
                last_close[symbol] = float(rows.iloc[-1]["close"])
        for entry in entries_by_date.get(date_key, []):
            replay_cash -= entry["notional"]
            replay_positions[entry["symbol"]] = (
                replay_positions.get(entry["symbol"], 0)
                + entry["quantity"]
            )
        for execution in execution_by_date.get(date_key, []):
            symbol = execution["symbol"]
            quantity = float(execution["quantity"])
            if execution["side"] == "buy":
                replay_cash -= execution["notional"] + execution["total_fee"]
                replay_positions[symbol] = (
                    replay_positions.get(symbol, 0) + quantity
                )
            else:
                replay_cash += execution["notional"] - execution["total_fee"]
                replay_positions[symbol] = (
                    replay_positions.get(symbol, 0) - quantity
                )
                if replay_positions[symbol] <= 1e-9:
                    replay_positions.pop(symbol)
        equity = replay_cash + sum(
            quantity * last_close[symbol]
            for symbol, quantity in replay_positions.items()
            if symbol in last_close
        )
        equity_curve.append(
            {"time": date_key.isoformat(), "equity": equity}
        )
    ending_equity = equity_curve[-1]["equity"] if equity_curve else config.initial_cash
    investment_dates = [
        value
        for item in executions
        for value in (
            (
                pd.Timestamp(item["signal_time"]).normalize(),
                pd.Timestamp(item["time"]).normalize(),
            )
            if "starting_cost" in item
            else (pd.Timestamp(item["time"]).normalize(),)
        )
    ]
    if investment_dates:
        start_date, end_date = min(investment_dates), max(investment_dates)
        active_curve = [
            item
            for item in equity_curve
            if start_date <= pd.Timestamp(item["time"]) <= end_date
        ]
    else:
        active_curve = []
    equity_values = np.asarray(
        [float(item["equity"]) for item in active_curve],
        dtype=float,
    )
    returns = (
        pd.Series(equity_values).pct_change().dropna().to_numpy()
        if len(equity_values) > 1
        else np.asarray([])
    )
    sharpe = (
        0.0
        if len(returns) < 2 or float(np.std(returns, ddof=1)) == 0
        else float(
            np.mean(returns)
            / np.std(returns, ddof=1)
            * np.sqrt(252)
        )
    )
    peaks = np.maximum.accumulate(equity_values)
    max_drawdown = (
        0.0
        if not len(equity_values)
        else float(np.max((peaks - equity_values) / peaks))
    )
    completed = []
    for symbol in requested_weights:
        buys = [
            item
            for item in executions
            if item["symbol"] == symbol and item["side"] == "buy"
        ]
        sells = [
            item
            for item in executions
            if item["symbol"] == symbol and item["side"] == "sell"
        ]
        for buy, sell in zip(buys, sells):
            completed.append(
                sell["notional"]
                - sell["total_fee"]
                - buy["notional"]
                - buy["total_fee"]
            )
        if not buys:
            completed.extend(
                sell["notional"]
                - sell["total_fee"]
                - sell["quantity"] * sell["starting_cost"]
                for sell in sells
                if "starting_cost" in sell
            )
    trade_count = len(completed)
    if trade_count == 0:
        reason_codes.append("no_completed_trades")
    total_return = ending_equity / starting_equity - 1
    turnover = (
        sum(float(item["notional"]) for item in executions)
        / starting_equity
    )
    benchmark_return = None
    relative = None
    benchmark_dates: list[str] = []
    if benchmark_frame is not None and active_curve:
        equity_by_date = {
            pd.Timestamp(item["time"]).normalize(): float(item["equity"])
            for item in active_curve
        }
        benchmark_by_date = {
            row["time"].normalize(): float(row["close"])
            for _, row in benchmark_frame.iterrows()
            if row["time"].normalize() in equity_by_date
        }
        common = sorted(set(equity_by_date).intersection(benchmark_by_date))
        if len(common) >= config.min_samples:
            benchmark_dates = [item.date().isoformat() for item in common]
            benchmark_return = (
                benchmark_by_date[common[-1]]
                / benchmark_by_date[common[0]]
                - 1
            )
            portfolio_interval_return = (
                equity_by_date[common[-1]]
                / equity_by_date[common[0]]
                - 1
            )
            relative = portfolio_interval_return - benchmark_return
        else:
            reason_codes.append("insufficient_benchmark_overlap")
    sample_count = len(active_curve)
    hit_rate = (
        0.0
        if not completed
        else sum(value > 0 for value in completed) / len(completed)
    )
    checks = (
        (sample_count >= config.min_samples, "insufficient_samples"),
        (trade_count >= config.min_trades, "insufficient_trades"),
        (hit_rate >= config.min_hit_rate, "low_hit_rate"),
        (sharpe >= config.min_sharpe, "low_sharpe"),
        (max_drawdown <= config.max_drawdown, "drawdown_exceeded"),
    )
    reason_codes.extend(code for passed, code in checks if not passed)
    return {
        "sample_count": sample_count,
        "trade_count": trade_count,
        "hit_rate": hit_rate,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "turnover": turnover,
        "benchmark_return": benchmark_return,
        "relative_benchmark_return": relative,
        "benchmark_dates": benchmark_dates,
        "ending_equity": ending_equity,
        "ending_positions": dict(position_quantities),
        "starting_equity": starting_equity,
        "equity_curve": equity_curve,
        "executions": executions,
        "audit": audit,
        "synthetic_sell_entries": synthetic_entries,
        "strategy_template": audit.get("strategy_template"),
        "account_source": account.source if account is not None else "test_config",
        "account_version": account.version if account is not None else None,
        "current_order_cash_required": current_order_cash_required,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


def _validate_akquant(
    raw: Mapping[str, Any],
    expected: Mapping[str, Any],
    config: BacktestSettings,
) -> dict[str, Any]:
    if raw.get("version") != AKQUANT_VERSION:
        raise ValueError("AKQuant version must be exactly 0.3.7")
    metrics = raw.get("metrics")
    required = {"total_return", "max_drawdown", "sharpe", "turnover"}
    if not isinstance(metrics, Mapping) or not required <= set(metrics):
        raise ValueError("AKQuant critical metrics are incomplete")
    for key in required:
        if metrics[key] is None or not math.isfinite(float(metrics[key])):
            raise ValueError(f"AKQuant metric {key} is invalid")
    executions = raw.get("executions")
    if not isinstance(executions, list) or not executions:
        raise ValueError("AKQuant filled execution details are missing")
    if int(raw.get("trade_count") or 0) <= 0:
        raise ValueError("AKQuant produced no completed trades")
    if int(raw["trade_count"]) != int(expected["trade_count"]):
        raise ValueError("AKQuant trade count mismatch")
    expected_fills = [
        (
            item["symbol"],
            item["side"],
            int(item["quantity"]),
            item["trade_date"],
        )
        for item in expected["executions"]
    ]
    actual_fills = [
        (
            str(item["symbol"]),
            str(item["side"]).lower(),
            int(float(item["quantity"])),
            str(item["trade_date"]),
        )
        for item in executions
    ]
    if actual_fills != expected_fills:
        raise ValueError("AKQuant execution details mismatch")
    for actual, planned in zip(executions, expected["executions"]):
        actual_date = pd.Timestamp(actual["timestamp"]).date()
        signal_date = pd.Timestamp(planned["signal_time"]).date()
        submit_time = pd.Timestamp(actual["submit_at"])
        planned_submit = pd.Timestamp(planned["submit_at"])
        if submit_time.date() != planned_submit.date():
            raise ValueError("AKQuant submit date mismatch")
        if submit_time.date() < signal_date:
            raise ValueError("AKQuant order predates template signal")
        if pd.Timestamp(actual["timestamp"]) <= submit_time:
            raise ValueError("AKQuant fill is not after order submission")
        if actual_date <= signal_date:
            raise ValueError("AKQuant execution is not after signal time")
        if (
            expected["audit"]["time_mode"] == "live_proposal"
            and actual_date
            < pd.Timestamp(planned["proposal_created_at"]).date()
        ):
            raise ValueError("AKQuant execution predates live proposal")
        if abs(float(actual["price"]) - float(planned["price"])) > 1e-6:
            raise ValueError("AKQuant execution price mismatch")
    tolerances = {
        "total_return": config.akquant_return_tolerance,
        "max_drawdown": config.akquant_drawdown_tolerance,
        "sharpe": config.akquant_sharpe_tolerance,
        "turnover": config.akquant_turnover_tolerance,
    }
    for key, tolerance in tolerances.items():
        if abs(float(metrics[key]) - float(expected[key])) > tolerance:
            raise ValueError(
                f"AKQuant {key} mismatch: "
                f"observed={float(metrics[key])}, "
                f"expected={float(expected[key])}, "
                f"tolerance={tolerance}"
            )
    return {"status": "ok", **dict(raw)}


def run_portfolio_backtest(
    *,
    proposals: Sequence[TradeProposal],
    histories: Mapping[str, pd.DataFrame],
    benchmark: pd.DataFrame | None,
    positions: Mapping[
        str, ExistingPosition | Mapping[str, Any]
    ] | None = None,
    account: AccountState | None = None,
    template: StrategyTemplate | None = None,
    as_of: datetime,
    config: BacktestSettings,
    akquant_check: Callable[..., Mapping[str, Any]] | None = None,
) -> BacktestVerdict:
    expected = _simulate(
        proposals=proposals,
        histories=histories,
        benchmark=benchmark,
        positions=positions,
        account=account,
        template=template,
        as_of=as_of,
        config=config,
    )
    reasons = list(expected["reason_codes"])
    try:
        if akquant_check is None:
            raise ValueError("AKQuant validator was not executed")
        raw = akquant_check(
            histories=histories,
            proposals=tuple(proposals),
            as_of=as_of,
            config=config,
            expected=expected,
        )
        akquant = _validate_akquant(raw, expected, config)
    except Exception as exc:
        akquant = {
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
        }
        reasons.append("akquant_validation_failed")
    reasons = list(dict.fromkeys(reasons))
    metrics = dict(expected)
    metrics["reason_codes"] = reasons
    metrics["akquant"] = akquant
    approved = not reasons
    first = tuple(proposals)[0]
    return BacktestVerdict(
        user_id=first.user_id,
        run_id=first.run_id,
        passed=approved,
        score=max(0.0, min(1.0, 1 - float(expected["max_drawdown"]))),
        metrics=metrics,
        summary=(
            (
                "策略模板历史验证支持当前交易提案"
                if template is not None
                else "组合回测通过"
            )
            if approved
            else (
                "策略模板验证拒绝："
                if template is not None
                else "组合回测拒绝："
            )
            + ", ".join(reasons)
        ),
        proposal_hash=proposal_semantics_hash(tuple(proposals)),
        created_at=_as_utc(as_of),
    )


def _process_entry(
    connection: Connection,
    function: Callable[..., Any],
    args: tuple[Any, ...],
) -> None:
    try:
        connection.send(("ok", function(*args)))
    except BaseException as exc:
        connection.send(("error", type(exc).__name__, str(exc)))
    finally:
        connection.close()


async def run_sync_in_killable_process(
    function: Callable[..., Any],
    *args: Any,
) -> Any:
    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_process_entry,
        args=(child, function, args),
    )
    process.start()
    child.close()
    try:
        while not parent.poll():
            if not process.is_alive():
                raise RuntimeError("worker exited without a result")
            await asyncio.sleep(0.01)
        message = parent.recv()
        process.join(timeout=0.2)
        if message[0] == "error":
            raise RuntimeError(f"{message[1]}: {message[2]}")
        return message[1]
    except asyncio.CancelledError:
        if process.is_alive():
            process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
        raise
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)


def _akquant_payload(
    histories: Mapping[str, pd.DataFrame],
    proposals: Sequence[TradeProposal],
    as_of: datetime,
    config: BacktestSettings,
    expected: Mapping[str, Any] | None = None,
    positions: Mapping[
        str, ExistingPosition | Mapping[str, Any]
    ] | None = None,
    account: AccountState | None = None,
    template: StrategyTemplate | None = None,
) -> dict[str, Any]:
    effective_positions = {} if template is not None else (positions or {})
    prepared = {
        symbol: _prepare_frame(frame, as_of=_as_utc(as_of), label=symbol)
        for symbol, frame in histories.items()
    }
    simulation = (
        dict(expected)
        if expected is not None
        else _simulate(
            proposals=proposals,
            histories=prepared,
            benchmark=None,
            positions=positions,
            account=account,
            template=template,
            as_of=as_of,
            config=config,
        )
    )
    rows = []
    for symbol, frame in prepared.items():
        renamed = frame.rename(columns={"time": "datetime"}).copy()
        renamed["symbol"] = symbol
        rows.extend(renamed.to_dict(orient="records"))
    for symbol, raw_position in effective_positions.items():
        position = ExistingPosition.model_validate(raw_position)
        acquired_day = pd.Timestamp(position.acquired_at).normalize()
        symbol_days = {
            pd.Timestamp(row["datetime"]).normalize()
            for row in rows
            if row["symbol"] == symbol
        }
        if acquired_day not in symbol_days:
            rows.append(
                {
                    "datetime": acquired_day,
                    "symbol": symbol,
                    "open": position.cost,
                    "high": position.cost,
                    "low": position.cost,
                    "close": position.cost,
                    "volume": max(position.quantity, 1),
                }
            )
    rows.sort(key=lambda row: (pd.Timestamp(row["datetime"]), row["symbol"]))
    return {
        "rows": rows,
        "symbols": list(prepared),
        "plans": simulation["executions"],
        "expected": simulation,
        "initial_cash": (
            (
                config.initial_cash
                if template is not None
                else account.cash
                if account is not None
                else config.initial_cash
            )
            + sum(
                ExistingPosition.model_validate(value).cost
                * ExistingPosition.model_validate(value).quantity
                for value in effective_positions.values()
            )
        ),
        "turnover_denominator": simulation["starting_equity"],
        "positions": {
            symbol: ExistingPosition.model_validate(value).model_dump(
                mode="json"
            )
            for symbol, value in effective_positions.items()
        },
        "slippage_bps": config.slippage_bps,
        "lot_size": config.lot_size,
        "end_time": _as_utc(as_of).isoformat(),
    }


def _akquant_validate_worker(payload: dict[str, Any]) -> dict[str, Any]:
    import akquant as aq

    if getattr(aq, "__version__", None) != AKQUANT_VERSION:
        raise RuntimeError("unexpected AKQuant version")
    frame = pd.DataFrame(payload["rows"])
    plans = list(payload["plans"])
    submitted: set[tuple[str, ...]] = set()
    sell_events = [
        plan
        for plan in plans
        if plan["side"] == "sell" and "starting_cost" in plan
    ]

    class PlanStrategy(aq.Strategy):
        def on_bar(self, bar):  # type: ignore[no-untyped-def]
            symbol = str(bar.symbol)
            day = pd.Timestamp(
                int(bar.timestamp), unit="ns", tz="UTC"
            ).date().isoformat()
            for plan in sell_events:
                if plan["symbol"] != symbol:
                    continue
                signal_key = str(plan["signal_key"])
                synthetic_key = (symbol, "synthetic", signal_key)
                entry_day = pd.Timestamp(
                    plan["synthetic_entry_time"]
                ).date().isoformat()
                if day != entry_day or synthetic_key in submitted:
                    continue
                self.buy(
                    symbol,
                    float(plan["quantity"]),
                    tag="synthetic_initial_position",
                    fill_policy={
                        "price_basis": "close",
                        "bar_offset": 0,
                        "temporal": "same_cycle",
                    },
                    slippage={"type": "fixed", "value": 0},
                    commission={"type": "fixed", "value": 0},
                )
                submitted.add(synthetic_key)
            for plan in plans:
                if plan["symbol"] != symbol:
                    continue
                if (
                    plan["side"] == "sell"
                    and "starting_cost" in plan
                ):
                    continue
                key = (
                    symbol,
                    plan["side"],
                    str(plan["signal_time"]),
                )
                signal_day = pd.Timestamp(plan["signal_time"]).date().isoformat()
                if day != signal_day or key in submitted:
                    continue
                method = self.buy if plan["side"] == "buy" else self.sell
                method(
                    symbol,
                    float(plan["quantity"]),
                    price=(
                        plan.get("limit_price")
                        if plan.get("order_type")
                        in {"limit", "stop_limit"}
                        else None
                    ),
                    trigger_price=(
                        plan.get("stop_price")
                        if plan.get("order_type") == "stop_limit"
                        else None
                    ),
                    fill_policy={
                        "price_basis": "open",
                        "bar_offset": 1,
                        "temporal": "next_event",
                    },
                    commission={
                        "type": "fixed",
                        "value": float(plan["total_fee"]),
                    },
                )
                submitted.add(key)

        def on_pre_open(self, event):  # type: ignore[no-untyped-def]
            trading_date = event.get("trading_date")
            day = (
                trading_date.isoformat()
                if hasattr(trading_date, "isoformat")
                else str(trading_date)
            )
            for plan in sell_events:
                symbol = str(plan["symbol"])
                key = (symbol, "sell", str(plan["signal_key"]))
                if day != plan["trade_date"] or key in submitted:
                    continue
                self.sell(
                    symbol,
                    float(plan["quantity"]),
                    tag="existing_position_sell",
                    price=(
                        plan.get("limit_price")
                        if plan.get("order_type")
                        in {"limit", "stop_limit"}
                        else None
                    ),
                    trigger_price=(
                        plan.get("stop_price")
                        if plan.get("order_type") == "stop_limit"
                        else None
                    ),
                    commission={
                        "type": "fixed",
                        "value": float(plan["total_fee"]),
                    },
                )
                submitted.add(key)

    result = aq.run_backtest(
        data=frame,
        strategy=PlanStrategy,
        symbols=payload["symbols"],
        initial_cash=payload["initial_cash"],
        commission_rate=0,
        stamp_tax_rate=0,
        min_commission=0,
        slippage={
            "type": "percent",
            "value": payload["slippage_bps"] / 10_000,
        },
        t_plus_one=True,
        lot_size=payload["lot_size"],
        show_progress=False,
        end_time=payload["end_time"],
    )
    metrics_frame = result.metrics_df
    values = (
        metrics_frame["value"].to_dict()
        if "value" in metrics_frame.columns
        else metrics_frame.iloc[:, 0].to_dict()
    )
    for key in ("total_return_pct", "max_drawdown_pct", "sharpe_ratio"):
        if key not in values or values[key] is None:
            raise RuntimeError(f"AKQuant missing metric {key}")
    filled = result.executions_df.copy()
    if filled.empty or "timestamp" not in filled:
        raise RuntimeError("AKQuant executions_df is incomplete")
    filled = filled.sort_values("timestamp").reset_index(drop=True)
    raw_execution_count = len(filled)
    orders_frame = result.orders_df.copy()
    order_submit_times = {}
    if (
        not orders_frame.empty
        and "id" in orders_frame
        and "created_at" in orders_frame
    ):
        order_submit_times = {
            str(row["id"]): pd.Timestamp(row["created_at"])
            for _, row in orders_frame.iterrows()
        }
    for symbol in {str(plan["symbol"]) for plan in sell_events}:
        synthetic = filled.index[
            (filled["symbol"].astype(str) == symbol)
            & (filled["side"].astype(str).str.lower().str.contains("buy"))
        ]
        expected_synthetic_count = sum(
            1 for plan in sell_events if plan["symbol"] == symbol
        )
        if len(synthetic):
            filled = filled.drop(
                list(synthetic[:expected_synthetic_count])
            )
    filled = filled.reset_index(drop=True)
    execution_rows = []
    expected_plans = list(payload["plans"])
    for _, row in filled.iterrows():
        timestamp = pd.Timestamp(row["timestamp"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        timestamp = timestamp.tz_convert("UTC")
        submitted_at = order_submit_times.get(str(row.get("order_id")))
        if submitted_at is None:
            raise RuntimeError("AKQuant order submit timestamp is missing")
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.tz_localize("UTC")
        submitted_at = submitted_at.tz_convert("UTC")
        execution_rows.append(
            {
                "symbol": str(row["symbol"]),
                "side": str(row["side"]).lower(),
                "quantity": float(row["quantity"]),
                "price": float(row["price"]),
                "timestamp": timestamp.isoformat(),
                "submit_at": submitted_at.isoformat(),
                "trade_date": timestamp.date().isoformat(),
            }
        )
    turnover = (
        float(
            (
                filled["quantity"].astype(float)
                * filled["price"].astype(float)
            ).sum()
        )
        / payload["turnover_denominator"]
        if not filled.empty
        else 0.0
    )
    daily_curve = result.equity_curve_daily
    start_day = min(
        (
            pd.Timestamp(item["signal_time"]).date().isoformat()
            if "starting_cost" in item
            else item["trade_date"]
        )
        for item in expected_plans
    )
    end_day = max(item["trade_date"] for item in expected_plans)
    aligned_values = np.asarray(
        [
            float(value)
            for timestamp, value in daily_curve.items()
            if start_day
            <= pd.Timestamp(timestamp).date().isoformat()
            <= end_day
        ],
        dtype=float,
    )
    if len(aligned_values) < 2:
        raise RuntimeError("AKQuant aligned equity curve is incomplete")
    aligned_returns = pd.Series(aligned_values).pct_change().dropna().to_numpy()
    aligned_sharpe = (
        0.0
        if len(aligned_returns) < 2
        or float(np.std(aligned_returns, ddof=1)) == 0
        else float(
            np.mean(aligned_returns)
            / np.std(aligned_returns, ddof=1)
            * np.sqrt(252)
        )
    )
    aligned_peaks = np.maximum.accumulate(aligned_values)
    return {
        "version": getattr(aq, "__version__", "unknown"),
        "metrics": {
            "total_return": (
                float(aligned_values[-1])
                / float(
                    aligned_values[0]
                    if sell_events
                    else payload["initial_cash"]
                )
                - 1
            ),
            "max_drawdown": float(
                np.max(
                    (aligned_peaks - aligned_values)
                    / aligned_peaks
                )
            ),
            "sharpe": aligned_sharpe,
            "turnover": turnover,
        },
        "engine_metrics": {
            "total_return_pct": float(values["total_return_pct"]),
            "max_drawdown_pct": float(values["max_drawdown_pct"]),
            "sharpe_ratio": float(values["sharpe_ratio"]),
        },
        "executions": execution_rows,
        "trade_count": int(len(result.trades_df)),
        "mode": "single_portfolio_per_order_fee",
        "engine_audit": {
            "raw_execution_count": raw_execution_count,
            "orders": result.orders_df[
                [
                    column
                    for column in (
                        "symbol",
                        "side",
                        "status",
                        "quantity",
                        "filled_quantity",
                        "reject_reason",
                        "error",
                        "message",
                    )
                    if column in result.orders_df
                ]
            ].to_dict(orient="records"),
        },
    }


def create_backtest_provider(
    *,
    history_provider: Callable[[str, datetime], Awaitable[pd.DataFrame]],
    benchmark_provider: Callable[[datetime], Awaitable[pd.DataFrame | None]],
    config: BacktestSettings,
    metadata_provider: Callable[
        [str, tuple[str, ...], datetime],
        Awaitable[SecurityMetadata],
    ] | None = None,
    positions_provider: Callable[
        [Any],
        Awaitable[
            Mapping[str, ExistingPosition | Mapping[str, Any]]
        ],
    ] | None = None,
    strategy_template_provider: Callable[
        [
            tuple[TradeProposal, ...],
            Mapping[str, pd.DataFrame],
            pd.DataFrame,
            datetime,
        ],
        Awaitable[StrategyTemplate],
    ] | None = None,
    account_provider: Callable[
        [Any], Awaitable[AccountState]
    ] | None = None,
    daily_limit_provider: Callable[
        [tuple[str, ...], tuple[str, ...]],
        Awaitable[tuple[DailyLimitStatus, ...]],
    ] | None = None,
    akquant_validator: Callable[..., Awaitable[Mapping[str, Any]]] | None = None,
):
    async def provider(
        proposals: TradeProposal | Sequence[TradeProposal],
        context: Any,
    ) -> BacktestVerdict:
        items = (
            (proposals,)
            if isinstance(proposals, TradeProposal)
            else tuple(proposals)
        )
        as_of = _as_utc(context.snapshot.as_of)
        fetched = await asyncio.gather(
            *(history_provider(item.symbol, as_of) for item in items),
            benchmark_provider(as_of),
        )
        histories = {
            item.symbol: fetched[index]
            for index, item in enumerate(items)
        }
        benchmark = fetched[-1]
        if metadata_provider is not None:
            metadata = await asyncio.gather(
                *(
                    metadata_provider(
                        item.symbol,
                        tuple(
                            pd.to_datetime(
                                histories[item.symbol]["time"], utc=True
                            ).dt.date.astype(str)
                        ),
                        as_of,
                    )
                    for item in items
                )
            )
            histories = {
                item.symbol: _attach_metadata(
                    histories[item.symbol],
                    metadata[index],
                )
                for index, item in enumerate(items)
            }
        account = (
            await account_provider(context)
            if account_provider is not None
            else None
        )
        positions = (
            account.positions
            if account is not None
            else await positions_provider(context)
            if positions_provider is not None
            else {}
        )
        template = (
            await strategy_template_provider(
                items,
                histories,
                benchmark,
                as_of,
            )
            if strategy_template_provider is not None
            else None
        )
        if strategy_template_provider is not None and template is None:
            raise ValueError("strategy template validation is missing")
        if account_provider is not None and account is None:
            raise ValueError("frozen snapshot account evidence missing")
        if daily_limit_provider is not None:
            required_pairs: set[tuple[str, str]] = set()
            etf_symbols = {
                item.symbol
                for item in items
                if _is_etf(item.symbol, histories[item.symbol])
            }
            for item in items:
                signals = (
                    tuple(template.signals.get(item.symbol, ()))
                    if template is not None
                    else (
                        item.strategy_template_as_of or item.created_at,
                    )
                )
                times = pd.to_datetime(
                    histories[item.symbol]["time"], utc=True
                )
                for signal in signals:
                    future = times.loc[times > pd.Timestamp(signal)]
                    for timestamp in future.iloc[:2]:
                        if item.symbol not in etf_symbols:
                            required_pairs.add(
                                (item.symbol, timestamp.date().isoformat())
                            )
            statuses = (
                await daily_limit_provider(
                    tuple(sorted({symbol for symbol, _ in required_pairs})),
                    tuple(sorted({date for _, date in required_pairs})),
                )
                if required_pairs
                else ()
            )
            by_key = {
                (item.symbol, item.trade_date): item
                for item in statuses
            }
            missing = required_pairs.difference(by_key)
            if missing:
                raise DataValidationError(
                    f"exact historical limit status missing: {sorted(missing)}"
                )
            enriched = {}
            for symbol, frame in histories.items():
                work = frame.copy()
                work["exact_limit_status"] = pd.NA
                work["exact_limit_locked"] = pd.NA
                work["exact_limit_source"] = pd.NA
                work["exact_limit_data_as_of"] = pd.NA
                work["limit_up_price"] = np.nan
                work["limit_down_price"] = np.nan
                dates = pd.to_datetime(work["time"], utc=True).dt.date.astype(str)
                for index, date in enumerate(dates):
                    if symbol in etf_symbols:
                        work.loc[index, "exact_limit_status"] = (
                            "etf_ohlcv_model"
                        )
                        work.loc[index, "exact_limit_locked"] = False
                        work.loc[index, "exact_limit_source"] = (
                            "ohlcv_locked_board"
                        )
                        work.loc[index, "exact_limit_data_as_of"] = date
                        continue
                    status = by_key.get((symbol, date))
                    if status is None:
                        continue
                    work.loc[index, "exact_limit_status"] = status.status
                    work.loc[index, "exact_limit_locked"] = status.locked
                    work.loc[index, "exact_limit_source"] = status.source
                    work.loc[index, "exact_limit_data_as_of"] = (
                        status.data_as_of.isoformat()
                        if status.data_as_of is not None
                        else date
                    )
                    work.loc[index, "limit_up_price"] = status.limit_up_price
                    work.loc[index, "limit_down_price"] = status.limit_down_price
                enriched[symbol] = work
            histories = enriched
        expected = _simulate(
            proposals=items,
            histories=histories,
            benchmark=benchmark,
            positions=positions,
            account=account,
            template=template,
            as_of=as_of,
            config=config,
        )
        validation_error: Exception | None = None
        try:
            if akquant_validator is None:
                validation = await run_sync_in_killable_process(
                    _akquant_validate_worker,
                    _akquant_payload(
                        histories,
                        items,
                        as_of,
                        config,
                        expected,
                        positions,
                        account,
                        template,
                    ),
                )
            else:
                validation = await akquant_validator(
                    histories=histories,
                    proposals=items,
                    as_of=as_of,
                    config=config,
                    expected=expected,
                )
        except Exception as exc:
            validation = {}
            validation_error = exc

        def checked(**_kwargs: Any) -> Mapping[str, Any]:
            if validation_error is not None:
                raise RuntimeError(str(validation_error)) from validation_error
            return validation

        return run_portfolio_backtest(
            proposals=items,
            histories=histories,
            benchmark=benchmark,
            positions=positions,
            account=account,
            template=template,
            as_of=as_of,
            config=config,
            akquant_check=checked,
        )

    return provider
