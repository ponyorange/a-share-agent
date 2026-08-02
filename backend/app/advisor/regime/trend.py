from __future__ import annotations

from typing import Any

_DEFAULT_TREND_RULES = {
    "uptrend_breadth_min": 0.55,
    "uptrend_drawdown_max": 0.12,
    "downtrend_drawdown_min": 0.18,
}


def _regime_cfg(cfg: dict | None) -> dict:
    if cfg is not None:
        return cfg
    from ..config_loader import load_config

    return load_config().get("regime") or {}


def _trend_rules(cfg: dict | None) -> dict[str, float]:
    regime = _regime_cfg(cfg)
    raw = regime.get("trend_rules") or {}
    merged = {**_DEFAULT_TREND_RULES, **raw}
    return {k: float(merged[k]) for k in _DEFAULT_TREND_RULES}


def classify_trend(features: dict, cfg: dict | None = None) -> dict[str, Any]:
    rules = _trend_rules(cfg)
    ma_stack = str(features.get("ma_stack") or "mixed")
    drawdown = float(features.get("drawdown_from_high") or 0.0)
    breadth = float(features.get("breadth") or 0.0)
    volume_vs_ma20 = float(features.get("volume_vs_ma20") or 1.0)

    breadth_min = rules["uptrend_breadth_min"]
    uptrend_dd_max = rules["uptrend_drawdown_max"]
    downtrend_dd_min = rules["downtrend_drawdown_min"]

    evidence: list[dict[str, Any]] = [
        {"key": "ma_stack", "value": ma_stack, "note": "指数相对 MA 排列"},
        {
            "key": "drawdown_from_high",
            "value": f"{drawdown:.4f}",
            "note": "距阶段高点回撤",
        },
        {"key": "breadth", "value": f"{breadth:.4f}", "note": "上涨家数占比"},
        {
            "key": "volume_vs_ma20",
            "value": f"{volume_vs_ma20:.4f}",
            "note": "成交额相对 20 日均",
        },
    ]

    if ma_stack == "below" or drawdown >= downtrend_dd_min:
        trend_regime = "downtrend"
        if ma_stack == "below":
            note = "均线空头排列"
        else:
            note = f"回撤 {drawdown:.2%} ≥ 阈值 {downtrend_dd_min:.2%}"
    elif (
        ma_stack == "above"
        and breadth >= breadth_min
        and drawdown <= uptrend_dd_max
    ):
        trend_regime = "uptrend"
        note = "均线多头且宽度与回撤满足上升趋势条件"
    else:
        trend_regime = "range"
        note = "未满足明确上升或下降趋势条件"

    evidence.append({"key": "trend_regime", "value": trend_regime, "note": note})

    return {"trend_regime": trend_regime, "evidence": evidence}
