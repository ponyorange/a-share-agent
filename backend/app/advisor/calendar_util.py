"""Trading calendar helpers (A-share weekdays; optional akshare enrichment)."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def _today() -> date:
    return date.today()


@lru_cache(maxsize=1)
def _trade_dates_set() -> set[str] | None:
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            return None
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        return {str(x)[:10].replace("/", "-") for x in df[col].tolist()}
    except Exception:
        return None


def is_trading_day(d: date | str | None = None) -> bool:
    if d is None:
        d = _today()
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    if d.weekday() >= 5:
        return False
    known = _trade_dates_set()
    if known is None:
        return True  # weekday fallback
    return d.isoformat() in known


def last_trading_day(on_or_before: date | None = None) -> str:
    d = on_or_before or _today()
    for _ in range(15):
        if is_trading_day(d):
            return d.isoformat()
        d -= timedelta(days=1)
    return (on_or_before or _today()).isoformat()


def next_trading_day(after: date | None = None) -> str:
    d = (after or _today()) + timedelta(days=1)
    for _ in range(15):
        if is_trading_day(d):
            return d.isoformat()
        d += timedelta(days=1)
    return d.isoformat()


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None
