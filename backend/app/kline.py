"""K-line data for A-share charts (realtime / 5d / daily / weekly / monthly)."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Literal

import akshare as ak
import pandas as pd
import requests

RangeType = Literal["realtime", "5d", "daily", "weekly", "monthly"]

VALID_RANGES: tuple[str, ...] = ("realtime", "5d", "daily", "weekly", "monthly")


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if s.startswith(prefix) and len(s) > 2:
            s = s[len(prefix) :]
            break
    if s.startswith(("SH", "SZ", "BJ")):
        pass
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) != 6:
        raise ValueError("股票代码需为 6 位数字，例如 000001")
    return digits


# Shanghai indices that begin with 0 — without this they are mis-mapped to SZ.
# 000001 is intentionally omitted: it collides with 平安银行 (SZ000001).
_SH_INDEX_SYMBOLS = frozenset(
    {
        "000016",  # 上证50
        "000300",  # 沪深300
        "000510",  # 中证A500
        "000680",  # 科创综指
        "000688",  # 科创50
        "000852",  # 中证1000
        "000905",  # 中证500
        "000922",  # 中证红利
    }
)


def market_prefix(symbol: str) -> str:
    """Map 6-digit code to exchange prefix used by Tencent/Sina.

    Shanghai: 6xxxxx stocks, 5xxxxx funds/ETFs, 9xxxxx B-shares,
    plus curated 0xxxxx Shanghai indices (e.g. 000300).
    Beijing: 4xxxxx / 8xxxxx.
    Shenzhen: remaining 0xxxxx / 1xxxxx / 2xxxxx / 3xxxxx (incl. 15/16xx ETFs).
    """
    if symbol in _SH_INDEX_SYMBOLS:
        return "sh"
    if symbol.startswith(("5", "6", "9")):
        return "sh"
    if symbol.startswith(("4", "8")):
        return "bj"
    return "sz"


def secid(symbol: str) -> str:
    # Eastmoney: 1=SH, 0=SZ; BJ uses 0 in many quote APIs
    market = 1 if market_prefix(symbol) == "sh" else 0
    return f"{market}.{symbol}"


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
    safe_avg = _safe_avg_price(avg_price)
    if safe_avg is not None:
        item["avg_price"] = safe_avg
    return item


def _safe_avg_price(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _parse_trend_row(row: Any) -> dict[str, Any] | None:
    parts = str(row).split(",")
    if len(parts) < 6:
        return None
    avg_price = _safe_avg_price(parts[7]) if len(parts) >= 8 else None
    return _bar(
        parts[0],
        float(parts[1]),
        float(parts[3]),
        float(parts[4]),
        float(parts[2]),
        float(parts[5]),
        avg_price=avg_price,
    )


def _fetch_trends(symbol: str, ndays: int) -> tuple[str, float | None, list[dict[str, Any]]]:
    url = "https://push2delay.eastmoney.com/api/qt/stock/trends2/get"
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": str(ndays),
        "secid": secid(symbol),
    }
    r = _session().get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("data") or {}
    name = data.get("name") or symbol
    pre_close = data.get("preClose")
    bars: list[dict[str, Any]] = []
    for row in data.get("trends") or []:
        # 时间,开,收,高,低,成交量,成交额,均价
        bar = _parse_trend_row(row)
        if bar is not None:
            bars.append(bar)
    return name, float(pre_close) if pre_close is not None else None, bars


def _fetch_tencent_kline(
    symbol: str, period: str
) -> tuple[str, list[dict[str, Any]], str]:
    """period: day | week | month. Returns (name, bars, adjust) where adjust is qfq|none."""
    code = f"{market_prefix(symbol)}{symbol}"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    r = _session().get(
        url,
        params={"param": f"{code},{period},,,320,qfq"},
        timeout=20,
        headers={"Referer": "https://gu.qq.com/"},
    )
    r.raise_for_status()
    payload = r.json().get("data") or {}
    block = payload.get(code) or {}
    key_map = {"day": "qfqday", "week": "qfqweek", "month": "qfqmonth"}
    qfq_key = key_map[period]
    # Prefer 前复权 series; wrong market prefix often only returns unadjusted `day`
    rows = block.get(qfq_key) or []
    adjust_used = "qfq" if rows else "none"
    if not rows:
        rows = block.get(period) or []
    name = symbol
    qt = block.get("qt") or {}
    # qt may be dict of lists; name sometimes at qt[code][1]
    if isinstance(qt, dict):
        arr = qt.get(code)
        if isinstance(arr, list) and len(arr) > 1:
            name = str(arr[1]) or symbol
    bars: list[dict[str, Any]] = []
    for row in rows:
        # date, open, close, high, low, volume
        if not row or len(row) < 5:
            continue
        vol = float(row[5]) if len(row) > 5 else None
        bars.append(
            _bar(
                str(row[0]),
                float(row[1]),
                float(row[3]),
                float(row[4]),
                float(row[2]),
                vol,
            )
        )
    return name, bars, adjust_used


def _fetch_eastmoney_kline(
    symbol: str, period: str, limit: int = 320
) -> tuple[str, list[dict[str, Any]], str]:
    """period: day | week | month. Eastmoney push2his fallback (前复权)."""
    klt_map = {"day": "101", "week": "102", "month": "103"}
    klt = klt_map.get(period)
    if not klt:
        raise ValueError(f"unsupported period: {period}")
    hosts = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        "https://push2delay.eastmoney.com/api/qt/stock/kline/get",
    )
    last_err: Exception | None = None
    for url in hosts:
        try:
            r = _session().get(
                url,
                params={
                    "secid": secid(symbol),
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": klt,
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
                vol = None
                if parts[5] not in ("", "-"):
                    try:
                        vol = float(parts[5])
                    except ValueError:
                        vol = None
                bars.append(
                    _bar(
                        parts[0],
                        float(parts[1]),
                        float(parts[3]),
                        float(parts[4]),
                        float(parts[2]),
                        vol,
                    )
                )
            if bars:
                return name, bars, "qfq"
            last_err = RuntimeError("eastmoney kline empty")
        except Exception as exc:
            last_err = exc
            continue
    raise RuntimeError(f"eastmoney kline failed: {last_err}")


def _fetch_akshare_kline(
    symbol: str, period: str
) -> tuple[str, list[dict[str, Any]], str]:
    """period: day only via akshare hist (week/month resampled from daily)."""
    raw = None
    if symbol.startswith(("51", "56", "58", "15", "16", "18")):
        try:
            raw = ak.fund_etf_hist_em(symbol=symbol, period="daily", adjust="qfq")
        except Exception:
            raw = None
    if raw is None or (hasattr(raw, "empty") and raw.empty):
        raw = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
    if raw is None or raw.empty:
        raise RuntimeError("akshare hist empty")

    df = raw.rename(
        columns={
            "日期": "time",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        }
    ).copy()
    df["time"] = df["time"].astype(str).str.slice(0, 10)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("time")

    if period == "week":
        df["time"] = pd.to_datetime(df["time"])
        df = (
            df.set_index("time")
            .resample("W-FRI")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["close"])
            .reset_index()
        )
        df["time"] = df["time"].dt.strftime("%Y-%m-%d")
    elif period == "month":
        df["time"] = pd.to_datetime(df["time"])
        df = (
            df.set_index("time")
            .resample("ME")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["close"])
            .reset_index()
        )
        df["time"] = df["time"].dt.strftime("%Y-%m-%d")

    # keep last ~320 bars
    df = df.tail(320)
    bars = [
        _bar(
            str(row["time"]),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]) if pd.notna(row.get("volume")) else None,
        )
        for _, row in df.iterrows()
    ]
    if not bars:
        raise RuntimeError("akshare bars empty")
    return symbol, bars, "qfq"


def _fetch_sina_kline(
    symbol: str, period: str, limit: int = 320
) -> tuple[str, list[dict[str, Any]], str]:
    """Sina CN_MarketData.getKLineData — reliable when Tencent/Eastmoney fail.

    scale: 240=日K. week/month resampled from daily.
    """
    code = f"{market_prefix(symbol)}{symbol}"
    # fetch enough daily bars to resample
    datalen = limit if period == "day" else min(limit * 6, 800)
    r = _session().get(
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData",
        params={
            "symbol": code,
            "scale": "240",
            "ma": "no",
            "datalen": str(datalen),
        },
        timeout=20,
        headers={"Referer": "https://finance.sina.com.cn"},
    )
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("sina kline empty")

    df = pd.DataFrame(rows)
    df = df.rename(
        columns={
            "day": "time",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
    )
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("time")

    if period == "week":
        df["time"] = pd.to_datetime(df["time"])
        df = (
            df.set_index("time")
            .resample("W-FRI")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["close"])
            .reset_index()
        )
        df["time"] = df["time"].dt.strftime("%Y-%m-%d")
    elif period == "month":
        df["time"] = pd.to_datetime(df["time"])
        df = (
            df.set_index("time")
            .resample("ME")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["close"])
            .reset_index()
        )
        df["time"] = df["time"].dt.strftime("%Y-%m-%d")

    df = df.tail(limit)
    bars = [
        _bar(
            str(row["time"]),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]) if pd.notna(row.get("volume")) else None,
        )
        for _, row in df.iterrows()
    ]
    if not bars:
        raise RuntimeError("sina bars empty")
    return symbol, bars, "none"


def _fetch_ohlc_kline(
    symbol: str, period: str
) -> tuple[str, list[dict[str, Any]], str, str]:
    """Tencent → Eastmoney → Sina → AKShare."""
    errors: list[str] = []
    try:
        name, bars, adj = _fetch_tencent_kline(symbol, period)
        if bars:
            return name, bars, adj, f"tencent.{period}.{adj}"
        errors.append("tencent:empty")
    except Exception as exc:
        errors.append(f"tencent:{type(exc).__name__}")

    try:
        name, bars, adj = _fetch_eastmoney_kline(symbol, period)
        return name, bars, adj, f"eastmoney.{period}.{adj}"
    except Exception as exc:
        errors.append(f"eastmoney:{type(exc).__name__}")

    try:
        name, bars, adj = _fetch_sina_kline(symbol, period)
        name = _fetch_name(symbol)
        return name, bars, adj, f"sina.{period}.{adj}"
    except Exception as exc:
        errors.append(f"sina:{type(exc).__name__}")

    try:
        name, bars, adj = _fetch_akshare_kline(symbol, period)
        name = _fetch_name(symbol) if name == symbol else name
        return name, bars, adj, f"akshare.{period}.{adj}"
    except Exception as exc:
        errors.append(f"akshare:{type(exc).__name__}")

    raise RuntimeError("K线获取失败: " + " | ".join(errors))


def _fetch_sina_minute_5d(symbol: str) -> tuple[str, list[dict[str, Any]]]:
    code = f"{market_prefix(symbol)}{symbol}"
    df = ak.stock_zh_a_minute(symbol=code, period="5", adjust="qfq")
    if df is None or df.empty:
        return symbol, []
    df = df.copy()
    df["day"] = df["day"].astype(str)
    df["_date"] = df["day"].str.slice(0, 10)
    dates = sorted(df["_date"].unique())
    last_dates = set(dates[-5:])
    df = df[df["_date"].isin(last_dates)]
    bars = [
        _bar(
            str(row["day"]),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]) if "volume" in row and pd.notna(row["volume"]) else None,
        )
        for _, row in df.iterrows()
    ]
    return symbol, bars


def _fetch_name(symbol: str) -> str:
    try:
        name, _, _ = _fetch_trends(symbol, 1)
        if name:
            return name
    except Exception:
        pass
    try:
        info = ak.stock_individual_info_em(symbol=symbol)
        if info is not None and not info.empty:
            mapping = dict(zip(info.iloc[:, 0].astype(str), info.iloc[:, 1].astype(str)))
            for key in ("股票简称", "名称", "name"):
                if key in mapping:
                    return mapping[key]
    except Exception:
        pass
    return symbol


def get_kline(symbol: str, range_: str, adjust: str = "qfq") -> dict[str, Any]:
    del adjust  # tencent/sina paths already use qfq where applicable
    symbol = normalize_symbol(symbol)
    if range_ not in VALID_RANGES:
        raise ValueError(f"range 无效，可选: {', '.join(VALID_RANGES)}")

    name = symbol
    pre_close: float | None = None
    bars: list[dict[str, Any]] = []
    chart_type = "candle"
    source = ""

    if range_ == "realtime":
        chart_type = "line"
        name, pre_close, bars = _fetch_trends(symbol, 1)
        source = "eastmoney.trends2"
        if not bars:
            # fallback: tick → last price per minute
            ticks = ak.stock_intraday_em(symbol=symbol)
            if ticks is not None and not ticks.empty:
                name = _fetch_name(symbol)
                today = datetime.now().strftime("%Y-%m-%d")
                grouped: dict[str, list[float]] = {}
                for _, row in ticks.iterrows():
                    t = str(row["时间"])
                    if len(t) == 5:
                        t = f"{t}:00"
                    key = f"{today} {t[:5]}"
                    grouped.setdefault(key, []).append(float(row["成交价"]))
                bars = [
                    _bar(k, v[0], max(v), min(v), v[-1])
                    for k, v in grouped.items()
                ]
                source = "eastmoney.intraday"
    elif range_ == "5d":
        chart_type = "candle"
        try:
            name, pre_close, bars = _fetch_trends(symbol, 5)
            source = "eastmoney.trends2"
            # weekend/holiday may only return 1 day — still ok; enrich via sina if thin
            dates = {b["time"][:10] for b in bars}
            if len(dates) < 2:
                raise RuntimeError("trends 天数不足")
        except Exception:
            name, bars = _fetch_sina_minute_5d(symbol)
            if name == symbol:
                name = _fetch_name(symbol)
            source = "sina.minute"
    elif range_ == "daily":
        name, bars, _adj, source = _fetch_ohlc_kline(symbol, "day")
    elif range_ == "weekly":
        name, bars, _adj, source = _fetch_ohlc_kline(symbol, "week")
    else:  # monthly
        name, bars, _adj, source = _fetch_ohlc_kline(symbol, "month")

    if not bars:
        raise RuntimeError("未获取到 K 线数据，请稍后重试或检查代码是否正确")

    if not name or name == symbol:
        name = _fetch_name(symbol)

    # Day/week/month quotes should use previous bar close as 昨收 (not today's open)
    if pre_close is None and len(bars) >= 2:
        pre_close = float(bars[-2]["close"])

    last = bars[-1]
    return {
        "symbol": symbol,
        "name": name,
        "range": range_,
        "chart_type": chart_type,
        "pre_close": pre_close,
        "source": source,
        "count": len(bars),
        "last": last,
        "bars": bars,
    }


def _sma(closes: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if period <= 0 or len(closes) < period:
        return out
    window_sum = 0.0
    for i, price in enumerate(closes):
        window_sum += price
        if i >= period:
            window_sum -= closes[i - period]
        if i >= period - 1:
            out[i] = window_sum / period
    return out


def fetch_symbol_daily_ma(symbol: str, recent: int = 30) -> dict[str, Any]:
    """Daily OHLC + MA5/MA10/MA20 for advisor agent (slim recent window)."""
    sym = normalize_symbol(symbol)
    payload = get_kline(sym, "daily")
    bars = payload.get("bars") or []
    closes = [float(b["close"]) for b in bars]
    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)

    def _round(v: float | None) -> float | None:
        if v is None:
            return None
        return round(float(v), 4)

    n = max(1, min(int(recent), 60))
    start = max(0, len(bars) - n)
    recent_bars: list[dict[str, Any]] = []
    for i in range(start, len(bars)):
        b = bars[i]
        recent_bars.append(
            {
                "time": b.get("time"),
                "open": b.get("open"),
                "high": b.get("high"),
                "low": b.get("low"),
                "close": b.get("close"),
                "volume": b.get("volume"),
                "ma5": _round(ma5[i]),
                "ma10": _round(ma10[i]),
                "ma20": _round(ma20[i]),
            }
        )

    last_i = len(bars) - 1
    last_close = closes[last_i] if last_i >= 0 else None
    latest = {
        "time": bars[last_i].get("time") if last_i >= 0 else None,
        "close": last_close,
        "ma5": _round(ma5[last_i]) if last_i >= 0 else None,
        "ma10": _round(ma10[last_i]) if last_i >= 0 else None,
        "ma20": _round(ma20[last_i]) if last_i >= 0 else None,
    }
    if last_close is not None:
        for key in ("ma5", "ma10", "ma20"):
            ma_v = latest[key]
            if ma_v:
                latest[f"close_vs_{key}_pct"] = round(
                    (last_close / float(ma_v) - 1.0) * 100.0, 2
                )

    return {
        "symbol": payload.get("symbol") or sym,
        "name": payload.get("name") or sym,
        "source": payload.get("source"),
        "bars_total": len(bars),
        "latest": latest,
        "recent": recent_bars,
    }
