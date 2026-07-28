"""Per-user stock watchlist stored in MongoDB."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import get_db
from ..kline import normalize_symbol

WATCHLIST_MAX = 100


def _lookup_name(symbol: str) -> str | None:
    """Resolve security name from live quote; None if unavailable."""
    try:
        from ..quote import get_last_quote

        q = get_last_quote(symbol)
        name = q.get("name")
        if name and str(name).strip() and str(name).strip() != symbol:
            return str(name).strip()
    except Exception:
        pass
    try:
        from ..kline import _fetch_name

        name = _fetch_name(symbol)
        if name and str(name).strip():
            return str(name).strip()
    except Exception:
        pass
    return None


def _best_name(item: dict[str, Any], quote: dict[str, Any] | None = None) -> str:
    sym = str(item.get("symbol") or "")
    candidates: list[Any] = []
    if quote:
        candidates.append(quote.get("name"))
    candidates.append(item.get("name"))
    for candidate in candidates:
        if not candidate:
            continue
        text = str(candidate).strip()
        if text and text != sym:
            return text
    return sym or "—"


def _save_items(user_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)
    body = {"user_id": user_id, "items": items, "updated_at": now}
    db.watchlists.update_one(
        {"user_id": user_id},
        {"$set": body},
        upsert=True,
    )
    return {"items": items}


def load_watchlist(user_id: str | None = None) -> dict[str, Any]:
    from .context import get_user_id

    uid = user_id if user_id is not None else get_user_id()
    if not uid:
        return {"items": []}
    db = get_db()
    doc = db.watchlists.find_one({"user_id": uid}) or {}
    return {"items": list(doc.get("items") or [])}


def add_symbol(
    user_id: str, symbol: str, name: str | None = None
) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    items = list(load_watchlist(user_id).get("items") or [])
    for row in items:
        if row.get("symbol") == sym:
            return {"items": items}
    if len(items) >= WATCHLIST_MAX:
        raise ValueError(f"收藏已达上限 {WATCHLIST_MAX} 只")
    resolved = (name or "").strip() or _lookup_name(sym) or sym
    items.append(
        {
            "symbol": sym,
            "name": resolved,
            "added_at": datetime.now(timezone.utc),
        }
    )
    return _save_items(user_id, items)


def remove_symbol(user_id: str, symbol: str) -> dict[str, Any]:
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        return load_watchlist(user_id)
    items = [
        row
        for row in (load_watchlist(user_id).get("items") or [])
        if row.get("symbol") != sym
    ]
    return _save_items(user_id, items)


def watchlist_status(user_id: str, symbols: list[str]) -> dict[str, Any]:
    owned = {
        str(row.get("symbol"))
        for row in (load_watchlist(user_id).get("items") or [])
        if row.get("symbol")
    }
    starred: dict[str, bool] = {}
    for raw in symbols:
        try:
            sym = normalize_symbol(raw)
        except ValueError:
            starred[str(raw).strip()] = False
            continue
        starred[sym] = sym in owned
    return {"starred": starred}


def watchlist_marks(user_id: str) -> dict[str, Any]:
    from ..quote import get_last_quote, trading_session

    items_in = list(load_watchlist(user_id).get("items") or [])
    session = trading_session()
    out_items: list[dict[str, Any]] = []
    name_dirty = False
    for pos in items_in:
        sym = str(pos.get("symbol") or "")
        try:
            quote = get_last_quote(sym)
        except Exception as exc:
            quote = {
                "symbol": sym,
                "name": pos.get("name") or sym,
                "price": None,
                "pre_close": None,
                "day_chg_pct": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        name = _best_name(pos, quote)
        if name and name != sym and pos.get("name") != name:
            pos["name"] = name
            name_dirty = True
        out_items.append(
            {
                "symbol": sym,
                "name": name,
                "added_at": pos.get("added_at"),
                "price": quote.get("price"),
                "pre_close": quote.get("pre_close"),
                "day_chg_pct": quote.get("day_chg_pct"),
                "error": quote.get("error"),
            }
        )
    if name_dirty and user_id:
        try:
            _save_items(user_id, items_in)
        except Exception:
            pass
    return {
        "session": session,
        "updated_at": session.get("now"),
        "count": len(out_items),
        "items": out_items,
    }
