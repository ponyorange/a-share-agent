"""Realtime quote board: 五档盘口 + 分时成交.

Eastmoney push2delay often clears 五档 after hours; fall back to Tencent
last-session book (keeps previous trading day close levels).
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .kline import market_prefix, normalize_symbol, secid

_CN = ZoneInfo("Asia/Shanghai")


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
    if f != f:
        return None
    return f


def _level(price: Any, volume: Any) -> dict[str, float | None]:
    return {"price": _num(price), "volume": _num(volume)}


def _book_has_levels(asks: list[dict], bids: list[dict]) -> bool:
    return any(
        lv.get("price") is not None for lv in (asks or []) + (bids or [])
    )


def trading_session(now: datetime | None = None) -> dict[str, Any]:
    """A-share continuous auction window (weekday + time; holidays via calendar)."""
    now = now or datetime.now(_CN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_CN)
    else:
        now = now.astimezone(_CN)

    weekday = now.weekday()  # 0=Mon
    t = now.time()
    # 含集合竞价尾盘一点冗余，便于收盘前刷新
    morning = time(9, 15) <= t <= time(11, 30)
    afternoon = time(13, 0) <= t <= time(15, 5)
    is_weekday = weekday < 5
    trade_day = is_weekday
    try:
        from .advisor.calendar_util import is_trading_day

        trade_day = bool(is_trading_day(now.date()))
    except Exception:
        trade_day = is_weekday
    is_trading = trade_day and (morning or afternoon)

    return {
        "timezone": "Asia/Shanghai",
        "now": now.isoformat(timespec="seconds"),
        "is_weekday": is_weekday,
        "is_trading_day": trade_day,
        "is_trading": is_trading,
        "refresh_recommended": is_trading,
    }


def _fetch_snapshot_em(symbol: str) -> dict[str, Any]:
    fields = (
        "f11,f12,f13,f14,f15,f16,f17,f18,f19,f20,"
        "f31,f32,f33,f34,f35,f36,f37,f38,f39,f40,"
        "f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,"
        "f57,f58,f60,f71,f161,f168,f169,f170"
    )
    r = _session().get(
        "https://push2delay.eastmoney.com/api/qt/stock/get",
        params={
            "fltt": "2",
            "invt": "2",
            "secid": secid(symbol),
            "fields": fields,
        },
        timeout=15,
    )
    r.raise_for_status()
    data = r.json().get("data") or {}
    if not data:
        raise RuntimeError("东财未返回盘口快照")

    asks = [
        _level(data.get("f31"), data.get("f32")),
        _level(data.get("f33"), data.get("f34")),
        _level(data.get("f35"), data.get("f36")),
        _level(data.get("f37"), data.get("f38")),
        _level(data.get("f39"), data.get("f40")),
    ]
    bids = [
        _level(data.get("f19"), data.get("f20")),
        _level(data.get("f17"), data.get("f18")),
        _level(data.get("f15"), data.get("f16")),
        _level(data.get("f13"), data.get("f14")),
        _level(data.get("f11"), data.get("f12")),
    ]

    return {
        "symbol": str(data.get("f57") or symbol),
        "name": str(data.get("f58") or symbol),
        "price": _num(data.get("f43")),
        "change": _num(data.get("f169")),
        "change_pct": _num(data.get("f170")),
        "open": _num(data.get("f46")),
        "high": _num(data.get("f44")),
        "low": _num(data.get("f45")),
        "pre_close": _num(data.get("f60")),
        "avg_price": _num(data.get("f71")),
        "volume": _num(data.get("f47")),
        "amount": _num(data.get("f48")),
        "turnover": _num(data.get("f168")),
        "volume_ratio": _num(data.get("f50")),
        "limit_up": _num(data.get("f51")),
        "limit_down": _num(data.get("f52")),
        "outer_vol": _num(data.get("f49")),
        "inner_vol": _num(data.get("f161")),
        "asks": asks,
        "bids": bids,
        "book_source": "eastmoney",
        "book_as_of": None,
    }


def _fetch_book_tencent(symbol: str) -> dict[str, Any]:
    """Last-session five-level book from Tencent (survives after hours)."""
    prefix = market_prefix(symbol)
    code = f"{prefix}{symbol}"
    r = _session().get(
        "https://qt.gtimg.cn/q=" + code,
        timeout=15,
        headers={"Referer": "https://gu.qq.com/"},
    )
    r.raise_for_status()
    text = r.content.decode("gbk", errors="replace").strip()
    # v_sh600519="1~名~代码~现价~..."
    if "~" not in text:
        raise RuntimeError("腾讯行情为空")
    body = text.split("=", 1)[-1].strip().strip(";").strip('"')
    p = body.split("~")
    if len(p) < 33:
        raise RuntimeError("腾讯行情字段不足")

    # 9..18 buy1..buy5 (price,vol)*5; 19..28 sell1..sell5
    bids = [
        _level(p[9], p[10]),
        _level(p[11], p[12]),
        _level(p[13], p[14]),
        _level(p[15], p[16]),
        _level(p[17], p[18]),
    ]
    asks_sell1_to_5 = [
        _level(p[19], p[20]),
        _level(p[21], p[22]),
        _level(p[23], p[24]),
        _level(p[25], p[26]),
        _level(p[27], p[28]),
    ]
    # UI expects 卖五→卖一
    asks = list(reversed(asks_sell1_to_5))

    as_of_raw = p[30] if len(p) > 30 else ""
    book_as_of = None
    if len(as_of_raw) >= 14 and as_of_raw.isdigit():
        book_as_of = (
            f"{as_of_raw[0:4]}-{as_of_raw[4:6]}-{as_of_raw[6:8]} "
            f"{as_of_raw[8:10]}:{as_of_raw[10:12]}:{as_of_raw[12:14]}"
        )

    return {
        "symbol": symbol,
        "name": p[1] or symbol,
        "price": _num(p[3]),
        "change": _num(p[31]) if len(p) > 31 else None,
        "change_pct": _num(p[32]) if len(p) > 32 else None,
        "open": _num(p[5]),
        "high": _num(p[33]) if len(p) > 33 else None,
        "low": _num(p[34]) if len(p) > 34 else None,
        "pre_close": _num(p[4]),
        "avg_price": None,
        "volume": _num(p[6]),
        "amount": None,
        "turnover": _num(p[38]) if len(p) > 38 else None,
        "volume_ratio": _num(p[49]) if len(p) > 49 else None,
        "limit_up": _num(p[47]) if len(p) > 47 else None,
        "limit_down": _num(p[48]) if len(p) > 48 else None,
        "outer_vol": _num(p[7]),
        "inner_vol": _num(p[8]),
        "asks": asks,
        "bids": bids,
        "book_source": "tencent",
        "book_as_of": book_as_of,
    }


def _fetch_ticks(symbol: str, limit: int = 40) -> list[dict[str, Any]]:
    r = _session().get(
        "https://push2delay.eastmoney.com/api/qt/stock/details/get",
        params={
            "fields1": "f1,f2,f3,f4",
            "fields2": "f51,f52,f53,f54,f55",
            "mpi": "2000",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "pos": "-0",
            "secid": secid(symbol),
        },
        timeout=15,
    )
    r.raise_for_status()
    details = (r.json().get("data") or {}).get("details") or []
    side_map = {"1": "卖盘", "2": "买盘", "4": "中性盘"}
    ticks: list[dict[str, Any]] = []
    for row in details[-limit:]:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        side_code = parts[4] if len(parts) > 4 else ""
        ticks.append(
            {
                "time": parts[0],
                "price": _num(parts[1]),
                "volume": _num(parts[2]),
                "side": side_map.get(side_code, "—"),
                "side_code": side_code,
            }
        )
    ticks.reverse()
    return ticks


def get_last_quote(symbol: str) -> dict[str, Any]:
    """Lightweight last price + pre_close (no ticks / order book)."""
    symbol = normalize_symbol(symbol)
    _ = market_prefix(symbol)
    snap: dict[str, Any] | None = None
    err: str | None = None
    try:
        snap = _fetch_snapshot_em(symbol)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    if snap is None or snap.get("price") is None:
        try:
            snap = _fetch_book_tencent(symbol)
            err = None
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            err = f"{err}; {detail}" if err else detail
            snap = snap or {"symbol": symbol, "name": symbol}
    price = _num(snap.get("price")) if snap else None
    pre_close = _num(snap.get("pre_close")) if snap else None
    change = _num(snap.get("change")) if snap else None
    change_pct = _num(snap.get("change_pct")) if snap else None
    # Prefer computing ratio from price/pre_close for consistent downstream math.
    day_chg_pct: float | None = None
    if price is not None and pre_close is not None and pre_close > 0:
        day_chg_pct = price / pre_close - 1.0
        if change is None:
            change = price - pre_close
    return {
        "symbol": str((snap or {}).get("symbol") or symbol),
        "name": str((snap or {}).get("name") or symbol),
        "price": price,
        "pre_close": pre_close,
        "change": change,
        "change_pct": change_pct,
        "day_chg_pct": day_chg_pct,
        "error": err,
    }


def get_quote(symbol: str, tick_limit: int = 40) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    _ = market_prefix(symbol)
    session = trading_session()

    snap_err: str | None = None
    tick_err: str | None = None
    book_note: str | None = None
    snapshot: dict[str, Any] = {
        "symbol": symbol,
        "name": symbol,
        "asks": [_level(None, None) for _ in range(5)],
        "bids": [_level(None, None) for _ in range(5)],
        "book_source": None,
        "book_as_of": None,
    }
    ticks: list[dict[str, Any]] = []
    sources_used = ["eastmoney.push2delay"]

    try:
        snapshot = _fetch_snapshot_em(symbol)
    except Exception as exc:
        snap_err = f"{type(exc).__name__}: {exc}"

    if not _book_has_levels(snapshot.get("asks") or [], snapshot.get("bids") or []):
        try:
            tx = _fetch_book_tencent(symbol)
            # Keep EM quote metrics when present; always take TX book
            if snap_err or not snapshot.get("price"):
                snapshot = {**snapshot, **tx}
            else:
                snapshot["asks"] = tx["asks"]
                snapshot["bids"] = tx["bids"]
                snapshot["book_source"] = tx["book_source"]
                snapshot["book_as_of"] = tx["book_as_of"]
                if not snapshot.get("outer_vol"):
                    snapshot["outer_vol"] = tx.get("outer_vol")
                if not snapshot.get("inner_vol"):
                    snapshot["inner_vol"] = tx.get("inner_vol")
            sources_used.append("tencent.qt")
            book_note = "非交易时段展示上一交易时段收盘五档（腾讯）"
            snap_err = None
        except Exception as exc:
            if snap_err:
                snap_err = f"{snap_err}; tencent: {exc}"
            else:
                snap_err = f"tencent: {exc}"

    try:
        ticks = _fetch_ticks(symbol, limit=max(10, min(tick_limit, 100)))
    except Exception as exc:
        tick_err = f"{type(exc).__name__}: {exc}"

    if snap_err and tick_err and not _book_has_levels(
        snapshot.get("asks") or [], snapshot.get("bids") or []
    ):
        raise RuntimeError(f"盘口获取失败: {snap_err}; {tick_err}")

    has_book = _book_has_levels(
        snapshot.get("asks") or [], snapshot.get("bids") or []
    )

    return {
        "symbol": snapshot.get("symbol") or symbol,
        "name": snapshot.get("name") or symbol,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "+".join(sources_used),
        "session": session,
        "book_available": has_book,
        "book_live": bool(session["is_trading"] and has_book and snapshot.get("book_source") == "eastmoney"),
        "book_note": book_note,
        "snapshot": snapshot,
        "ticks": ticks,
        "errors": {
            "snapshot": snap_err,
            "ticks": tick_err,
        },
    }
