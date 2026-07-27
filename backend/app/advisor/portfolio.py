"""Per-user portfolio stored in MongoDB."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ..db import get_db
from ..kline import normalize_symbol


class Position(BaseModel):
    symbol: str
    name: str | None = None
    qty: float = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    note: str | None = None


class PortfolioPayload(BaseModel):
    positions: list[Position] = Field(default_factory=list)


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


def _normalize_positions(positions: list[Position] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in positions:
        if isinstance(p, Position):
            raw = p
            sym = normalize_symbol(raw.symbol)
            name = raw.name or None
            qty = float(raw.qty)
            cost = float(raw.cost)
            note = raw.note
        else:
            try:
                sym = normalize_symbol(str(p.get("symbol") or ""))
            except ValueError:
                continue
            name = p.get("name") or None
            qty = float(p.get("qty") or 0)
            cost = float(p.get("cost") or 0)
            note = p.get("note")
        if sym in seen:
            continue
        seen.add(sym)
        if not name or str(name).strip() == sym:
            name = _lookup_name(sym) or name or sym
        out.append(
            {
                "symbol": sym,
                "name": name,
                "qty": qty,
                "cost": cost,
                "note": note,
            }
        )
    return out


def load_portfolio(user_id: str | None = None) -> dict[str, Any]:
    from .context import get_user_id

    uid = user_id if user_id is not None else get_user_id()
    if not uid:
        return {"positions": []}
    db = get_db()
    doc = db.portfolios.find_one({"user_id": uid}) or {}
    return {"positions": list(doc.get("positions") or [])}


def save_portfolio(payload: PortfolioPayload, user_id: str) -> dict[str, Any]:
    # Preserve notes already stored when client omits them.
    existing_by_sym = {
        str(p.get("symbol")): p for p in (load_portfolio(user_id).get("positions") or [])
    }
    positions = _normalize_positions(payload.positions)
    for row in positions:
        prev = existing_by_sym.get(str(row.get("symbol")))
        if prev and row.get("note") in (None, ""):
            row["note"] = prev.get("note")
        if prev and (not row.get("name") or row.get("name") == row.get("symbol")):
            if prev.get("name") and prev.get("name") != row.get("symbol"):
                row["name"] = prev.get("name")
    body = {"positions": positions}
    db = get_db()
    now = datetime.now(timezone.utc)
    db.portfolios.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "positions": positions, "updated_at": now}},
        upsert=True,
    )
    return body


def position_symbols(user_id: str | None = None) -> list[str]:
    return [p["symbol"] for p in load_portfolio(user_id)["positions"]]


def has_position(symbol: str, user_id: str | None = None) -> bool:
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        return False
    return any(p["symbol"] == sym for p in load_portfolio(user_id)["positions"])


def get_position(symbol: str, user_id: str | None = None) -> dict[str, Any] | None:
    try:
        sym = normalize_symbol(symbol)
    except ValueError:
        return None
    for p in load_portfolio(user_id)["positions"]:
        if p["symbol"] == sym:
            return p
    return None


def upsert_position(
    user_id: str,
    *,
    symbol: str,
    qty: float,
    cost: float,
    name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Insert or update one real position; returns updated portfolio."""
    sym = normalize_symbol(symbol)
    if qty < 0 or cost < 0:
        raise ValueError("数量与成本不能为负")
    port = load_portfolio(user_id)
    positions = list(port.get("positions") or [])
    found = False
    for i, p in enumerate(positions):
        if p.get("symbol") == sym:
            positions[i] = {
                "symbol": sym,
                "name": name or p.get("name") or sym,
                "qty": float(qty),
                "cost": float(cost),
                "note": note if note is not None else p.get("note"),
            }
            found = True
            break
    if not found:
        positions.append(
            {
                "symbol": sym,
                "name": name or sym,
                "qty": float(qty),
                "cost": float(cost),
                "note": note,
            }
        )
    return save_portfolio(PortfolioPayload(positions=positions), user_id)


def remove_position(user_id: str, symbol: str) -> dict[str, Any]:
    """Remove one real position by symbol; returns updated portfolio."""
    sym = normalize_symbol(symbol)
    port = load_portfolio(user_id)
    positions = [p for p in (port.get("positions") or []) if p.get("symbol") != sym]
    return save_portfolio(PortfolioPayload(positions=positions), user_id)


