"""Home-page market helpers: hot industry sectors ranking."""

from __future__ import annotations

from typing import Any

from .calendar_util import last_trading_day
from .market_context import _session_ak, fetch_industry_strength_map


def _raw_industry_rows(trade_date: str | None = None) -> list[dict[str, Any]]:
    """Return [{name, change_pct}] for ranking. Monkeypatch in unit tests."""
    _ = trade_date
    try:
        ak = _session_ak()
        df = None
        for caller in (
            lambda: ak.stock_board_industry_name_em(),
            lambda: ak.stock_board_industry_spot_em(),
        ):
            try:
                df = caller()
                if df is not None and not df.empty:
                    break
            except Exception:
                continue
        if df is None or getattr(df, "empty", True):
            return []
        name_col = "板块名称" if "板块名称" in df.columns else df.columns[0]
        pct_col = None
        for c in ("涨跌幅", "涨跌幅%", "涨跌幅％"):
            if c in df.columns:
                pct_col = c
                break
        if pct_col is None:
            for c in df.columns:
                if "涨" in str(c):
                    pct_col = c
                    break
        if pct_col is None:
            return []
        rows: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            try:
                pct = float(str(row[pct_col]).replace("%", ""))
            except (TypeError, ValueError):
                continue
            name = str(row[name_col]).strip()
            if not name:
                continue
            rows.append({"name": name, "change_pct": pct})
        return rows
    except Exception:
        return []


def list_hot_sectors(top: int = 8, trade_date: str | None = None) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    n = max(1, min(int(top or 8), 30))
    rows = list(_raw_industry_rows(day) or [])
    if not rows:
        strength = fetch_industry_strength_map(day)
        by_name = dict(strength.get("by_name") or {})
        if not by_name:
            return {
                "trade_date": day,
                "ok": False,
                "source": strength.get("source") or "industry_strength",
                "items": [],
                "error": strength.get("error") or "empty",
            }
        ranked = sorted(by_name.items(), key=lambda kv: float(kv[1]), reverse=True)[:n]
        items = [
            {
                "rank": i + 1,
                "name": name,
                "change_pct": None,
                "strength": float(score),
            }
            for i, (name, score) in enumerate(ranked)
        ]
        return {
            "trade_date": day,
            "ok": True,
            "source": strength.get("source") or "industry_strength",
            "items": items,
        }

    pcts = [float(r["change_pct"]) for r in rows]
    order = sorted(range(len(pcts)), key=lambda i: pcts[i])
    rank_pct = {i: (j + 1) / len(pcts) for j, i in enumerate(order)}
    enriched = [
        {
            "name": str(r["name"]),
            "change_pct": float(r["change_pct"]),
            "strength": round(float(rank_pct[i]), 4),
        }
        for i, r in enumerate(rows)
    ]
    enriched.sort(key=lambda x: float(x["change_pct"]), reverse=True)
    items = [{"rank": i + 1, **row} for i, row in enumerate(enriched[:n])]
    return {
        "trade_date": day,
        "ok": True,
        "source": "akshare.stock_board_industry",
        "items": items,
    }
