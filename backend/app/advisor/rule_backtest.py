"""Lightweight knowledge RuleSpec validation, simulation, and aggregation."""

from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np
import pandas as pd

from .config_loader import load_config
from .features import (
    compute_factors,
    fetch_daily_df,
    load_benchmark,
    volume_ratio_last,
)
from .universe import build_universe

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
        "is_yin",
        "is_yang",
    }
)
FACTOR_ALIASES = {
    "volume_ratio": "vol_ratio",
    "vol": "vol_ratio",
    # Agent 常误写 volume；按相对均量语义映射，避免「不支持」误报
    "volume": "vol_ratio",
}
REJECTED_FACTORS = {
    "turn": "暂不支持换手率绝对值，请用 vol_ratio + lookback",
    "turnover": "暂不支持换手率绝对值，请用 vol_ratio + lookback",
}
VOL_RATIO_LOOKBACK_MIN = 2
VOL_RATIO_LOOKBACK_MAX = 60
VOL_RATIO_LOOKBACK_DEFAULT = 5
ALLOWED_OPS = frozenset({">", ">=", "<", "<=", "between"})
ALLOWED_EXIT_TYPES = frozenset({"hold_days", "stop_loss", "take_profit"})


def describe_rule_factors() -> dict[str, Any]:
    """Catalog for Agent discovery — call before drafting rule_json."""
    return {
        "allowed_factors": sorted(ALLOWED_FACTORS),
        "aliases": dict(FACTOR_ALIASES),
        "rejected": dict(REJECTED_FACTORS),
        "vol_ratio": {
            "meaning": "当日成交量 / 前 N 日均量（不含当日）",
            "lookback_default": VOL_RATIO_LOOKBACK_DEFAULT,
            "lookback_min": VOL_RATIO_LOOKBACK_MIN,
            "lookback_max": VOL_RATIO_LOOKBACK_MAX,
        },
        "yin_yang": {
            "is_yin": "close < open → 1",
            "is_yang": "close > open → 1",
            "flat": "close == open → 二者均为 0",
        },
        "example_shrink_yin": {
            "entry": {
                "all": [
                    {"factor": "is_yin", "op": ">=", "value": 1},
                    {
                        "factor": "vol_ratio",
                        "lookback": 5,
                        "op": "<",
                        "value": 1.0,
                    },
                ]
            }
        },
        "notes": [
            "量能请用 vol_ratio（或别名 volume_ratio / vol / volume），不要用 turn",
            "引擎语义是 entry 全满足则买入；「不接/不卖」需写成可交易的正向条件或在知识正文说明",
        ],
    }


def normalize_factor_name(name: str) -> str:
    f = str(name or "").strip()
    return FACTOR_ALIASES.get(f, f)


def _condition_value(
    cond: dict[str, Any],
    factors: dict[str, float],
    df: pd.DataFrame | None = None,
) -> float | None:
    factor = normalize_factor_name(str(cond.get("factor") or ""))
    if factor == "vol_ratio":
        if df is not None:
            try:
                n = int(cond.get("lookback") or VOL_RATIO_LOOKBACK_DEFAULT)
            except (TypeError, ValueError):
                n = VOL_RATIO_LOOKBACK_DEFAULT
            return volume_ratio_last(df, n)
        # fallback to precomputed default-5 ratio
        val = factors.get("vol_ratio")
        return float(val) if val is not None else None
    val = factors.get(factor)
    if val is None:
        return None
    return float(val)


def eval_condition(
    cond: dict[str, Any],
    factors: dict[str, float],
    df: pd.DataFrame | None = None,
) -> bool:
    op = str(cond.get("op") or "")
    val = _condition_value(cond, factors, df=df)
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


def entry_matches(
    spec: dict[str, Any],
    factors: dict[str, float],
    df: pd.DataFrame | None = None,
) -> bool:
    all_conds = ((spec.get("entry") or {}).get("all")) or []
    if not all_conds:
        return False
    return all(eval_condition(c, factors, df=df) for c in all_conds)


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
            raw_f = str(cond.get("factor") or "")
            if raw_f in REJECTED_FACTORS:
                errors.append(
                    f"entry.all[{i}].factor 不支持: {raw_f}（{REJECTED_FACTORS[raw_f]}）"
                )
                continue
            f = normalize_factor_name(raw_f)
            cond["factor"] = f
            op = str(cond.get("op") or "")
            if f not in ALLOWED_FACTORS:
                catalog = describe_rule_factors()
                errors.append(
                    f"entry.all[{i}].factor 不支持: {raw_f or f}；"
                    f"可用因子={catalog['allowed_factors']}；"
                    f"别名={catalog['aliases']}；"
                    f"缩量阴示例={catalog['example_shrink_yin']}"
                )
            if op not in ALLOWED_OPS:
                errors.append(f"entry.all[{i}].op 不支持: {op}")
            if f == "vol_ratio":
                if "lookback" in cond and cond.get("lookback") is not None:
                    try:
                        lb = int(cond["lookback"])
                    except (TypeError, ValueError):
                        errors.append(f"entry.all[{i}].lookback 须为整数")
                        lb = VOL_RATIO_LOOKBACK_DEFAULT
                    else:
                        if lb < VOL_RATIO_LOOKBACK_MIN or lb > VOL_RATIO_LOOKBACK_MAX:
                            errors.append(
                                f"entry.all[{i}].lookback 须在 "
                                f"{VOL_RATIO_LOOKBACK_MIN}..{VOL_RATIO_LOOKBACK_MAX}"
                            )
                        else:
                            cond["lookback"] = lb
                else:
                    cond["lookback"] = VOL_RATIO_LOOKBACK_DEFAULT
            elif "lookback" in cond:
                # only vol_ratio supports lookback in this version
                del cond["lookback"]
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
        if not entry_matches(spec, factors, df=window):
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


