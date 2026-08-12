"""Hard risk gates for paper trader intents (LLM cannot override)."""

from __future__ import annotations

from typing import Any


def should_halt_for_daily_loss(
    *,
    equity: float,
    equity_day_open: float | None,
    max_daily_loss_pct: float,
) -> bool:
    if equity_day_open is None:
        return False
    try:
        open_eq = float(equity_day_open)
        cur = float(equity)
        lim = float(max_daily_loss_pct)
    except (TypeError, ValueError):
        return False
    if open_eq <= 0 or lim < 0:
        return False
    return (open_eq - cur) / open_eq >= lim


def is_near_limit_board(
    quote: dict[str, Any],
    *,
    board: str | None = None,
    symbol: str | None = None,
) -> bool:
    if not isinstance(quote, dict):
        return False
    if quote.get("limit_up") or quote.get("limit_down"):
        return True
    try:
        price = float(quote["price"]) if quote.get("price") is not None else None
    except (TypeError, ValueError):
        price = None
    if price is not None and price > 0:
        for key in ("limit_up_price", "limit_down_price"):
            raw = quote.get(key)
            if raw is None:
                continue
            try:
                lim = float(raw)
            except (TypeError, ValueError):
                continue
            if lim > 0 and abs(price - lim) / lim < 0.005:
                return True
    try:
        chg = float(quote.get("day_chg_pct"))
    except (TypeError, ValueError):
        return False
    thr = 0.095
    board_v = (board or "").strip()
    sym = (symbol or str(quote.get("symbol") or "")).strip()
    if board_v in ("chiNext", "star", "cyb", "kcb") or sym.startswith(
        ("300", "688", "301")
    ):
        thr = 0.195
    return abs(chg) >= thr


def _pos_map(account: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in account.get("positions") or []:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").strip()
        if not sym:
            continue
        out[sym] = dict(p)
    return out


def _qty_of(pos: dict[str, Any] | None) -> float:
    if not pos:
        return 0.0
    try:
        return float(pos.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


def _available_qty(pos: dict[str, Any] | None) -> float:
    if not pos:
        return 0.0
    if pos.get("available_qty") is not None:
        try:
            return float(pos["available_qty"])
        except (TypeError, ValueError):
            pass
    return _qty_of(pos)


def filter_intents(
    intents: list[dict[str, Any]],
    *,
    account: dict[str, Any],
    quotes_by_symbol: dict[str, dict[str, Any]],
    risk: dict[str, Any],
    trades_today: int,
    equity_day_open: float | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (allowed, blocked). Simulates fills in-order for exposure checks."""
    del equity_day_open  # used at cycle level for halt; kept for API symmetry
    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    try:
        equity = float(account.get("equity") or 0)
    except (TypeError, ValueError):
        equity = 0.0
    try:
        cash = float(account.get("cash") or 0)
    except (TypeError, ValueError):
        cash = 0.0
    positions = _pos_map(account)

    max_single = float(risk.get("max_single_position") or 0.25)
    max_total = float(risk.get("max_total_exposure") or 0.90)
    max_positions = int(risk.get("max_positions") or 10)
    max_trades = int(risk.get("max_trades_per_day") or 30)
    lot = int(risk.get("lot_size") or 100)
    block_limit = bool(risk.get("block_limit_board", True))

    trades_left = max(0, max_trades - int(trades_today or 0))

    def market_value() -> float:
        total = 0.0
        for sym, pos in positions.items():
            q = quotes_by_symbol.get(sym) or {}
            px = q.get("price")
            if px is None:
                px = pos.get("last") or pos.get("cost") or 0
            try:
                total += _qty_of(pos) * float(px)
            except (TypeError, ValueError):
                continue
        return total

    for raw in intents or []:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip()
        side = str(raw.get("side") or "").strip().lower()
        try:
            qty = float(raw.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        reason = raw.get("reason")
        base = {"symbol": symbol, "side": side, "qty": qty}
        if reason is not None:
            base["reason"] = reason

        if side not in ("buy", "sell") or not symbol:
            blocked.append({**base, "reason": "invalid_intent"})
            continue
        if qty <= 0:
            blocked.append({**base, "reason": "invalid_qty"})
            continue
        if trades_left <= 0:
            blocked.append({**base, "reason": "max_trades_per_day"})
            continue

        quote = quotes_by_symbol.get(symbol) or {}
        if block_limit and is_near_limit_board(quote, symbol=symbol):
            blocked.append({**base, "reason": "near_limit_board"})
            continue

        try:
            price = float(quote.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            blocked.append({**base, "reason": "no_price"})
            continue

        if side == "buy":
            lots = int(qty // lot) * lot
            if lots < lot:
                blocked.append({**base, "reason": "lot_size"})
                continue
            qty = float(lots)
            base["qty"] = qty
            cost = qty * price
            if cost > cash + 1e-6:
                blocked.append({**base, "reason": "insufficient_cash"})
                continue
            pos = positions.get(symbol)
            new_qty = _qty_of(pos) + qty
            single_mv = new_qty * price
            if equity > 0 and single_mv / equity > max_single + 1e-9:
                blocked.append({**base, "reason": "max_single_position"})
                continue
            held_count = sum(1 for p in positions.values() if _qty_of(p) > 0)
            opening_new = _qty_of(pos) <= 0
            if opening_new and held_count >= max_positions:
                blocked.append({**base, "reason": "max_positions"})
                continue
            # tentative apply
            cash -= cost
            if pos is None:
                positions[symbol] = {"symbol": symbol, "qty": qty, "last": price}
            else:
                positions[symbol] = {**pos, "qty": new_qty, "last": price}
            mv = market_value()
            # restore then decide on total exposure using projected equity ~ cash+mv
            # Recompute with current cash after buy
            proj_equity = cash + mv
            if proj_equity > 0 and mv / proj_equity > max_total + 1e-9:
                # rollback
                cash += cost
                if opening_new:
                    positions.pop(symbol, None)
                else:
                    positions[symbol] = pos  # type: ignore[assignment]
                blocked.append({**base, "reason": "max_total_exposure"})
                continue
            allowed.append(dict(base))
            trades_left -= 1
            continue

        # sell
        pos = positions.get(symbol)
        avail = _available_qty(pos)
        if qty > avail + 1e-9:
            blocked.append({**base, "reason": "t_plus_one"})
            continue
        # apply
        new_qty = _qty_of(pos) - qty
        proceeds = qty * price
        cash += proceeds
        if new_qty <= 1e-9:
            positions.pop(symbol, None)
        else:
            positions[symbol] = {**(pos or {}), "qty": new_qty, "last": price}
        allowed.append(dict(base))
        trades_left -= 1

    return allowed, blocked
