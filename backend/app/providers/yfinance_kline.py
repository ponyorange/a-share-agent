"""yfinance K-line adapter (matches /api/{source}/kline shape)."""

from __future__ import annotations

import math
import re
import time
from typing import Any

import pandas as pd

VALID_RANGES = ("realtime", "5d", "daily", "weekly", "monthly")

# range → (period, interval, chart_type)
_RANGE_SPEC: dict[str, tuple[str, str, str]] = {
    "realtime": ("1d", "1m", "line"),
    "5d": ("5d", "5m", "candle"),
    "daily": ("1y", "1d", "candle"),
    "weekly": ("5y", "1wk", "candle"),
    "monthly": ("max", "1mo", "candle"),
}

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^_=/-]{1,32}$")

CACHE_TTL_SEC = 60.0
_cache: dict[str, Any] = {}


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        raise ValueError("股票代码不能为空，例如 AAPL")
    if not _SYMBOL_RE.fullmatch(s):
        raise ValueError("代码格式无效，例如 AAPL / MSFT / ^GSPC / 0700.HK")
    return s


def _bar(
    time: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None = None,
    avg_price: float | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "time": time,
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }
    if volume is not None and not pd.isna(volume):
        item["volume"] = float(volume)
    if (
        avg_price is not None
        and not pd.isna(avg_price)
        and math.isfinite(float(avg_price))
        and float(avg_price) > 0
    ):
        item["avg_price"] = float(avg_price)
    return item


def _fmt_time(idx: Any, *, with_clock: bool) -> str:
    ts = pd.Timestamp(idx)
    if with_clock:
        return ts.strftime("%Y-%m-%d %H:%M")
    return ts.strftime("%Y-%m-%d")


def _bars_from_df(df: pd.DataFrame, *, with_clock: bool) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[-1] if isinstance(c, tuple) else c for c in df.columns]

    bars: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        try:
            o = float(row["Open"])
            h = float(row["High"])
            l = float(row["Low"])
            c = float(row["Close"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(pd.isna(x) for x in (o, h, l, c)):
            continue
        vol = None
        if "Volume" in df.columns and not pd.isna(row.get("Volume")):
            try:
                vol = float(row["Volume"])
            except (TypeError, ValueError):
                vol = None
        bars.append(_bar(_fmt_time(idx, with_clock=with_clock), o, h, l, c, vol, None))
    return bars


def _is_rate_limit(exc: BaseException) -> bool:
    name = type(exc).__name__
    msg = str(exc)
    return "RateLimit" in name or "Too Many Requests" in msg or "rate limited" in msg.lower()


def _flatten_download(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        level0 = list(df.columns.get_level_values(0))
        if symbol in level0:
            return df[symbol].dropna(how="all")
        # single-ticker download sometimes uses Price as top level
        if "Open" in level0 or "Close" in level0:
            flat = df.copy()
            flat.columns = [c[-1] if isinstance(c, tuple) else c for c in flat.columns]
            return flat.dropna(how="all")
        # only one ticker group
        tickers = sorted(set(level0))
        if len(tickers) == 1:
            return df[tickers[0]].dropna(how="all")
        return pd.DataFrame()
    return df.dropna(how="all")


def _fetch_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Prefer download() to avoid extra Ticker.info/tz round-trips when possible."""
    import yfinance as yf

    try:
        raw = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
            group_by="ticker",
        )
    except Exception as exc:
        if _is_rate_limit(exc):
            raise RuntimeError("Yahoo Finance 限流，请稍后再试") from exc
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    df = _flatten_download(raw, symbol)
    if df is not None and not df.empty:
        return df

    # fallback: Ticker.history (some intervals behave better)
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
            actions=False,
        )
    except Exception as exc:
        if _is_rate_limit(exc):
            raise RuntimeError("Yahoo Finance 限流，请稍后再试") from exc
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    return hist if hist is not None else pd.DataFrame()


def get_kline(symbol: str, range_: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    if range_ not in VALID_RANGES:
        raise ValueError(f"range 无效，可选: {', '.join(VALID_RANGES)}")

    cache_key = f"{symbol}:{range_}"
    now = time.monotonic()
    hit = _cache.get(cache_key)
    if hit and (now - float(hit.get("ts") or 0.0)) < CACHE_TTL_SEC:
        return hit["payload"]

    period, interval, chart_type = _RANGE_SPEC[range_]
    with_clock = range_ in ("realtime", "5d")

    df = _fetch_history(symbol, period, interval)

    # 1m empty outside US hours → fall back to last-session 5m
    if (df is None or df.empty) and range_ == "realtime":
        try:
            df5 = _fetch_history(symbol, "5d", "5m")
            if df5 is not None and not df5.empty:
                last_day = pd.Timestamp(df5.index[-1]).strftime("%Y-%m-%d")
                df = df5[
                    [pd.Timestamp(i).strftime("%Y-%m-%d") == last_day for i in df5.index]
                ]
                interval = "5m"
        except RuntimeError:
            pass

    bars = _bars_from_df(df, with_clock=with_clock)
    if range_ in ("daily", "weekly", "monthly") and len(bars) > 320:
        bars = bars[-320:]

    if not bars:
        raise RuntimeError(
            "未获取到 K 线数据（Yahoo 可能限流或代码无效），请稍后再试"
        )

    # Avoid extra Yahoo calls for company name (fast_info/info worsen rate limits)
    name = symbol
    pre_close: float | None = None
    if len(bars) >= 2:
        pre_close = float(bars[-2]["close"])

    last = bars[-1]
    payload = {
        "symbol": symbol,
        "name": name,
        "range": range_,
        "chart_type": chart_type,
        "pre_close": pre_close,
        "source": f"yfinance.{interval}.{range_}",
        "count": len(bars),
        "last": last,
        "bars": bars,
    }
    _cache[cache_key] = {"ts": now, "payload": payload}
    return payload
