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


def _normalize_positions(positions: list[Position] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in positions:
        if isinstance(p, Position):
            raw = p
            sym = normalize_symbol(raw.symbol)
            name = raw.name or sym
            qty = float(raw.qty)
            cost = float(raw.cost)
            note = raw.note
        else:
            try:
                sym = normalize_symbol(str(p.get("symbol") or ""))
            except ValueError:
                continue
            name = p.get("name") or sym
            qty = float(p.get("qty") or 0)
            cost = float(p.get("cost") or 0)
            note = p.get("note")
        if sym in seen:
            continue
        seen.add(sym)
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
    positions = _normalize_positions(payload.positions)
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
