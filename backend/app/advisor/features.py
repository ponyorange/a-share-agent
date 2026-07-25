"""Feature engineering from daily kline bars."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import requests

from ..kline import get_kline, normalize_symbol, secid
from .universe import BENCHMARK_SYMBOL


@dataclass
class FeatureResult:
    symbol: str
    name: str
    as_of: str
    close: float
    factors: dict[str, float]
    bars_used: int
    day_chg_pct: float | None = None
    prev_close: float | None = None


def bars_to_df(bars: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    if df.empty:
        return df
    df = df.copy()
    df["time"] = df["time"].astype(str).str.slice(0, 10)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("time").reset_index(drop=True)
    if "volume" not in df.columns:
        df["volume"] = np.nan
    df["amount"] = df["close"] * df["volume"].fillna(0)
    return df


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
    )
    return s


def _fetch_eastmoney_daily(symbol: str, limit: int = 320) -> tuple[str, pd.DataFrame]:
    """Direct Eastmoney push2his kline — more reliable than Tencent for many ETFs."""
    r = _session().get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid(symbol),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "end": "20500101",
            "lmt": str(limit),
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json().get("data") or {}
    name = str(data.get("name") or symbol)
    bars: list[dict[str, Any]] = []
    for row in data.get("klines") or []:
        parts = str(row).split(",")
        if len(parts) < 6:
            continue
        bars.append(
            {
                "time": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]) if parts[5] not in ("", "-") else None,
            }
        )
    df = bars_to_df(bars)
    if len(df) < 25:
        raise RuntimeError("eastmoney daily too short")
    return name, df


def _fetch_akshare_daily(symbol: str) -> tuple[str, pd.DataFrame]:
    import akshare as ak

    last_err: Exception | None = None
    if symbol.startswith(("51", "56", "58", "15", "16", "18")):
        try:
            raw = ak.fund_etf_hist_em(symbol=symbol, period="daily", adjust="qfq")
            if raw is not None and not raw.empty:
                df = raw.rename(
                    columns={
                        "日期": "time",
                        "开盘": "open",
                        "收盘": "close",
                        "最高": "high",
                        "最低": "low",
                        "成交量": "volume",
                    }
                )
                out = bars_to_df(df.to_dict(orient="records"))
                if len(out) >= 25:
                    return symbol, out
        except Exception as exc:
            last_err = exc
    try:
        raw = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        if raw is not None and not raw.empty:
            df = raw.rename(
                columns={
                    "日期": "time",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                }
            )
            out = bars_to_df(df.to_dict(orient="records"))
            if len(out) >= 25:
                return symbol, out
    except Exception as exc:
        last_err = exc
    raise RuntimeError(f"akshare daily failed: {last_err}")


def fetch_daily_df(symbol: str) -> tuple[str, pd.DataFrame]:
    symbol = normalize_symbol(symbol)
    errors: list[str] = []

    # 1) Eastmoney (primary for advisor)
    try:
        return _fetch_eastmoney_daily(symbol)
    except Exception as exc:
        errors.append(f"em:{exc}")

    # 2) Existing kline module (Tencent etc.)
    try:
        payload = get_kline(symbol, "daily")
        name = str(payload.get("name") or symbol)
        df = bars_to_df(payload.get("bars") or [])
        if len(df) >= 25:
            return name, df
        errors.append("tencent:too_short")
    except Exception as exc:
        errors.append(f"tencent:{exc}")

    # 3) AKShare (single attempt — batch path relies on coarse fallback)
    try:
        return _fetch_akshare_daily(symbol)
    except Exception as exc:
        errors.append(f"ak:{exc}")

    raise RuntimeError("日线获取失败: " + " | ".join(errors[:4]))


def _ret(series: pd.Series, n: int) -> float:
    if len(series) <= n:
        return float("nan")
    a, b = float(series.iloc[-1]), float(series.iloc[-1 - n])
    if b == 0 or np.isnan(b):
        return float("nan")
    return a / b - 1.0


def _zscore_last(series: pd.Series, window: int = 20) -> float:
    s = series.dropna()
    if len(s) < window:
        return float("nan")
    window_s = s.iloc[-window:]
    mu = float(window_s.mean())
    sigma = float(window_s.std(ddof=0))
    if sigma < 1e-12:
        return 0.0
    return (float(s.iloc[-1]) - mu) / sigma


def _ann_vol(close: pd.Series, window: int = 20) -> float:
    if len(close) < window + 1:
        return float("nan")
    rets = close.pct_change().iloc[-window:]
    return float(rets.std(ddof=0) * np.sqrt(252))


def volume_ratio_last(df: pd.DataFrame, lookback: int = 5) -> float:
    """Last bar volume / mean of prior `lookback` bars (excluding last)."""
    n = int(lookback)
    if n < 1 or df is None or df.empty or "volume" not in df.columns:
        return float("nan")
    v = df["volume"].dropna()
    # need last + n prior bars
    if len(v) < n + 1:
        return float("nan")
    avg = float(v.iloc[-(n + 1) : -1].mean())
    if avg <= 0:
        return float("nan")
    return float(v.iloc[-1]) / avg


def compute_factors(
    df: pd.DataFrame,
    bench_df: pd.DataFrame | None = None,
) -> dict[str, float]:
    """Compute raw factor values for the last bar (as_of last close)."""
    close = df["close"]
    amount = df["amount"]

    mom_1 = _ret(close, 1)
    mom_5 = _ret(close, 5)
    mom_10 = _ret(close, 10)
    mom_20 = _ret(close, 20)

    rs_300 = float("nan")
    if bench_df is not None and not bench_df.empty:
        merged = pd.merge(
            df[["time", "close"]].rename(columns={"close": "c"}),
            bench_df[["time", "close"]].rename(columns={"close": "b"}),
            on="time",
            how="inner",
        )
        if len(merged) > 20:
            asset_r = _ret(merged["c"], 10)
            bench_r = _ret(merged["b"], 10)
            if not np.isnan(asset_r) and not np.isnan(bench_r):
                rs_300 = asset_r - bench_r

    ma20_bias = float("nan")
    if len(close) >= 20:
        ma20 = float(close.iloc[-20:].mean())
        if ma20 > 0:
            ma20_bias = float(close.iloc[-1]) / ma20 - 1.0

    vol_z = _zscore_last(amount, 20)
    vol_ratio = volume_ratio_last(df, 5)

    ann_vol = _ann_vol(close, 20)
    low_vol = float("nan") if np.isnan(ann_vol) else max(0.0, 1.0 - ann_vol)

    is_yin = 0.0
    is_yang = 0.0
    if "open" in df.columns and len(df) >= 1:
        last_close = float(close.iloc[-1])
        last_open = float(df["open"].iloc[-1])
        if last_close < last_open:
            is_yin = 1.0
        elif last_close > last_open:
            is_yang = 1.0

    return {
        "mom_1": mom_1,
        "mom_5": mom_5,
        "mom_10": mom_10,
        "mom_20": mom_20,
        "rs_300": rs_300,
        "ma20_bias": ma20_bias,
        "vol_z": vol_z,
        "vol_ratio": vol_ratio,
        "ann_vol": ann_vol,
        "low_vol": low_vol,
        "is_yin": is_yin,
        "is_yang": is_yang,
    }


def build_feature_result(
    symbol: str,
    name: str,
    df: pd.DataFrame,
    bench_df: pd.DataFrame | None,
    as_of: str | None = None,
) -> FeatureResult | None:
    if df is None or df.empty or len(df) < 25:
        return None
    work = df
    if as_of:
        work = df[df["time"] <= as_of]
        if len(work) < 25:
            return None
    factors = compute_factors(work, bench_df)
    last = work.iloc[-1]
    close = float(last["close"])
    day_chg: float | None = None
    prev_close: float | None = None
    if len(work) >= 2:
        prev_close = float(work.iloc[-2]["close"])
        if prev_close > 0:
            day_chg = close / prev_close - 1.0
    return FeatureResult(
        symbol=symbol,
        name=name,
        as_of=str(last["time"]),
        close=close,
        factors=factors,
        bars_used=len(work),
        day_chg_pct=None if day_chg is None else round(day_chg, 6),
        prev_close=prev_close,
    )


def load_benchmark(as_of: str | None = None) -> pd.DataFrame:
    try:
        _, df = fetch_daily_df(BENCHMARK_SYMBOL)
        if as_of:
            df = df[df["time"] <= as_of]
        return df
    except Exception:
        return pd.DataFrame()
