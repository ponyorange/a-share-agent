"""Yahoo Finance market overview (US / global indices)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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


def _quote_from_fast_info(symbol: str, name: str, featured: bool) -> dict[str, Any]:
    import yfinance as yf

    t = yf.Ticker(symbol)
    fi = getattr(t, "fast_info", None)
    data: dict[str, Any] = {}
    if fi is not None:
        try:
            data = dict(fi) if hasattr(fi, "items") else dict(fi or {})
        except Exception:
            for key in (
                "lastPrice",
                "previousClose",
                "open",
                "dayHigh",
                "dayLow",
                "lastVolume",
                "regularMarketPreviousClose",
            ):
                try:
                    data[key] = getattr(fi, key, None)
                except Exception:
                    pass

    price = _num(data.get("lastPrice") or data.get("last_price"))
    pre_close = _num(
        data.get("previousClose")
        or data.get("regularMarketPreviousClose")
        or data.get("previous_close")
    )
    open_ = _num(data.get("open"))
    high = _num(data.get("dayHigh") or data.get("day_high"))
    low = _num(data.get("dayLow") or data.get("day_low"))
    volume = _num(data.get("lastVolume") or data.get("last_volume"))

    if price is None or pre_close is None:
        hist = t.history(period="5d", interval="1d", auto_adjust=False)
        if hist is not None and not hist.empty:
            last = hist.iloc[-1]
            price = _num(last.get("Close"))
            open_ = open_ if open_ is not None else _num(last.get("Open"))
            high = high if high is not None else _num(last.get("High"))
            low = low if low is not None else _num(last.get("Low"))
            volume = volume if volume is not None else _num(last.get("Volume"))
            if len(hist) >= 2:
                pre_close = _num(hist.iloc[-2].get("Close"))
            elif pre_close is None:
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


def _fetch_indices() -> list[dict[str, Any]]:
    featured_set = {c["symbol"] for c in FEATURED}
    catalog = FEATURED + EXTRA
    out: list[dict[str, Any]] = []
    for meta in catalog:
        try:
            out.append(
                _quote_from_fast_info(
                    meta["symbol"],
                    meta["name"],
                    featured=meta["symbol"] in featured_set,
                )
            )
        except Exception:
            out.append(
                _item(
                    meta["symbol"],
                    meta["name"],
                    price=None,
                    change=None,
                    change_pct=None,
                    featured=meta["symbol"] in featured_set,
                )
            )
    return out


def _board_row(rank: int, row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or row.get("Symbol") or "")
    name = str(row.get("shortName") or row.get("longName") or row.get("name") or symbol)
    price = _num(row.get("regularMarketPrice") or row.get("price") or row.get("lastPrice"))
    change = _num(row.get("regularMarketChange") or row.get("change"))
    change_pct = _num(
        row.get("regularMarketChangePercent") or row.get("percentchange") or row.get("change_pct")
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
        "pre_close": _num(row.get("regularMarketPreviousClose") or row.get("previousClose")),
        "volume": volume,
        "amount": amount,
    }


def _screen_rows(preset: str, count: int = 15) -> list[dict[str, Any]]:
    import yfinance as yf
    import pandas as pd

    screen_fn = getattr(yf, "screen", None)
    if screen_fn is None:
        raise RuntimeError("当前 yfinance 版本不支持 screen()")

    raw = screen_fn(preset, count=count)
    if isinstance(raw, dict):
        quotes = raw.get("quotes") or raw.get("finance", {}).get("result", [{}])[0].get(
            "quotes"
        )
        if isinstance(quotes, list):
            return [q for q in quotes if isinstance(q, dict)]
        # nested result
        for key in ("quotes", "rows", "data"):
            if isinstance(raw.get(key), list):
                return [q for q in raw[key] if isinstance(q, dict)]
    if isinstance(raw, pd.DataFrame):
        return raw.reset_index().to_dict(orient="records")
    raise RuntimeError(f"无法解析 screener 结果: {type(raw).__name__}")


def _fetch_boards(limit: int = 15) -> dict[str, Any]:
    gainers: list[dict[str, Any]] = []
    losers: list[dict[str, Any]] = []
    amount: list[dict[str, Any]] = []
    try:
        g_rows = _screen_rows("day_gainers", limit)
        gainers = [_board_row(i + 1, r) for i, r in enumerate(g_rows[:limit]) if r]
    except Exception:
        gainers = []
    try:
        l_rows = _screen_rows("day_losers", limit)
        losers = [_board_row(i + 1, r) for i, r in enumerate(l_rows[:limit]) if r]
    except Exception:
        losers = []
    try:
        a_rows = _screen_rows("most_actives", limit)
        amount = [_board_row(i + 1, r) for i, r in enumerate(a_rows[:limit]) if r]
    except Exception:
        amount = []

    return {
        "gainers": gainers,
        "losers": losers,
        "amount": amount,
        "source": "yfinance",
    }


def get_market() -> dict[str, Any]:
    items = _fetch_indices()
    featured = [x for x in items if x["featured"]]
    order = {c["symbol"]: i for i, c in enumerate(FEATURED)}
    featured.sort(key=lambda x: order.get(x["symbol"], 999))

    boards: dict[str, Any] = {
        "gainers": [],
        "losers": [],
        "amount": [],
        "source": "yfinance",
        "error": None,
    }
    try:
        boards = {**_fetch_boards(15), "error": None}
        if not boards["gainers"] and not boards["losers"] and not boards["amount"]:
            boards["error"] = "screener 无数据或当前环境不可用"
    except Exception as exc:
        boards["error"] = f"{type(exc).__name__}: {exc}"

    return {
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
