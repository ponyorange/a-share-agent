"""Assemble SignalContext / MarketState from advisor market data."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ...kline import normalize_symbol
from ..calendar_util import last_trading_day
from ..features import build_feature_result, fetch_daily_df, load_benchmark
from ..market_context import enrich_symbol_context, get_market_score
from ..regime import get_regime_for_gate
from .a_share_graph.market import normalize_ticker
from .a_share_graph.models import MarketState, SignalContext


def map_regime_to_graph(regime: dict[str, Any] | None) -> str:
    """Collapse advisor regime into bull / sideways / bear graph nodes."""
    payload = regime or {}
    trend = str(payload.get("trend_regime") or "").lower()
    sentiment = str(payload.get("sentiment_cycle") or "").lower()
    gate = str(payload.get("gate_level") or "").lower()
    if gate == "risk_off" or trend == "downtrend":
        return "bear"
    if trend == "uptrend" and sentiment in {"strengthen", "climax", "repair"}:
        return "bull"
    if trend == "uptrend":
        return "bull"
    return "sideways"


def _industry_slug(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return "unknown"
    return text.replace(" ", "_").replace("/", "_")[:48] or "unknown"


def infer_patterns(factors: dict[str, Any] | None) -> tuple[str, ...]:
    """Derive coarse pattern tags from existing feature factors."""
    f = factors or {}
    patterns: list[str] = []

    def _num(key: str) -> float | None:
        try:
            value = float(f.get(key))
        except (TypeError, ValueError):
            return None
        if value != value:  # NaN
            return None
        return value

    mom = _num("mom_20")
    if mom is not None:
        if mom > 0.03:
            patterns.append("momentum_up")
        elif mom < -0.03:
            patterns.append("momentum_down")

    vol_ratio = _num("vol_ratio")
    if vol_ratio is not None and vol_ratio >= 1.5:
        patterns.append("volume_breakout")

    ma_bias = _num("ma20_bias")
    if ma_bias is not None:
        if ma_bias > 0.02:
            patterns.append("ma_bullish")
        elif ma_bias < -0.02:
            patterns.append("ma_bearish")

    if not patterns:
        patterns.append("neutral")
    return tuple(patterns[:4])


def _price_on_or_before(df: pd.DataFrame, trade_date: str) -> float | None:
    if df is None or df.empty or "close" not in df.columns:
        return None
    work = df.copy()
    work["time"] = work["time"].astype(str).str.slice(0, 10)
    work = work[work["time"] <= trade_date[:10]]
    if work.empty:
        return None
    try:
        price = float(work.iloc[-1]["close"])
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


def _limit_flags(
    *,
    close: float | None,
    prev_close: float | None,
    day_chg_pct: float | None,
) -> tuple[bool, bool]:
    """Best-effort limit-up / limit-down flags from day change."""
    if day_chg_pct is None and close and prev_close and prev_close > 0:
        day_chg_pct = close / prev_close - 1.0
    if day_chg_pct is None:
        return False, False
    return day_chg_pct >= 0.095, day_chg_pct <= -0.095


def build_signal_inputs(
    symbol: str,
    *,
    trade_date: str | None = None,
    trade_tick: int,
    horizon_days: int = 5,
    owner: str = "default",
    regime: dict[str, Any] | None = None,
    industry_hint: str | None = None,
    patterns: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Load prices/features and return kwargs for SignalEngine.generate."""
    code = normalize_symbol(symbol)
    ticker = normalize_ticker(code)
    day = (trade_date or last_trading_day())[:10]

    name, df = fetch_daily_df(code)
    bench = load_benchmark(day)
    feat = build_feature_result(code, name, df, bench, as_of=day)
    if feat is None:
        raise ValueError(f"历史日线不足: {code}")

    entry_price = float(feat.close)
    bench_price = _price_on_or_before(bench, day)
    if bench_price is None:
        raise ValueError("基准价格不可用")

    regime_payload = (
        regime if regime is not None else get_regime_for_gate(allow_stale=True)
    )
    market_regime = map_regime_to_graph(regime_payload)

    industry = industry_hint
    if not industry:
        ctx = enrich_symbol_context(code, market=get_market_score(day))
        industry = (ctx.get("sector") or {}).get("industry")
    industry_id = _industry_slug(industry)

    resolved_patterns = (
        patterns if patterns is not None else infer_patterns(feat.factors)
    )
    is_limit_up, is_limit_down = _limit_flags(
        close=feat.close,
        prev_close=feat.prev_close,
        day_chg_pct=feat.day_chg_pct,
    )

    context = SignalContext(
        ticker=ticker,
        trade_date=day,
        trade_tick=int(trade_tick),
        market_regime=market_regime,
        industry=industry_id,
        patterns=resolved_patterns,
        horizon_days=int(horizon_days),
        owner=owner,
    )
    market_state = MarketState(
        ticker=ticker,
        is_suspended=False,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
        is_st="ST" in str(name or "").upper(),
        trading_days_since_listing=int(feat.bars_used),
    )
    return {
        "context": context,
        "market_state": market_state,
        "entry_price": entry_price,
        "benchmark_entry_price": bench_price,
        "symbol": code,
        "name": name or code,
        "as_of": feat.as_of,
        "industry": industry,
        "market_regime": market_regime,
        "patterns": list(resolved_patterns),
        "day_chg_pct": feat.day_chg_pct,
        "close": feat.close,
        "prev_close": feat.prev_close,
    }


def load_exit_prices(
    symbol: str,
    *,
    trade_date: str,
) -> tuple[float, float]:
    """Return (stock_exit, benchmark_exit) on or before trade_date."""
    code = normalize_symbol(symbol)
    _name, df = fetch_daily_df(code)
    stock = _price_on_or_before(df, trade_date)
    bench = _price_on_or_before(load_benchmark(trade_date), trade_date)
    if stock is None or bench is None:
        raise ValueError(f"退出价格不可用: {code} @ {trade_date}")
    return stock, bench
