"""BaoStock historical K-line adapter (matches /api/{source}/kline shape)."""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any

import pandas as pd

from .baostock_common import result_to_df, session

VALID_RANGES = ("realtime", "5d", "daily", "weekly", "monthly")

# 日线可含 preclose；周/月线不支持 preclose
_K_FIELDS_DAY = (
    "date,code,open,high,low,close,preclose,volume,amount,pctChg"
)
_K_FIELDS_WEEK_MONTH = "date,code,open,high,low,close,volume,amount,pctChg"
_K_FIELDS_MIN = "date,time,code,open,high,low,close,volume,amount"


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    for prefix in ("SH", "SZ", "BJ", "SH.", "SZ.", "BJ."):
        if s.startswith(prefix) and len(s) > len(prefix):
            s = s[len(prefix) :]
            break
    s = s.replace(".", "")
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) != 6:
        raise ValueError("股票代码需为 6 位数字，例如 600519")
    return digits


def to_bs_code(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    if symbol.startswith(("5", "6", "9")):
        return f"sh.{symbol}"
    if symbol.startswith(("4", "8")):
        return f"bj.{symbol}"
    return f"sz.{symbol}"


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


def _f(v: Any) -> float:
    return float(v)


def _parse_minute_time(date: str, time_raw: str) -> str:
    """BaoStock time like 20260717093500000 → 2026-07-17 09:35"""
    t = "".join(ch for ch in str(time_raw) if ch.isdigit())
    if len(t) >= 12:
        hh, mm = t[8:10], t[10:12]
        return f"{date} {hh}:{mm}"
    return f"{date} 00:00"


def _fetch_name(bs: Any, code: str, fallback: str) -> str:
    try:
        rs = bs.query_stock_basic(code=code)
        df = result_to_df(rs)
        if not df.empty and "code_name" in df.columns:
            name = str(df.iloc[0]["code_name"]).strip()
            if name:
                return name
    except Exception:
        pass
    return fallback


def _query_k(
    bs: Any,
    code: str,
    fields: str,
    start: str,
    end: str,
    frequency: str,
    adjustflag: str = "2",
) -> pd.DataFrame:
    rs = bs.query_history_k_data_plus(
        code,
        fields,
        start_date=start,
        end_date=end,
        frequency=frequency,
        adjustflag=adjustflag,
    )
    return result_to_df(rs)


def _bars_from_day_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            vol = _f(row["volume"]) if "volume" in row and str(row["volume"]) != "" else None
            bars.append(
                _bar(
                    str(row["date"]),
                    _f(row["open"]),
                    _f(row["high"]),
                    _f(row["low"]),
                    _f(row["close"]),
                    vol,
                )
            )
        except (TypeError, ValueError):
            continue
    return bars


def _bars_from_min_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    current_day: str | None = None
    cumulative_amount = 0.0
    cumulative_volume = 0.0
    last_avg: float | None = None
    for _, row in df.iterrows():
        try:
            date = str(row["date"])
            t = _parse_minute_time(date, row.get("time", ""))
            vol = _f(row["volume"]) if "volume" in row and str(row["volume"]) != "" else None
            amount = (
                _f(row["amount"])
                if "amount" in row and str(row["amount"]) != ""
                else None
            )
            if date != current_day:
                current_day = date
                cumulative_amount = 0.0
                cumulative_volume = 0.0
                last_avg = None
            avg_price: float | None = None
            if (
                vol is not None
                and amount is not None
                and math.isfinite(vol)
                and math.isfinite(amount)
                and vol > 0
                and amount > 0
            ):
                cumulative_volume += vol
                cumulative_amount += amount
                last_avg = cumulative_amount / cumulative_volume
                avg_price = last_avg
            elif last_avg is not None:
                avg_price = last_avg
            bars.append(
                _bar(
                    t,
                    _f(row["open"]),
                    _f(row["high"]),
                    _f(row["low"]),
                    _f(row["close"]),
                    vol,
                    avg_price=avg_price,
                )
            )
        except (TypeError, ValueError):
            continue
    return bars


def get_kline(symbol: str, range_: str) -> dict[str, Any]:
    if range_ not in VALID_RANGES:
        raise ValueError(f"range 无效，可选: {', '.join(VALID_RANGES)}")

    digits = normalize_symbol(symbol)
    code = to_bs_code(digits)
    today = datetime.now().date()
    end = today.strftime("%Y-%m-%d")

    chart_type = "candle"
    source = f"baostock.{range_}.qfq"
    bars: list[dict[str, Any]] = []
    name = digits

    with session() as bs:
        name = _fetch_name(bs, code, digits)

        if range_ == "realtime":
            chart_type = "line"
            # 非交易日当日可能为空 → 回退最近若干天取最后交易日的 5 分钟线
            start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
            df = _query_k(bs, code, _K_FIELDS_MIN, start, end, "5", "2")
            all_bars = _bars_from_min_df(df)
            if all_bars:
                last_day = all_bars[-1]["time"][:10]
                bars = [b for b in all_bars if b["time"][:10] == last_day]
            source = "baostock.5min.intraday.qfq"
        elif range_ == "5d":
            start = (today - timedelta(days=12)).strftime("%Y-%m-%d")
            df = _query_k(bs, code, _K_FIELDS_MIN, start, end, "5", "2")
            bars = _bars_from_min_df(df)
            # keep last 5 trading sessions by date
            if bars:
                dates = sorted({b["time"][:10] for b in bars})
                keep = set(dates[-5:])
                bars = [b for b in bars if b["time"][:10] in keep]
            source = "baostock.5min.5d.qfq"
        elif range_ == "daily":
            start = (today - timedelta(days=520)).strftime("%Y-%m-%d")
            df = _query_k(bs, code, _K_FIELDS_DAY, start, end, "d", "2")
            bars = _bars_from_day_df(df)[-320:]
            source = "baostock.day.qfq"
        elif range_ == "weekly":
            start = (today - timedelta(days=320 * 7)).strftime("%Y-%m-%d")
            df = _query_k(bs, code, _K_FIELDS_WEEK_MONTH, start, end, "w", "2")
            bars = _bars_from_day_df(df)[-320:]
            source = "baostock.week.qfq"
        else:  # monthly
            start = (today - timedelta(days=320 * 31)).strftime("%Y-%m-%d")
            df = _query_k(bs, code, _K_FIELDS_WEEK_MONTH, start, end, "m", "2")
            bars = _bars_from_day_df(df)[-320:]
            source = "baostock.month.qfq"

    if not bars:
        raise RuntimeError("未获取到 K 线数据，请稍后重试或检查代码是否正确")

    pre_close: float | None = None
    if len(bars) >= 2:
        pre_close = float(bars[-2]["close"])

    last = bars[-1]
    return {
        "symbol": digits,
        "name": name,
        "range": range_,
        "chart_type": chart_type,
        "pre_close": pre_close,
        "source": source,
        "count": len(bars),
        "last": last,
        "bars": bars,
    }
