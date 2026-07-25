"""Lightweight knowledge RuleSpec validation, simulation, and aggregation."""

from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np
import pandas as pd

from .features import compute_factors

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


def split_bar_range(n_bars: int, train_ratio: float = 0.7) -> tuple[int, int]:
    n = int(n_bars)
    if n <= 0:
        return 0, 0
    train_end = int(n * float(train_ratio))
    train_end = max(1, min(train_end, n - 1)) if n >= 2 else n
    return train_end, n


def metrics_from_trades(
    trades: list[dict[str, Any]],
    equity_rets: list[float],
) -> dict[str, Any]:
    rets = [float(t["ret"]) for t in trades]
    trade_count = len(rets)
    hit_rate = (sum(1 for r in rets if r > 0) / trade_count) if trade_count else 0.0
    eq = np.asarray(equity_rets, dtype=float) if equity_rets else np.asarray([0.0])
    wealth = np.cumprod(1.0 + eq) if len(eq) else np.asarray([1.0])
    total_return = float(wealth[-1] - 1.0)
    peak = np.maximum.accumulate(wealth)
    dd = (wealth / peak) - 1.0
    max_drawdown = float(abs(dd.min())) if len(dd) else 0.0
    if len(eq) >= 2 and float(np.std(eq, ddof=1)) > 1e-12:
        sharpe = float(np.mean(eq) / np.std(eq, ddof=1) * np.sqrt(252))
    else:
        sharpe = 0.0
    return {
        "total_return": round(total_return, 6),
        "max_drawdown": round(max_drawdown, 6),
        "sharpe": round(sharpe, 6),
        "hit_rate": round(hit_rate, 6),
        "trade_count": trade_count,
        "sample_count": int(len(eq)),
    }


def _exit_index(
    df: pd.DataFrame,
    entry_i: int,
    entry_px: float,
    spec: dict[str, Any],
) -> tuple[int, float]:
    hold_days = int(spec["hold_days"])
    stop = None
    take = None
    for ex in (spec.get("exit") or {}).get("any") or []:
        t = ex.get("type")
        if t == "stop_loss":
            stop = float(ex["value"])
        elif t == "take_profit":
            take = float(ex["value"])
    last_i = len(df) - 1
    target_i = min(entry_i + hold_days, last_i)
    for j in range(entry_i + 1, target_i + 1):
        px = float(df.iloc[j]["close"])
        ret = px / entry_px - 1.0
        if stop is not None and ret <= -stop:
            return j, ret
        if take is not None and ret >= take:
            return j, ret
    exit_i = target_i
    exit_px = float(df.iloc[exit_i]["close"])
    return exit_i, exit_px / entry_px - 1.0


def simulate_symbol(
    df: pd.DataFrame,
    bench_df: pd.DataFrame | None,
    spec: dict[str, Any],
    *,
    sample_step: int = 1,
    index_lo: int = 0,
    index_hi: int | None = None,
) -> dict[str, Any]:
    """Evaluate entries on [index_lo, index_hi); exits may use full df."""
    if df is None or len(df) < 30:
        return {"trades": [], "equity_rets": [], "trade_count": 0}
    hi = len(df) if index_hi is None else int(index_hi)
    lo = max(24, int(index_lo))
    step = max(1, int(sample_step))
    trades: list[dict[str, Any]] = []
    equity = [0.0] * len(df)
    next_free = lo
    for i in range(lo, hi, step):
        if i < next_free:
            continue
        if i >= len(df) - 1:
            break
        window = df.iloc[: i + 1]
        bench_cut = None
        if bench_df is not None and not bench_df.empty:
            bench_cut = bench_df[bench_df["time"] <= window.iloc[-1]["time"]]
        factors = compute_factors(window, bench_cut)
        if not entry_matches(spec, factors):
            continue
        entry_px = float(df.iloc[i]["close"])
        if entry_px <= 0:
            continue
        exit_i, ret = _exit_index(df, i, entry_px, spec)
        trades.append({"entry_i": i, "exit_i": exit_i, "ret": float(ret)})
        equity[exit_i] += float(ret)
        next_free = exit_i + 1
    return {
        "trades": trades,
        "equity_rets": equity,
        "trade_count": len(trades),
    }
