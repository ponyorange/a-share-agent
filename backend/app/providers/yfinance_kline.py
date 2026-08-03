"""yfinance K-line adapter (matches /api/{source}/kline shape)."""

from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

VALID_RANGES = ("realtime", "5d", "daily", "weekly", "monthly")

# range → (period, interval, chart_type)
_RANGE_SPEC: dict[str, tuple[str, str, str]] = {
    "realtime": ("1d", "1m", "line"),
    "5d": ("5d", "5m", "candle"),
    "daily": ("2y", "1d", "candle"),
    "weekly": ("10y", "1wk", "candle"),
    "monthly": ("max", "1mo", "candle"),
}

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^_=/-]{1,32}$")


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
    # flatten possible MultiIndex columns from download quirks
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
        avg = None
        if with_clock and vol is not None and vol > 0:
            # approximate VWAP-ish running avg not available; skip
            avg = None
        bars.append(_bar(_fmt_time(idx, with_clock=with_clock), o, h, l, c, vol, avg))
    return bars


def _resolve_name(ticker: Any, symbol: str) -> str:
    try:
        fi = getattr(ticker, "fast_info", None)
        if fi is not None:
            for key in ("shortName", "longName", "name"):
                try:
                    val = fi[key] if hasattr(fi, "__getitem__") else getattr(fi, key, None)
                except Exception:
                    val = getattr(fi, key, None) if hasattr(fi, key) else None
                if val:
                    return str(val)
    except Exception:
        pass
    try:
        info = getattr(ticker, "info", None) or {}
        if isinstance(info, dict):
            for key in ("shortName", "longName", "symbol"):
                val = info.get(key)
                if val:
                    return str(val)
    except Exception:
        pass
    return symbol


def get_kline(symbol: str, range_: str) -> dict[str, Any]:
    import yfinance as yf

    symbol = normalize_symbol(symbol)
    if range_ not in VALID_RANGES:
        raise ValueError(f"range 无效，可选: {', '.join(VALID_RANGES)}")

    period, interval, chart_type = _RANGE_SPEC[range_]
    with_clock = range_ in ("realtime", "5d")

    ticker = yf.Ticker(symbol)
    try:
        df = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=False,
            actions=False,
        )
    except Exception as exc:
        msg = str(exc)
        if "Rate" in type(exc).__name__ or "Too Many Requests" in msg:
            raise RuntimeError("Yahoo Finance 限流，请稍后再试") from exc
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

    # 1m sometimes empty outside US hours → fall back to 5m for realtime
    if (df is None or df.empty) and range_ == "realtime":
        try:
            df = ticker.history(
                period="5d",
                interval="5m",
                auto_adjust=False,
                actions=False,
            )
            if df is not None and not df.empty:
                # keep last session only
                last_day = pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")
                df = df[[pd.Timestamp(i).strftime("%Y-%m-%d") == last_day for i in df.index]]
                interval = "5m"
        except Exception:
            pass

    bars = _bars_from_df(df, with_clock=with_clock)
    if range_ in ("daily", "weekly", "monthly") and len(bars) > 320:
        bars = bars[-320:]

    if not bars:
        raise RuntimeError("未获取到 K 线数据，请稍后重试或检查代码是否正确")

    name = _resolve_name(ticker, symbol)
    pre_close: float | None = None
    if len(bars) >= 2:
        pre_close = float(bars[-2]["close"])

    last = bars[-1]
    return {
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
