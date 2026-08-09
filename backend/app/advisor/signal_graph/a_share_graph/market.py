import math
import re

from .errors import InvalidMarketDataError, InvalidTickerError
from .models import Action, MarketState, TradingCostConfig

_TICKER = re.compile(r"^(\d{6})(?:\.(SH|SZ|BJ))?$", re.IGNORECASE)


def normalize_ticker(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidTickerError(str(value))
    match = _TICKER.fullmatch(value.strip())
    if not match:
        raise InvalidTickerError(value)
    code, suffix = match.groups()
    if suffix is None:
        if code.startswith(("5", "6", "9")):
            suffix = "SH"
        elif code.startswith(("4", "8")):
            suffix = "BJ"
        else:
            suffix = "SZ"
    return f"{code}.{suffix.upper()}"


def assess_tradability(
    state: MarketState,
    requested: Action,
    *,
    allow_st: bool = False,
    min_listing_days: int = 60,
) -> str | None:
    if state.is_suspended:
        return "suspended"
    if state.is_st and not allow_st:
        return "st"
    if state.trading_days_since_listing < min_listing_days:
        return "new_listing"
    if requested is Action.BUY and state.is_limit_up:
        return "limit_up"
    if requested is Action.SELL and state.is_limit_down:
        return "limit_down"
    return None


def calculate_excess_return(
    *,
    stock_entry: float,
    stock_exit: float,
    benchmark_entry: float,
    benchmark_exit: float,
    action: Action,
    costs: TradingCostConfig,
) -> float:
    prices = (stock_entry, stock_exit, benchmark_entry, benchmark_exit)
    if any(not math.isfinite(value) or value <= 0 for value in prices):
        raise InvalidMarketDataError("prices must be finite and positive")

    rates = (
        costs.commission_rate,
        costs.stamp_duty_rate,
        costs.slippage_rate,
    )
    if any(not math.isfinite(value) or value < 0 for value in rates):
        raise InvalidMarketDataError("cost rates must be finite and non-negative")

    stock_return = stock_exit / stock_entry - 1.0
    benchmark_return = benchmark_exit / benchmark_entry - 1.0
    raw_excess = stock_return - benchmark_return
    directional = -raw_excess if action is Action.SELL else raw_excess
    round_trip_cost = (
        2 * costs.commission_rate
        + costs.stamp_duty_rate
        + 2 * costs.slippage_rate
    )
    applied_cost = 0.0 if action is Action.HOLD else round_trip_cost
    return directional - applied_cost
