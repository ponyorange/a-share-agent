"""A-share major index quotes (大盘行情)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import akshare as ak
import requests

# Featured board — shown as large tiles first
FEATURED: list[dict[str, str]] = [
    {"symbol": "000001", "name": "上证指数", "secid": "1.000001"},
    {"symbol": "399001", "name": "深证成指", "secid": "0.399001"},
    {"symbol": "399006", "name": "创业板指", "secid": "0.399006"},
    {"symbol": "000688", "name": "科创50", "secid": "1.000688"},
    {"symbol": "000300", "name": "沪深300", "secid": "1.000300"},
    {"symbol": "000905", "name": "中证500", "secid": "1.000905"},
    {"symbol": "000852", "name": "中证1000", "secid": "1.000852"},
    {"symbol": "000016", "name": "上证50", "secid": "1.000016"},
    {"symbol": "899050", "name": "北证50", "secid": "0.899050"},
]

# Extra indices for the table (still curated, not full universe)
EXTRA: list[dict[str, str]] = [
    {"symbol": "399106", "name": "深证综指", "secid": "0.399106"},
    {"symbol": "000680", "name": "科创综指", "secid": "1.000680"},
    {"symbol": "000510", "name": "中证A500", "secid": "1.000510"},
    {"symbol": "000922", "name": "中证红利", "secid": "1.000922"},
    {"symbol": "399673", "name": "创业板50", "secid": "0.399673"},
    {"symbol": "399005", "name": "中小板指", "secid": "0.399005"},
]


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


def _fetch_eastmoney(catalog: list[dict[str, str]]) -> list[dict[str, Any]]:
    secids = ",".join(c["secid"] for c in catalog)
    featured_set = {c["symbol"] for c in FEATURED}
    r = _session().get(
        "https://push2delay.eastmoney.com/api/qt/ulist.np/get",
        params={
            "fltt": "2",
            "fields": "f2,f3,f4,f5,f6,f12,f13,f14,f15,f16,f17,f18",
            "secids": secids,
        },
        timeout=15,
    )
    r.raise_for_status()
    diff = (r.json().get("data") or {}).get("diff") or []
    by_code = {str(x.get("f12")): x for x in diff if x.get("f12")}
    out: list[dict[str, Any]] = []
    for meta in catalog:
        row = by_code.get(meta["symbol"])
        if not row:
            continue
        out.append(
            _item(
                symbol=meta["symbol"],
                name=str(row.get("f14") or meta["name"]),
                price=_num(row.get("f2")),
                change=_num(row.get("f4")),
                change_pct=_num(row.get("f3")),
                open_=_num(row.get("f17")),
                high=_num(row.get("f15")),
                low=_num(row.get("f16")),
                pre_close=_num(row.get("f18")),
                volume=_num(row.get("f5")),
                amount=_num(row.get("f6")),
                featured=meta["symbol"] in featured_set,
            )
        )
    if not out:
        raise RuntimeError("eastmoney returned empty index list")
    return out


def _sina_code(symbol: str) -> str:
    if symbol.startswith(("39", "15", "16", "18")) or symbol.startswith("399"):
        return f"sz{symbol}"
    if symbol.startswith("89"):
        return f"bj{symbol}"
    return f"sh{symbol}"


# 沪深京 A 股（不含指数）
_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_BOARD_FIELDS = "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18"


def _stock_row(rank: int, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": rank,
        "symbol": str(row.get("f12") or ""),
        "name": str(row.get("f14") or ""),
        "price": _num(row.get("f2")),
        "change": _num(row.get("f4")),
        "change_pct": _num(row.get("f3")),
        "open": _num(row.get("f17")),
        "high": _num(row.get("f15")),
        "low": _num(row.get("f16")),
        "pre_close": _num(row.get("f18")),
        "volume": _num(row.get("f5")),
        "amount": _num(row.get("f6")),
    }


def _fetch_clist(fid: str, po: int, limit: int = 15) -> list[dict[str, Any]]:
    """Eastmoney sorted A-share list. po=1 desc, po=0 asc; fid=f3 pct, f6 amount."""
    r = _session().get(
        "https://push2delay.eastmoney.com/api/qt/clist/get",
        params={
            "pn": "1",
            "pz": str(limit),
            "po": str(po),
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": fid,
            "fs": _A_SHARE_FS,
            "fields": _BOARD_FIELDS,
        },
        timeout=15,
    )
    r.raise_for_status()
    diff = (r.json().get("data") or {}).get("diff") or []
    return [_stock_row(i + 1, row) for i, row in enumerate(diff)]


def _fetch_boards(limit: int = 15) -> dict[str, Any]:
    gainers = _fetch_clist("f3", po=1, limit=limit)
    losers = _fetch_clist("f3", po=0, limit=limit)
    amount = _fetch_clist("f6", po=1, limit=limit)
    return {
        "gainers": gainers,
        "losers": losers,
        "amount": amount,
        "source": "eastmoney.clist",
    }


def _fetch_sina(catalog: list[dict[str, str]]) -> list[dict[str, Any]]:
    df = ak.stock_zh_index_spot_sina()
    if df is None or df.empty:
        raise RuntimeError("sina index spot empty")
    featured_set = {c["symbol"] for c in FEATURED}
    by_code = {str(r["代码"]): r for _, r in df.iterrows()}
    out: list[dict[str, Any]] = []
    for meta in catalog:
        key = _sina_code(meta["symbol"])
        row = by_code.get(key)
        if row is None:
            continue
        out.append(
            _item(
                symbol=meta["symbol"],
                name=str(row.get("名称") or meta["name"]),
                price=_num(row.get("最新价")),
                change=_num(row.get("涨跌额")),
                change_pct=_num(row.get("涨跌幅")),
                open_=_num(row.get("今开")),
                high=_num(row.get("最高")),
                low=_num(row.get("最低")),
                pre_close=_num(row.get("昨收")),
                volume=_num(row.get("成交量")),
                amount=_num(row.get("成交额")),
                featured=meta["symbol"] in featured_set,
            )
        )
    if not out:
        raise RuntimeError("sina returned no matched indices")
    return out


def get_market() -> dict[str, Any]:
    catalog = FEATURED + EXTRA
    source = "eastmoney.ulist"
    try:
        items = _fetch_eastmoney(catalog)
    except Exception:
        items = _fetch_sina(catalog)
        source = "sina.index_spot"

    featured = [x for x in items if x["featured"]]
    # keep FEATURED order
    order = {c["symbol"]: i for i, c in enumerate(FEATURED)}
    featured.sort(key=lambda x: order.get(x["symbol"], 999))

    # Rough two-market turnover from SH/SZ composite indices
    amount_sh = next((x["amount"] for x in items if x["symbol"] == "000001"), None)
    amount_sz = next((x["amount"] for x in items if x["symbol"] == "399001"), None)
    amount_total = None
    if amount_sh is not None or amount_sz is not None:
        amount_total = (amount_sh or 0) + (amount_sz or 0)

    boards: dict[str, Any] = {
        "gainers": [],
        "losers": [],
        "amount": [],
        "source": None,
        "error": None,
    }
    try:
        boards = {**_fetch_boards(15), "error": None}
    except Exception as exc:
        boards["error"] = f"{type(exc).__name__}: {exc}"

    return {
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": source,
        "summary": {
            "amount_sh": amount_sh,
            "amount_sz": amount_sz,
            "amount_total": amount_total,
        },
        "featured": featured,
        "indices": items,
        "boards": boards,
    }


def featured_indices_snapshot(market: dict[str, Any]) -> dict[str, Any]:
    """Slim featured-index payload for advisor agent tools."""
    indices: list[dict[str, Any]] = []
    for item in market.get("featured") or []:
        indices.append(
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "price": item.get("price"),
                "change": item.get("change"),
                "change_pct": item.get("change_pct"),
                "amount": item.get("amount"),
            }
        )
    return {
        "updated_at": market.get("updated_at"),
        "source": market.get("source"),
        "indices": indices,
    }


def _index_catalog() -> list[dict[str, str]]:
    return FEATURED + EXTRA


def resolve_index(query: str) -> dict[str, str] | None:
    """Resolve 6-digit code or Chinese name to catalog meta."""
    q = (query or "").strip()
    if not q:
        return None
    # allow sh000688 / 1.000688
    raw = q.upper().replace("SH", "").replace("SZ", "").replace("BJ", "")
    raw = raw.split(".")[-1]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 6:
        code = digits[-6:]
        for meta in _index_catalog():
            if meta["symbol"] == code:
                return meta
    aliases = {
        "科创板50": "000688",
        "STAR50": "000688",
        "STAR 50": "000688",
        "上证": "000001",
        "沪指": "000001",
        "深成指": "399001",
        "创业板": "399006",
        "创指": "399006",
        "沪深300": "000300",
        "CSI300": "000300",
    }
    alias_code = aliases.get(q) or aliases.get(q.upper())
    if alias_code:
        for meta in _index_catalog():
            if meta["symbol"] == alias_code:
                return meta
    for meta in _index_catalog():
        if meta["name"] == q or q in meta["name"] or meta["name"] in q:
            return meta
    return None


def _sina_index_symbol(meta: dict[str, str]) -> str:
    market, code = meta["secid"].split(".", 1)
    if code.startswith("899"):
        return f"bj{code}"
    prefix = "sh" if market == "1" else "sz"
    return f"{prefix}{code}"


def fetch_index_extremes(query: str = "科创50") -> dict[str, Any]:
    """Historical extremes from full daily history (not truncated kline).

    Primary source: ak.stock_zh_index_daily (sina). Returns intraday ATH
    (max high) and close ATH (max close) with dates, plus latest close.
    """
    meta = resolve_index(query)
    if meta is None:
        names = "、".join(m["name"] for m in FEATURED)
        return {
            "query": query,
            "error": "无法识别指数，请用代码（如 000688）或名称（如 科创50）",
            "supported_examples": names,
        }

    sina_symbol = _sina_index_symbol(meta)
    source = "akshare.stock_zh_index_daily"
    try:
        df = ak.stock_zh_index_daily(symbol=sina_symbol)
    except Exception as exc:
        return {
            "query": query,
            "symbol": meta["symbol"],
            "name": meta["name"],
            "sina_symbol": sina_symbol,
            "source": source,
            "error": f"{type(exc).__name__}: {exc}",
        }

    if df is None or getattr(df, "empty", True):
        return {
            "query": query,
            "symbol": meta["symbol"],
            "name": meta["name"],
            "sina_symbol": sina_symbol,
            "source": source,
            "error": "empty history",
        }

    work = df.copy()
    # normalize columns
    colmap = {str(c).lower(): c for c in work.columns}
    date_col = colmap.get("date") or next(
        (c for c in work.columns if "日期" in str(c)), None
    )
    high_col = colmap.get("high") or next(
        (c for c in work.columns if "高" in str(c)), None
    )
    close_col = colmap.get("close") or next(
        (c for c in work.columns if "收" in str(c)), None
    )
    if not date_col or not high_col or not close_col:
        return {
            "query": query,
            "symbol": meta["symbol"],
            "name": meta["name"],
            "source": source,
            "error": f"unexpected columns: {list(work.columns)}",
        }

    highs = work[high_col].map(_num)
    closes = work[close_col].map(_num)
    work = work.assign(_high=highs, _close=closes).dropna(subset=["_high", "_close"])
    if work.empty:
        return {
            "query": query,
            "symbol": meta["symbol"],
            "name": meta["name"],
            "source": source,
            "error": "no numeric OHLC rows",
        }

    i_high = work["_high"].idxmax()
    i_close = work["_close"].idxmax()
    last = work.iloc[-1]

    def _date_str(val: Any) -> str:
        if hasattr(val, "isoformat"):
            return str(val)[:10]
        return str(val)[:10]

    ath_high = float(work.loc[i_high, "_high"])
    ath_close = float(work.loc[i_close, "_close"])
    last_close = float(last["_close"])

    return {
        "query": query,
        "symbol": meta["symbol"],
        "name": meta["name"],
        "sina_symbol": sina_symbol,
        "source": source,
        "bars": int(len(work)),
        "ath_intraday": {
            "price": ath_high,
            "date": _date_str(work.loc[i_high, date_col]),
            "note": "日线最高价（盘中高点代理；精确到日）",
        },
        "ath_close": {
            "price": ath_close,
            "date": _date_str(work.loc[i_close, date_col]),
            "note": "历史最高收盘价",
        },
        "latest": {
            "date": _date_str(last[date_col]),
            "close": last_close,
            "high": float(last["_high"]),
        },
        "drawdown_from_ath_intraday_pct": round(
            (last_close / ath_high - 1.0) * 100.0, 2
        )
        if ath_high
        else None,
    }