def _best_name(pos: dict[str, Any], quote: dict[str, Any] | None = None) -> str:
    """Prefer live quote name; ignore stored name when it is just the symbol."""
    sym = str(pos.get("symbol") or "")
    candidates = []
    if quote:
        candidates.append(quote.get("name"))
    candidates.append(pos.get("name"))
    for candidate in candidates:
        if not candidate:
            continue
        text = str(candidate).strip()
        if text and text != sym:
            return text
    return sym or "—"


def _build_mark_row(pos: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    qty = float(pos.get("qty") or 0)
    cost = float(pos.get("cost") or 0)
    price = quote.get("price")
    pre_close = quote.get("pre_close")
    day_chg_pct = quote.get("day_chg_pct")
    name = _best_name(pos, quote)
    market_value: float | None = None
    day_pnl: float | None = None
    position_pnl: float | None = None
    position_pnl_pct: float | None = None
    if price is not None and qty > 0:
        px = float(price)
        market_value = round(px * qty, 2)
        if cost > 0:
            position_pnl = round((px - cost) * qty, 2)
            position_pnl_pct = round(px / cost - 1.0, 6)
        if pre_close is not None and float(pre_close) > 0:
            day_pnl = round((px - float(pre_close)) * qty, 2)
        elif day_chg_pct is not None:
            day_pnl = round(float(day_chg_pct) * px * qty / (1.0 + float(day_chg_pct)), 2)
    return {
        "symbol": pos.get("symbol"),
        "name": name,
        "qty": qty,
        "cost": cost,
        "price": price,
        "pre_close": pre_close,
        "day_chg_pct": day_chg_pct,
        "day_pnl": day_pnl,
        "market_value": market_value,
        "position_pnl": position_pnl,
        "position_pnl_pct": position_pnl_pct,
        "weight": None,
        "error": quote.get("error"),
    }


def portfolio_marks(user_id: str) -> dict[str, Any]:
    """Mark-to-market snapshot for real portfolio holdings."""
    from ..quote import get_last_quote, trading_session

    positions = list(load_portfolio(user_id).get("positions") or [])
    session = trading_session()
    items: list[dict[str, Any]] = []
    name_dirty = False
    for pos in positions:
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
        row = _build_mark_row(pos, quote)
        # Backfill real name into stored portfolio when it was missing / equal to code.
        resolved = row.get("name")
        if resolved and resolved != sym and pos.get("name") != resolved:
            pos["name"] = resolved
            name_dirty = True
        items.append(row)

    if name_dirty and user_id:
        try:
            db = get_db()
            db.portfolios.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "positions": positions,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
        except Exception:
            pass

    total_mv = sum(
        float(x["market_value"])
        for x in items
        if x.get("market_value") is not None
    )
    total_cost = round(
        sum(float(x.get("qty") or 0) * float(x.get("cost") or 0) for x in items),
        2,
    )
    for row in items:
        mv = row.get("market_value")
        if mv is not None and total_mv > 0:
            row["weight"] = round(float(mv) / total_mv, 6)
        else:
            row["weight"] = None

    day_pnl_total = sum(
        float(x["day_pnl"]) for x in items if x.get("day_pnl") is not None
    )
    position_pnl_total = sum(
        float(x["position_pnl"])
        for x in items
        if x.get("position_pnl") is not None
    )
    # Prefer sum of per-leg PnL; fall back to MV − cost when quotes partially missing.
    if not any(x.get("position_pnl") is not None for x in items) and total_mv > 0:
        position_pnl_total = round(total_mv - total_cost, 2)
    total_return_pct: float | None = None
    if total_cost > 0:
        total_return_pct = round(position_pnl_total / total_cost, 6)
    return {
        "session": session,
        "updated_at": session.get("now"),
        "count": len(items),
        "total_market_value": round(total_mv, 2),
        "total_cost": total_cost,
        "total_day_pnl": round(day_pnl_total, 2),
        "total_position_pnl": round(position_pnl_total, 2),
        "total_return_pct": total_return_pct,
        "items": items,
    }
