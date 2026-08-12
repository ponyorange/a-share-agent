"""Build candidate universe and tag buy/sell/neutral directions."""

from __future__ import annotations

from typing import Any, Literal

from .defaults import default_paper_trader_config

Direction = Literal["buy", "sell", "neutral"]


def _recommendation_symbols(user_id: str) -> list[str]:
    from ..service import get_recommendations

    recs = get_recommendations(user_id=user_id)
    out: list[str] = []
    for row in recs.get("items") or []:
        if not isinstance(row, dict):
            continue
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


def build_candidates(user_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    cfg = default_paper_trader_config()
    lim = int(limit if limit is not None else cfg.get("candidate_limit") or 40)
    lim = max(1, lim)

    rec_set = set(_recommendation_symbols(user_id))
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

    symbols = sorted(rec_set | watch_set | set(held.keys()))
    buy_th, sell_th = _buy_sell_thresholds()

    rows: list[dict[str, Any]] = []
    for sym in symbols:
        score = _rule_score(sym)
        gact = _graph_action(sym)
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
                "name": pos.get("name"),
                "direction": direction,
                "rule_score": score,
                "graph_action": gact,
                "in_watchlist": sym in watch_set,
                "in_recommendations": sym in rec_set,
                "held_qty": held_qty,
            }
        )

    # Prefer held, then directional, then |score-0.5|
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
    # Always keep held even if over limit: partition
    held_rows = [r for r in rows if float(r.get("held_qty") or 0) > 0]
    other = [r for r in rows if float(r.get("held_qty") or 0) <= 0]
    room = max(0, lim - len(held_rows))
    return held_rows + other[:room]
