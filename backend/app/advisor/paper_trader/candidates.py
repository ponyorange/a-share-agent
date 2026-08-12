"""Build candidate universe and tag buy/sell/neutral directions."""

from __future__ import annotations

from typing import Any, Literal

from .defaults import default_paper_trader_config

Direction = Literal["buy", "sell", "neutral"]


def _recommendation_rows(
    user_id: str, *, allow_live: bool = False
) -> list[dict[str, Any]]:
    """Prefer archived snapshot; optional live get_recommendations fallback."""
    try:
        from ..snapshots import effective_rec_date, snapshot_as_recommendations

        day = effective_rec_date()
        snap = snapshot_as_recommendations(day, user_id=user_id)
        if snap and (snap.get("items") or []):
            return [r for r in (snap.get("items") or []) if isinstance(r, dict)]
    except Exception:
        pass
    if not allow_live:
        return []
    try:
        from ..service import get_recommendations

        recs = get_recommendations(user_id=user_id)
        return [r for r in (recs.get("items") or []) if isinstance(r, dict)]
    except Exception:
        return []


def _recommendation_symbols(user_id: str, *, allow_live: bool = False) -> list[str]:
    out: list[str] = []
    for row in _recommendation_rows(user_id, allow_live=allow_live):
        sym = str(row.get("symbol") or "").strip()
        if sym:
            out.append(sym)
    return out


def _watchlist_symbols(user_id: str) -> list[str]:
    from ..watchlist import load_watchlist

    wl = load_watchlist(user_id)
    out: list[str] = []
    for row in wl.get("items") or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").strip()
        if sym:
            out.append(sym)
    return out


def _paper_positions(user_id: str) -> list[dict[str, Any]]:
    from ..paper import get_account

    acc = get_account(user_id, mark_to_market=False)
    return list(acc.get("positions") or [])


def _rule_score(symbol: str) -> float | None:
    try:
        from ..service import get_advice

        adv = get_advice(symbol)
        if adv.get("score") is None:
            return None
        return float(adv["score"])
    except Exception:
        return None


def _graph_action(symbol: str) -> str | None:
    try:
        from ..signal_graph.service import get_signal_view

        view = get_signal_view(symbol)
        action = str(view.get("action") or "").strip().upper()
        return action or None
    except Exception:
        return None


def _buy_sell_thresholds() -> tuple[float, float]:
    from ..config_loader import load_config

    cfg = load_config()
    buy = float(cfg.get("buy_threshold", 0.55))
    sell = float(cfg.get("sell_threshold", 0.35))
    return buy, sell


def _tag_direction(
    *,
    graph_action: str | None,
    rule_score: float | None,
    buy_th: float,
    sell_th: float,
) -> Direction:
    g = (graph_action or "").upper()
    if g == "BUY":
        return "buy"
    if g == "SELL":
        return "sell"
    if rule_score is not None:
        if rule_score >= buy_th:
            return "buy"
        if rule_score <= sell_th:
            return "sell"
    return "neutral"


def _assemble(
    *,
    user_id: str,
    lim: int,
    allow_live_recs: bool,
    enrich: bool,
) -> list[dict[str, Any]]:
    rec_rows = _recommendation_rows(user_id, allow_live=allow_live_recs)
    score_by_sym: dict[str, float] = {}
    name_by_sym: dict[str, str] = {}
    rec_set: set[str] = set()
    for row in rec_rows:
        sym = str(row.get("symbol") or "").strip()
        if not sym:
            continue
        rec_set.add(sym)
        if row.get("name"):
            name_by_sym[sym] = str(row.get("name"))
        if row.get("score") is not None:
            try:
                score_by_sym[sym] = float(row["score"])
            except (TypeError, ValueError):
                pass

    watch_set = set(_watchlist_symbols(user_id))
    positions = _paper_positions(user_id)
    held: dict[str, dict[str, Any]] = {}
    for p in positions:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").strip()
        if not sym:
            continue
        held[sym] = p
        if p.get("name"):
            name_by_sym.setdefault(sym, str(p.get("name")))

    symbols = sorted(rec_set | watch_set | set(held.keys()))
    buy_th, sell_th = _buy_sell_thresholds()

    rows: list[dict[str, Any]] = []
    for sym in symbols:
        if enrich:
            score = score_by_sym.get(sym)
            if score is None:
                score = _rule_score(sym)
            gact = _graph_action(sym)
        else:
            score = score_by_sym.get(sym)
            gact = None
        direction = _tag_direction(
            graph_action=gact,
            rule_score=score,
            buy_th=buy_th,
            sell_th=sell_th,
        )
        pos = held.get(sym) or {}
        try:
            held_qty = float(pos.get("qty") or 0)
        except (TypeError, ValueError):
            held_qty = 0.0
        rows.append(
            {
                "symbol": sym,
                "name": name_by_sym.get(sym) or pos.get("name"),
                "direction": direction,
                "rule_score": score,
                "graph_action": gact,
                "in_watchlist": sym in watch_set,
                "in_recommendations": sym in rec_set,
                "held_qty": held_qty,
            }
        )

    def sort_key(r: dict[str, Any]) -> tuple:
        held_first = 0 if float(r.get("held_qty") or 0) > 0 else 1
        dir_rank = 0 if r.get("direction") in ("buy", "sell") else 1
        score = r.get("rule_score")
        try:
            dist = -abs(float(score) - 0.5) if score is not None else 0.0
        except (TypeError, ValueError):
            dist = 0.0
        return (held_first, dir_rank, dist, r["symbol"])

    rows.sort(key=sort_key)
    held_rows = [r for r in rows if float(r.get("held_qty") or 0) > 0]
    other = [r for r in rows if float(r.get("held_qty") or 0) <= 0]
    room = max(0, lim - len(held_rows))
    return held_rows + other[:room]


def build_candidates(
    user_id: str,
    *,
    limit: int | None = None,
    light: bool = False,
) -> list[dict[str, Any]]:
    """Build candidates.

    light=True (cockpit): snapshot + watchlist + positions only; no live
    recommendation rebuild / per-symbol advice.
    light=False (worker cycle): may fall back to live recommendations and
    enrich with advice/graph signals.
    """
    cfg = default_paper_trader_config()
    lim = int(limit if limit is not None else cfg.get("candidate_limit") or 40)
    lim = max(1, lim)
    return _assemble(
        user_id=user_id,
        lim=lim,
        allow_live_recs=not light,
        enrich=not light,
    )


def build_candidates_light(
    user_id: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    return build_candidates(user_id, limit=limit, light=True)
