"""Yahoo Finance market overview (US / global indices)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

FEATURED: list[dict[str, str]] = [
    {"symbol": "^GSPC", "name": "标普500"},
    {"symbol": "^DJI", "name": "道琼斯"},
    {"symbol": "^IXIC", "name": "纳斯达克"},
    {"symbol": "^RUT", "name": "罗素2000"},
    {"symbol": "^VIX", "name": "VIX"},
    {"symbol": "^FTSE", "name": "富时100"},
    {"symbol": "^N225", "name": "日经225"},
    {"symbol": "^HSI", "name": "恒生指数"},
]

EXTRA: list[dict[str, str]] = [
    {"symbol": "000001.SS", "name": "上证指数"},
    {"symbol": "^GDAXI", "name": "德国DAX"},
    {"symbol": "BTC-USD", "name": "比特币"},
    {"symbol": "GC=F", "name": "黄金期货"},
]

CACHE_TTL_SEC = 45.0
_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


def _num(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _item(
    symbol: str,
    name: str,
    *,
    price: float | None,
    change: float | None,
    change_pct: float | None,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    pre_close: float | None = None,
    volume: float | None = None,
    amount: float | None = None,
    featured: bool = False,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "open": open_,
        "high": high,
        "low": low,
        "pre_close": pre_close,
        "volume": volume,
        "amount": amount,
        "featured": featured,
    }


def _empty_item(symbol: str, name: str, featured: bool) -> dict[str, Any]:
    return _item(
        symbol,
        name,
        price=None,
        change=None,
        change_pct=None,
        featured=featured,
    )


def _hist_frame_for_symbol(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        level0 = df.columns.get_level_values(0)
        if symbol in set(level0):
            sub = df[symbol]
        else:
            # yfinance sometimes drops ^ prefix quirks; try exact match only
            return pd.DataFrame()
    else:
        sub = df
    if not isinstance(sub, pd.DataFrame):
        return pd.DataFrame()
    return sub.dropna(how="all")


def _quote_from_history(
    symbol: str, name: str, featured: bool, hist: pd.DataFrame
) -> dict[str, Any]:
    if hist is None or hist.empty:
        return _empty_item(symbol, name, featured)
    last = hist.iloc[-1]
    price = _num(last.get("Close"))
    open_ = _num(last.get("Open"))
    high = _num(last.get("High"))
    low = _num(last.get("Low"))
    volume = _num(last.get("Volume"))
    pre_close = None
    if len(hist) >= 2:
        pre_close = _num(hist.iloc[-2].get("Close"))
    if pre_close is None:
        pre_close = price
    change = None
    change_pct = None
    if price is not None and pre_close is not None:
        change = price - pre_close
        if pre_close != 0:
            change_pct = (change / pre_close) * 100.0
    return _item(
        symbol,
        name,
        price=price,
        change=change,
        change_pct=change_pct,
        open_=open_,
        high=high,
        low=low,
        pre_close=pre_close,
        volume=volume,
        amount=None,
        featured=featured,
    )


def _is_rate_limit(exc: BaseException) -> bool:
    name = type(exc).__name__
    msg = str(exc)
    return "RateLimit" in name or "Too Many Requests" in msg or "rate limited" in msg.lower()


def _download_history(tickers: str) -> pd.DataFrame:
    import yfinance as yf

    try:
        return yf.download(
            tickers,
            period="5d",
            interval="1d",
            group_by="ticker",
            threads=False,
            progress=False,
            auto_adjust=False,
        )
    except Exception as exc:
        if _is_rate_limit(exc):
            raise RuntimeError(
                "Yahoo Finance 限流（Too Many Requests），请稍后再试"
            ) from exc
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


def _fetch_indices() -> list[dict[str, Any]]:
    """Batch-download daily bars for all curated symbols (one Yahoo round-trip)."""
    catalog = FEATURED + EXTRA
    featured_set = {c["symbol"] for c in FEATURED}
    symbols = [c["symbol"] for c in catalog]
    tickers = " ".join(symbols)

    df = _download_history(tickers)
    out: list[dict[str, Any]] = []
    for meta in catalog:
        sym = meta["symbol"]
        featured = sym in featured_set
        hist = _hist_frame_for_symbol(df, sym)
        out.append(_quote_from_history(sym, meta["name"], featured, hist))

    if out and all(x.get("price") is None for x in out):
        # brief backoff + one retry (Yahoo often recovers after short cooldown)
        time.sleep(2.0)
        df = _download_history(tickers)
        out = []
        for meta in catalog:
            sym = meta["symbol"]
            featured = sym in featured_set
            hist = _hist_frame_for_symbol(df, sym)
            out.append(_quote_from_history(sym, meta["name"], featured, hist))
        if out and all(x.get("price") is None for x in out):
            raise RuntimeError(
                "未能获取 yfinance 指数行情（Yahoo 可能限流或网络不可用），请稍后再试"
            )
    return out


def _board_row(rank: int, row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or row.get("Symbol") or "")
    name = str(row.get("shortName") or row.get("longName") or row.get("name") or symbol)
    price = _num(row.get("regularMarketPrice") or row.get("price") or row.get("lastPrice"))
    change = _num(row.get("regularMarketChange") or row.get("change"))
    change_pct = _num(
        row.get("regularMarketChangePercent")
        or row.get("percentchange")
        or row.get("change_pct")
    )
    volume = _num(row.get("regularMarketVolume") or row.get("volume"))
    amount = None
    if price is not None and volume is not None:
        amount = price * volume
    return {
        "rank": rank,
        "symbol": symbol,
        "name": name,
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "open": _num(row.get("regularMarketOpen") or row.get("open")),
        "high": _num(row.get("regularMarketDayHigh") or row.get("dayHigh")),
        "low": _num(row.get("regularMarketDayLow") or row.get("dayLow")),
        "pre_close": _num(
            row.get("regularMarketPreviousClose") or row.get("previousClose")
        ),
        "volume": volume,
        "amount": amount,
    }


def _screen_rows(preset: str, count: int = 15) -> list[dict[str, Any]]:
    import yfinance as yf

    screen_fn = getattr(yf, "screen", None)
    if screen_fn is None:
        raise RuntimeError("当前 yfinance 版本不支持 screen()")

    raw = screen_fn(preset, count=count)
    if isinstance(raw, dict):
        quotes = raw.get("quotes")
        if isinstance(quotes, list):
            return [q for q in quotes if isinstance(q, dict)]
        finance = raw.get("finance") or {}
        result = finance.get("result") or []
        if result and isinstance(result[0], dict):
            nested = result[0].get("quotes")
            if isinstance(nested, list):
                return [q for q in nested if isinstance(q, dict)]
        for key in ("quotes", "rows", "data"):
            if isinstance(raw.get(key), list):
                return [q for q in raw[key] if isinstance(q, dict)]
    if isinstance(raw, pd.DataFrame):
        return raw.reset_index().to_dict(orient="records")
    raise RuntimeError(f"无法解析 screener 结果: {type(raw).__name__}")


def _fetch_boards(limit: int = 15) -> dict[str, Any]:
    presets = (
        ("gainers", "day_gainers"),
        ("losers", "day_losers"),
        ("amount", "most_actives"),
    )
    out: dict[str, Any] = {
        "gainers": [],
        "losers": [],
        "amount": [],
        "source": "yfinance",
        "error": None,
    }
    errors: list[str] = []
    for key, preset in presets:
        try:
            rows = _screen_rows(preset, limit)
            out[key] = [
                _board_row(i + 1, r) for i, r in enumerate(rows[:limit]) if r
            ]
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            errors.append(msg)
            out[key] = []
            # stop hammering Yahoo after rate limit
            if _is_rate_limit(exc):
                out["error"] = "Yahoo Finance 限流，涨跌榜暂不可用"
                return out
    if not out["gainers"] and not out["losers"] and not out["amount"]:
        out["error"] = errors[0] if errors else "screener 无数据或当前环境不可用"
    return out


def get_market(*, force: bool = False) -> dict[str, Any]:
    now_mono = time.monotonic()
    cached = _cache.get("payload")
    if (
        not force
        and cached is not None
        and (now_mono - float(_cache.get("ts") or 0.0)) < CACHE_TTL_SEC
    ):
        return cached

    items = _fetch_indices()
    featured = [x for x in items if x["featured"]]
    order = {c["symbol"]: i for i, c in enumerate(FEATURED)}
    featured.sort(key=lambda x: order.get(x["symbol"], 999))

    boards = _fetch_boards(15)

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "yfinance",
        "summary": {
            "amount_sh": None,
            "amount_sz": None,
            "amount_total": None,
        },
        "featured": featured,
        "indices": items,
        "boards": boards,
    }
    _cache["ts"] = now_mono
    _cache["payload"] = payload
    return payload
