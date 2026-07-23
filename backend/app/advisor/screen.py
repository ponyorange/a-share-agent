"""Coarse screen from spot fields (no daily kline)."""

from __future__ import annotations

import math
from typing import Any

from .config_loader import load_config
from .portfolio import load_portfolio
from .universe import BoardId, classify_symbol


def _clip01(v: float) -> float:
    return float(max(0.0, min(1.0, v)))


def coarse_score(item: dict[str, Any]) -> float:
    """Cheap 0~1 score from spot: pct_chg / 量比 / 换手 / log成交额."""
    cfg = load_config()
    rec = cfg.get("recommendations") or {}
    weights = dict(
        rec.get("coarse_weights")
        or {
            "pct_chg": 0.35,
            "volume_ratio": 0.25,
            "turnover": 0.15,
            "amount": 0.25,
        }
    )

    pct = item.get("pct_chg")
    vol_ratio = item.get("volume_ratio")
    turnover = item.get("turnover")
    amount = item.get("amount") or 0.0

    # 涨跌幅：-5%~+8% 映射到 0~1，温和上涨更好
    if pct is None or (isinstance(pct, float) and pct != pct):
        n_pct = 0.5
    else:
        n_pct = _clip01((float(pct) + 5.0) / 13.0)

    # 量比：0.5~3
    if vol_ratio is None or (isinstance(vol_ratio, float) and vol_ratio != vol_ratio):
        n_vol = 0.5
    else:
        n_vol = _clip01((float(vol_ratio) - 0.5) / 2.5)

    # 换手：0.5%~8%
    if turnover is None or (isinstance(turnover, float) and turnover != turnover):
        n_turn = 0.5
    else:
        n_turn = _clip01((float(turnover) - 0.5) / 7.5)

    # 成交额：log10 映射（约 1e7~1e10）
    try:
        n_amt = _clip01((math.log10(max(float(amount), 1.0)) - 7.0) / 3.0)
    except (TypeError, ValueError):
        n_amt = 0.5

    parts = {
        "pct_chg": n_pct,
        "volume_ratio": n_vol,
        "turnover": n_turn,
        "amount": n_amt,
    }
    wsum = sum(float(weights.get(k, 0)) for k in parts) or 1.0
    score = sum(parts[k] * float(weights.get(k, 0)) for k in parts) / wsum
    return round(float(score), 4)


def select_for_precise(
    pool: list[dict[str, Any]],
    board: BoardId,
    precise_limit: int,
) -> list[dict[str, Any]]:
    """Rank by coarse_score; always keep portfolio names on this board."""
    force: set[str] = set()
    for pos in load_portfolio().get("positions") or []:
        sym = str(pos.get("symbol") or "")
        if classify_symbol(sym) == board:
            force.add(sym)

    ranked: list[dict[str, Any]] = []
    for raw in pool:
        item = dict(raw)
        item["coarse_score"] = coarse_score(item)
        ranked.append(item)
    ranked.sort(key=lambda x: float(x.get("coarse_score") or 0), reverse=True)

    picked: list[dict[str, Any]] = []
    seen: set[str] = set()

    # portfolio first (still get coarse_score if in pool)
    by_sym = {r["symbol"]: r for r in ranked}
    for sym in force:
        if sym in by_sym and sym not in seen:
            picked.append(by_sym[sym])
            seen.add(sym)
        elif sym not in seen:
            picked.append(
                {
                    "symbol": sym,
                    "name": sym,
                    "amount": 0.0,
                    "board": board,
                    "pct_chg": None,
                    "volume_ratio": None,
                    "turnover": None,
                    "coarse_score": 0.5,
                    "forced": True,
                }
            )
            seen.add(sym)

    for item in ranked:
        if len(picked) >= precise_limit:
            break
        if item["symbol"] in seen:
            continue
        picked.append(item)
        seen.add(item["symbol"])

    return picked
