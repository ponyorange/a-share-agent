"""Multi-factor scoring (V2): tech / flow / sector / value + market scale."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np

from .config_loader import load_config
from .features import FeatureResult

Action = Literal["buy", "watch", "hold", "add", "sell"]

# Map raw tech factor → [0,1] (higher = more bullish)
_FACTOR_NORMALIZE = {
    "mom_1": lambda x: _clip01((x + 0.02) / 0.05),
    "mom_5": lambda x: _clip01((x + 0.03) / 0.08),
    "mom_10": lambda x: _clip01((x + 0.04) / 0.10),
    "mom_20": lambda x: _clip01((x + 0.05) / 0.14),
    "rs_300": lambda x: _clip01((x + 0.03) / 0.08),
    "ma20_bias": lambda x: _clip01((x + 0.04) / 0.10),
    "vol_z": lambda x: _clip01((x + 1.0) / 3.0),
    "vol_ratio": lambda x: _clip01((x - 0.6) / 1.4),
    "low_vol": lambda x: _clip01(x),
}

_DEFAULT_LAYER = {"tech": 0.40, "flow": 0.25, "sector": 0.20, "value": 0.15}
_DEFAULT_MARKET_SCALE = {"base": 0.85, "scale": 0.30}


def _clip01(v: float | None) -> float:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 0.5
    return float(max(0.0, min(1.0, v)))


def factor_contributions(factors: dict[str, float]) -> list[dict[str, Any]]:
    """Tech-layer factor rows (for UI / rationale)."""
    cfg = load_config()
    weights: dict[str, float] = dict(cfg.get("weights") or {})
    rows: list[dict[str, Any]] = []
    for key, weight in weights.items():
        raw = factors.get(key)
        if raw is None or (isinstance(raw, float) and np.isnan(raw)):
            raw_f = float("nan")
            norm = 0.5
        else:
            raw_f = float(raw)
            fn = _FACTOR_NORMALIZE.get(key, lambda x: 0.5)
            norm = fn(raw_f)
        contrib = norm * float(weight)
        rows.append(
            {
                "name": key,
                "raw": None if np.isnan(raw_f) else round(raw_f, 6),
                "normalized": round(norm, 4),
                "weight": float(weight),
                "contribution": round(contrib, 4),
                "layer": "tech",
            }
        )
    return rows


def tech_score_from_factors(factors: dict[str, float]) -> tuple[float, list[dict[str, Any]]]:
    contribs = factor_contributions(factors)
    weight_sum = sum(c["weight"] for c in contribs) or 1.0
    score = sum(c["contribution"] for c in contribs) / weight_sum
    return _clip01(score), contribs


def score_features(
    feat: FeatureResult,
    *,
    context_scores: dict[str, float] | None = None,
) -> tuple[float, list[dict[str, Any]], dict[str, Any]]:
    """Return (final_score, tech_contribs, layer_detail).

    context_scores may include flow_score / sector_score / value_score / market_score.
    Missing layers default to 0.5 (neutral).
    """
    cfg = load_config()
    tech, contribs = tech_score_from_factors(feat.factors)
    ctx = context_scores or {}
    flow = _clip01(ctx.get("flow_score"))
    sector = _clip01(ctx.get("sector_score"))
    value = _clip01(ctx.get("value_score"))
    market = _clip01(ctx.get("market_score"))

    layers = dict(_DEFAULT_LAYER)
    layers.update({k: float(v) for k, v in (cfg.get("layer_weights") or {}).items()})
    wsum = sum(layers.values()) or 1.0
    stock = (
        layers.get("tech", 0.4) * tech
        + layers.get("flow", 0.25) * flow
        + layers.get("sector", 0.2) * sector
        + layers.get("value", 0.15) * value
    ) / wsum

    ms = dict(_DEFAULT_MARKET_SCALE)
    ms.update({k: float(v) for k, v in (cfg.get("market_scale") or {}).items()})
    final = stock * (ms.get("base", 0.85) + ms.get("scale", 0.30) * market)
    final = _clip01(final)

    ann_vol = feat.factors.get("ann_vol")
    thr = float(cfg.get("high_vol_ann_threshold", 0.45))
    penalty = float(cfg.get("high_vol_penalty", 0.75))
    vol_penalized = False
    if ann_vol is not None and not np.isnan(ann_vol) and ann_vol > thr:
        final = _clip01(final * penalty)
        vol_penalized = True

    detail = {
        "tech_score": round(tech, 4),
        "flow_score": round(flow, 4),
        "sector_score": round(sector, 4),
        "value_score": round(value, 4),
        "market_score": round(market, 4),
        "stock_score": round(_clip01(stock), 4),
        "layer_weights": {k: round(float(v) / wsum, 4) for k, v in layers.items()},
        "market_scale": ms,
        "high_vol_penalty_applied": vol_penalized,
    }
    return round(float(final), 4), contribs, detail


def decide_action(score: float, has_position: bool) -> Action:
    cfg = load_config()
    buy_th = float(cfg.get("buy_threshold", 0.55))
    add_th = float(cfg.get("add_threshold", 0.65))
    sell_th = float(cfg.get("sell_threshold", 0.35))

    if has_position:
        if score >= add_th:
            return "add"
        if score <= sell_th:
            return "sell"
        return "hold"
    if score >= buy_th:
        return "buy"
    return "watch"


def action_label(action: Action, has_position: bool) -> str:
    labels = {
        "buy": "值得关注 / 可买",
        "watch": "观望",
        "hold": "持有",
        "add": "加仓",
        "sell": "卖出 / 减仓",
    }
    return labels.get(action, action)


def build_rationale(
    action: Action,
    score: float,
    contribs: list[dict[str, Any]],
    has_position: bool,
    layer_detail: dict[str, Any] | None = None,
) -> str:
    parts = [f"综合分 {score:.2f}（{action_label(action, has_position)}）"]
    if layer_detail:
        parts.append(
            "分层："
            f"tech={layer_detail.get('tech_score')} "
            f"flow={layer_detail.get('flow_score')} "
            f"sector={layer_detail.get('sector_score')} "
            f"value={layer_detail.get('value_score')} "
            f"market={layer_detail.get('market_score')}"
        )
    top = sorted(contribs, key=lambda c: c["contribution"], reverse=True)[:3]
    if top:
        detail = "、".join(f"{c['name']}={c['normalized']:.2f}" for c in top)
        parts.append(f"tech主因子：{detail}")
    if has_position:
        parts.append("已结合当前持仓给出动作建议。")
    else:
        parts.append("当前无持仓：按是否达到买入阈值判断。")
    return " ".join(parts)