def _rule_cfg() -> dict[str, Any]:
    return load_config().get("rule_backtest") or {}


def resolve_symbols(symbols: list[str] | None = None) -> list[str]:
    if symbols:
        out: list[str] = []
        seen: set[str] = set()
        for s in symbols:
            s = str(s or "").strip()
            if len(s) == 6 and s not in seen:
                seen.add(s)
                out.append(s)
        return out or ["510300", "510500", "159915"]

    cfg = _rule_cfg()
    per_board = int(cfg.get("max_symbols_per_board", 8))
    board_ids = [str(b) for b in (cfg.get("boards") or ["etf", "hs"])]
    try:
        universe = build_universe(force=False)
    except Exception:
        return ["510300", "510500", "159915"]

    out = []
    seen = set()
    for bid in board_ids:
        block = (universe.get("boards") or {}).get(bid) or {}
        items = block.get("symbols") or block.get("items") or []
        # universe boards may be list or {symbols: [...]}
        if isinstance(block, list):
            items = block
        for u in list(items)[:per_board]:
            if isinstance(u, dict):
                s = str(u.get("symbol") or "").strip()
            else:
                s = str(u or "").strip()
            if len(s) == 6 and s not in seen:
                seen.add(s)
                out.append(s)
    return out or ["510300", "510500", "159915"]


def _truncate_lookback(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if lookback and len(df) > lookback + 5:
        return df.iloc[-(lookback + 5) :].reset_index(drop=True)
    return df.reset_index(drop=True)


def _aggregate_symbol_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    max_len = 0
    for r in runs:
        trades.extend(r.get("trades") or [])
        max_len = max(max_len, len(r.get("equity_rets") or []))
    if max_len == 0:
        return metrics_from_trades([], [])
    # equal-weight average of daily equity rets across symbols
    stacked = np.zeros(max_len, dtype=float)
    counts = np.zeros(max_len, dtype=float)
    for r in runs:
        eq = r.get("equity_rets") or []
        for i, v in enumerate(eq):
            stacked[i] += float(v)
            counts[i] += 1.0
    avg = np.divide(
        stacked, np.maximum(counts, 1.0), out=np.zeros_like(stacked), where=counts > 0
    )
    return metrics_from_trades(trades, avg.tolist())


def _simulate_symbols(
    spec: dict[str, Any],
    syms: list[str],
    *,
    sample_step: int,
    lookback: int,
    bench_df: pd.DataFrame | None,
    segment: str = "full",
) -> dict[str, Any]:
    """segment: full | train | valid — when train/valid, split each df 70/30."""
    train_ratio = float((_rule_cfg() or {}).get("train_ratio", 0.7))
    runs: list[dict[str, Any]] = []
    for sym in syms:
        try:
            _name, df = fetch_daily_df(sym)
        except Exception:
            continue
        df = _truncate_lookback(df, lookback)
        if df is None or len(df) < 30:
            continue
        lo = 0
        hi = None
        if segment == "train":
            train_end, _n = split_bar_range(len(df), train_ratio)
            lo, hi = 0, train_end
        elif segment == "valid":
            train_end, n = split_bar_range(len(df), train_ratio)
            lo, hi = train_end, n
        runs.append(
            simulate_symbol(
                df,
                bench_df,
                spec,
                sample_step=sample_step,
                index_lo=lo,
                index_hi=hi,
            )
        )
    return _aggregate_symbol_runs(runs)


def run_rule_backtest_report(
    spec: dict[str, Any],
    *,
    symbols: list[str] | None = None,
    segment: str = "all",
    sample_step: int | None = None,
) -> dict[str, Any]:
    cfg = _rule_cfg()
    lookback = int(cfg.get("lookback_bars", 240))
    step = int(sample_step if sample_step is not None else cfg.get("sample_step", 5))
    seg = (segment or "all").strip().lower()
    if seg not in ("all", "train", "valid"):
        return {"ok": False, "error": f"无效 segment: {segment}"}

    try:
        bench_df = load_benchmark()
    except Exception:
        bench_df = None

    syms = resolve_symbols(symbols)
    if not syms:
        return {"ok": False, "error": "无可用标的", "symbols": []}

    if seg == "all":
        full = _simulate_symbols(
            spec, syms, sample_step=step, lookback=lookback, bench_df=bench_df, segment="full"
        )
        inn = _simulate_symbols(
            spec, syms, sample_step=step, lookback=lookback, bench_df=bench_df, segment="train"
        )
        out = _simulate_symbols(
            spec, syms, sample_step=step, lookback=lookback, bench_df=bench_df, segment="valid"
        )
        return {
            "ok": True,
            "symbols": syms,
            "segment": "all",
            "metrics": full,
            "in_sample": inn,
            "out_of_sample": out,
        }

    mapped = "train" if seg == "train" else "valid"
    metrics = _simulate_symbols(
        spec, syms, sample_step=step, lookback=lookback, bench_df=bench_df, segment=mapped
    )
    return {
        "ok": True,
        "symbols": syms,
        "segment": seg,
        "metrics": metrics,
    }
