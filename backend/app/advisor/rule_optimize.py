"""Neighborhood search over RuleSpec parameters for knowledge rule tuning."""

from __future__ import annotations

import copy
from typing import Any

import numpy as np

from . import rule_backtest as rb
from .config_loader import load_config


def score_objective(
    metrics: dict[str, Any],
    objective: str,
    *,
    min_sharpe: float,
    max_dd: float,
) -> tuple[bool, float]:
    tr = float(metrics.get("total_return") or 0)
    sh = float(metrics.get("sharpe") or 0)
    dd = float(metrics.get("max_drawdown") or 0)
    obj = (objective or "C").upper()
    if obj == "A":
        return True, tr
    if obj == "B":
        return True, sh
    # C: constraints then maximize total_return
    feasible = (sh >= float(min_sharpe)) and (dd <= float(max_dd))
    if feasible:
        return True, tr
    penalty = 0.0
    if sh < float(min_sharpe):
        penalty += float(min_sharpe) - sh
    if dd > float(max_dd):
        penalty += dd - float(max_dd)
    return False, -penalty


def perturb_rule(spec: dict[str, Any], rng: np.random.Generator) -> dict[str, Any]:
    s = copy.deepcopy(spec)
    for cond in s.get("entry", {}).get("all") or []:
        op = str(cond.get("op") or "")
        factor = str(cond.get("factor") or "")
        if op == "between":
            lo, hi = float(cond["value"][0]), float(cond["value"][1])
            span = max(1e-6, hi - lo)
            lo2 = lo + float(rng.normal(0, 0.1 * span))
            hi2 = hi + float(rng.normal(0, 0.1 * span))
            cond["value"] = [min(lo2, hi2), max(lo2, hi2)]
        else:
            v = float(cond["value"])
            # keep 0/1 yin-yang mostly stable; small chance flip unused
            if factor in ("is_yin", "is_yang"):
                continue
            scale = 0.2 * (abs(v) if abs(v) > 1e-6 else 0.01)
            cond["value"] = v + float(rng.normal(0, scale))
        if factor == "vol_ratio" and rng.random() < 0.4:
            try:
                lb = int(cond.get("lookback") or 5)
            except (TypeError, ValueError):
                lb = 5
            lb = int(max(2, min(60, lb + int(rng.choice([-2, -1, 1, 2])))))
            cond["lookback"] = lb
    if rng.random() < 0.5:
        delta = int(rng.choice([-1, 1]))
        s["hold_days"] = int(max(1, min(20, int(s.get("hold_days") or 1) + delta)))
    for ex in (s.get("exit") or {}).get("any") or []:
        t = ex.get("type")
        if t in ("stop_loss", "take_profit") and "value" in ex:
            v = float(ex["value"])
            nv = v * (1.0 + float(rng.normal(0, 0.2)))
            ex["value"] = float(max(0.01, min(0.99, nv)))
    normalized, errs = rb.validate_rule_spec(s)
    return normalized if normalized is not None else spec


def _cfg() -> dict[str, Any]:
    return load_config().get("rule_backtest") or {}


def optimize_rules(
    spec: dict[str, Any],
    *,
    objective: str = "C",
    symbols: list[str] | None = None,
    min_sharpe: float = 0.0,
    max_dd: float = 0.25,
    max_trials: int = 20,
    seed: int = 0,
) -> dict[str, Any]:
    cfg = _cfg()
    hard = int(cfg.get("max_trials_hard", 30))
    budget = max(1, min(int(max_trials), hard))
    min_trades = int(cfg.get("min_trades", 5))
    obj = (objective or "C").upper()
    if obj not in ("A", "B", "C"):
        obj = "C"

    rng = np.random.default_rng(int(seed))
    base, errs = rb.validate_rule_spec(spec)
    if base is None:
        return {
            "ok": False,
            "error": "无效 RuleSpec",
            "errors": errs,
            "objective": obj,
            "feasible": False,
            "truncated": False,
            "trials_run": 0,
            "best_spec": None,
            "in_sample": None,
            "out_of_sample": None,
            "closest": None,
            "trial_log": [],
        }

    trial_log: list[dict[str, Any]] = []
    best_feasible: dict[str, Any] | None = None
    best_feasible_score = float("-inf")
    closest: dict[str, Any] | None = None
    closest_score = float("-inf")

    current = base
    for trial in range(budget):
        candidate = current if trial == 0 else perturb_rule(current, rng)
        if candidate is None:
            continue
        # Score on in-sample only
        report = rb.run_rule_backtest_report(
            candidate, symbols=symbols, segment="train"
        )
        if not report.get("ok"):
            metrics = {
                "total_return": 0.0,
                "sharpe": 0.0,
                "max_drawdown": 1.0,
                "trade_count": 0,
            }
        else:
            metrics = report.get("metrics") or {}
        trade_count = int(metrics.get("trade_count") or 0)
        if trade_count < min_trades:
            trial_log.append(
                {
                    "trial": trial,
                    "feasible": False,
                    "score": None,
                    "metrics": metrics,
                    "invalid": "min_trades",
                }
            )
            current = candidate
            continue

        feasible, score = score_objective(
            metrics, obj, min_sharpe=min_sharpe, max_dd=max_dd
        )
        trial_log.append(
            {
                "trial": trial,
                "feasible": feasible,
                "score": score,
                "metrics": metrics,
            }
        )
        if feasible and score > best_feasible_score:
            best_feasible_score = score
            best_feasible = {
                "spec": candidate,
                "in_sample": metrics,
                "score": score,
            }
        if (not feasible) or best_feasible is None:
            if score > closest_score:
                closest_score = score
                closest = {
                    "spec": candidate,
                    "in_sample": metrics,
                    "score": score,
                    "feasible": feasible,
                }
        current = candidate

    truncated = True  # budget always exhausted for MVP search
    if best_feasible is not None:
        best_spec = best_feasible["spec"]
        # Final OOS evaluation
        oos_report = rb.run_rule_backtest_report(
            best_spec, symbols=symbols, segment="valid"
        )
        oos = (oos_report.get("metrics") if oos_report.get("ok") else {}) or {}
        return {
            "ok": True,
            "objective": obj,
            "feasible": True,
            "truncated": truncated,
            "trials_run": budget,
            "best_spec": best_spec,
            "in_sample": best_feasible["in_sample"],
            "out_of_sample": oos,
            "closest": None,
            "trial_log": trial_log,
        }

    # No feasible solution
    closest_spec = (closest or {}).get("spec")
    oos = None
    if closest_spec is not None:
        oos_report = rb.run_rule_backtest_report(
            closest_spec, symbols=symbols, segment="valid"
        )
        oos = (oos_report.get("metrics") if oos_report.get("ok") else {}) or {}
    return {
        "ok": True,
        "objective": obj,
        "feasible": False,
        "truncated": truncated,
        "trials_run": budget,
        "best_spec": None,
        "in_sample": (closest or {}).get("in_sample"),
        "out_of_sample": oos,
        "closest": closest,
        "trial_log": trial_log,
    }
