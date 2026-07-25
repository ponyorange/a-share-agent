"""Lightweight knowledge RuleSpec validation and condition evaluation."""

from __future__ import annotations

import copy
import math
from typing import Any

ALLOWED_FACTORS = frozenset(
    {
        "mom_1",
        "mom_5",
        "mom_10",
        "mom_20",
        "ma20_bias",
        "vol_z",
        "vol_ratio",
        "rs_300",
        "low_vol",
    }
)
ALLOWED_OPS = frozenset({">", ">=", "<", "<=", "between"})
ALLOWED_EXIT_TYPES = frozenset({"hold_days", "stop_loss", "take_profit"})


def eval_condition(cond: dict[str, Any], factors: dict[str, float]) -> bool:
    factor = str(cond.get("factor") or "")
    op = str(cond.get("op") or "")
    val = factors.get(factor)
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return False
    raw = cond.get("value")
    if op == "between":
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return False
        lo, hi = float(raw[0]), float(raw[1])
        return lo <= float(val) <= hi
    thr = float(raw)
    if op == ">":
        return float(val) > thr
    if op == ">=":
        return float(val) >= thr
    if op == "<":
        return float(val) < thr
    if op == "<=":
        return float(val) <= thr
    return False


def entry_matches(spec: dict[str, Any], factors: dict[str, float]) -> bool:
    all_conds = ((spec.get("entry") or {}).get("all")) or []
    if not all_conds:
        return False
    return all(eval_condition(c, factors) for c in all_conds)


def validate_rule_spec(
    raw: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, ["rule_spec 必须是 JSON 对象"]
    spec = copy.deepcopy(raw)
    spec["version"] = int(spec.get("version") or 1)
    if spec["version"] != 1:
        errors.append("version 仅支持 1")
    action = str(spec.get("action") or "buy").lower()
    if action != "buy":
        errors.append("action 仅支持 buy")
    spec["action"] = "buy"
    try:
        hold_days = int(spec.get("hold_days") or 1)
    except (TypeError, ValueError):
        hold_days = 0
    if hold_days < 1 or hold_days > 20:
        errors.append("hold_days 须在 1..20")
    spec["hold_days"] = max(1, min(hold_days or 1, 20))

    entry = spec.get("entry")
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("all"), list)
        or not entry["all"]
    ):
        errors.append("entry.all 须为非空数组")
    else:
        for i, cond in enumerate(entry["all"]):
            if not isinstance(cond, dict):
                errors.append(f"entry.all[{i}] 无效")
                continue
            f = str(cond.get("factor") or "")
            op = str(cond.get("op") or "")
            if f not in ALLOWED_FACTORS:
                errors.append(f"entry.all[{i}].factor 不支持: {f}")
            if op not in ALLOWED_OPS:
                errors.append(f"entry.all[{i}].op 不支持: {op}")
            if op == "between":
                v = cond.get("value")
                if not isinstance(v, (list, tuple)) or len(v) != 2:
                    errors.append(f"entry.all[{i}].value between 须为 [lo,hi]")
            else:
                try:
                    float(cond.get("value"))
                except (TypeError, ValueError):
                    errors.append(f"entry.all[{i}].value 须为数字")

    exit_block = spec.get("exit")
    if exit_block is None:
        spec["exit"] = {"any": [{"type": "hold_days"}]}
    else:
        any_exits = (
            (exit_block.get("any") if isinstance(exit_block, dict) else None) or []
        )
        if not any_exits:
            errors.append("exit.any 不能为空")
        for i, ex in enumerate(any_exits):
            if not isinstance(ex, dict):
                errors.append(f"exit.any[{i}] 无效")
                continue
            t = str(ex.get("type") or "")
            if t not in ALLOWED_EXIT_TYPES:
                errors.append(f"exit.any[{i}].type 不支持: {t}")
            if t in ("stop_loss", "take_profit"):
                try:
                    v = float(ex.get("value"))
                    if v <= 0 or v >= 1:
                        errors.append(f"exit.any[{i}].value 须在 (0,1)")
                except (TypeError, ValueError):
                    errors.append(f"exit.any[{i}].value 须为数字")

    if errors:
        return None, errors
    spec.setdefault("name", "未命名规则")
    spec.setdefault("source_knowledge_id", None)
    spec.setdefault("natural_language_summary", "")
    return spec, []
