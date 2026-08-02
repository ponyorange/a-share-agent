"""Apply market-regime gates to recommendation payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..config_loader import load_config
from ..scoring import action_label


def _regime_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if cfg is not None:
        return dict(cfg)
    return dict((load_config().get("regime") or {}))


def _summary(
    regime: dict[str, Any],
    *,
    override: bool,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    level = str(regime.get("gate_level") or "normal")
    if override and level == "risk_off":
        level = "defensive"
    position_cap = regime.get("position_cap")
    pool_policy = regime.get("pool_policy")
    if override:
        position_cap = (cfg.get("position_cap") or {}).get(level, position_cap)
        pool_policy = (cfg.get("pool_policy") or {}).get(level, pool_policy)
    return {
        "gate_level": level,
        "position_cap": position_cap,
        "pool_policy": pool_policy,
        "data_quality": regime.get("data_quality"),
        "override_applied": bool(override),
    }


def _gate_buy_action(item: dict[str, Any]) -> bool:
    action = str(item.get("action") or "").lower()
    if action == "buy":
        item["action"] = "watch"
        item["action_label"] = action_label("watch", False)
        return True
    if action == "add":
        item["action"] = "hold"
        item["action_label"] = action_label("hold", True)
        return True
    return False


def _defensive_relabel(item: dict[str, Any], cfg: dict[str, Any]) -> None:
    action = str(item.get("action") or "").lower()
    if action not in {"buy", "add"}:
        return
    try:
        score = float(item.get("score"))
    except (TypeError, ValueError):
        return
    buy_threshold = float(
        cfg.get("buy_threshold") or load_config().get("buy_threshold") or 0.55
    )
    boost = float(cfg.get("defensive_buy_threshold_boost") or 0.0)
    if score >= buy_threshold + boost:
        return
    if action == "buy":
        item["action"] = "watch"
        item["action_label"] = action_label("watch", False)
    else:
        item["action"] = "hold"
        item["action_label"] = action_label("hold", True)


def _truncate(items: list[dict[str, Any]], top_k: int | None) -> list[dict[str, Any]]:
    if top_k is None or top_k <= 0:
        return items
    return items[:top_k]


def apply_regime_gate(
    result: dict[str, Any],
    regime: dict[str, Any],
    *,
    override: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a gated copy of a recommendations API payload."""
    gate_cfg = _regime_cfg(cfg)
    out = deepcopy(result)
    summary = _summary(regime, override=override, cfg=gate_cfg)
    out["regime"] = summary

    level = summary["gate_level"]
    blocked = False
    top_k = int(gate_cfg.get("shrink_top_k") or 0) or None
    should_shrink = summary.get("pool_policy") == "shrink" or (
        gate_cfg.get("pool_policy") or {}
    ).get(level) == "shrink"

    if override and str(regime.get("gate_level") or "") == "risk_off":
        warnings = list(out.get("warnings") or [])
        warnings.append("regime_override applied: risk_off treated as defensive")
        out["warnings"] = warnings

    def apply_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal blocked
        rows = _truncate(items, top_k) if should_shrink else items
        for item in rows:
            if str(regime.get("gate_level") or "") == "risk_off" and not override:
                blocked = _gate_buy_action(item) or blocked
            elif level == "defensive":
                _defensive_relabel(item, gate_cfg)
        return rows

    boards = out.get("boards") or {}
    if boards:
        for block in boards.values():
            items = apply_items(list(block.get("items") or []))
            block["items"] = items
            block["count"] = len(items)

        board_filter = out.get("board")
        preserve_items = board_filter not in (None, "", "all")
        if preserve_items:
            items = apply_items(list(out.get("items") or []))
            out["items"] = items
            out["count"] = len(items)
        else:
            flat: list[dict[str, Any]] = []
            for block in boards.values():
                flat.extend(block.get("items") or [])
            out["items"] = flat
            out["count"] = len(flat)
    else:
        items = apply_items(list(out.get("items") or []))
        out["items"] = items
        out["count"] = len(items)

    if blocked:
        out["gate_blocked_buys"] = True
    return out
