# 知识规则回测与调优 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Agent 把自然语言知识编译为轻量 RuleSpec，机械回测并有限次调参，再经 `save_knowledge` 预览确认写回知识库。

**Architecture:** 纯函数引擎 `rule_backtest.py`（DSL 校验、单标的仿真、70/30 分段聚合）+ `rule_optimize.py`（邻域搜索 ≤20）；Agent 负责把 NL 起草为 `rule_json`，工具 `compile_knowledge_rules` 只做校验/归一化与溯源；`run_rule_backtest` / `optimize_knowledge_rules` 调用引擎；写库复用现有 `save_knowledge`。前端不改。

**Tech Stack:** Python / pandas / numpy / LangChain `@tool` / pytest；行情复用 `fetch_daily_df` + `compute_factors`。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-knowledge-rule-backtest-optimize-design.md`
- 优化目标默认 **C**：`sharpe ≥ min_sharpe`（默认 0）且 `max_drawdown ≤ max_dd`（默认 0.25）下最大化 `total_return`
- 分段：时间序前 **70%** in-sample / 后 **30%** out-of-sample；选参只用 in-sample
- `optimize` 试验上限默认 **20**，硬上限 **30**；`trade_count < 5` 的试验无效
- 默认池：复用 `config.backtest.boards`，每板最多 **8** 只（比普通回测 12 更紧）
- MVP factor：`mom_1|mom_5|mom_10|mom_20|ma20_bias|vol_z|vol_ratio|rs_300|low_vol`
- MVP `action` 仅 `buy`；`op`：`> >= < <= between`
- 写库必须经现有 `save_knowledge(confirm=true)`；本功能工具本身不落库
- 前端本版不改
- Commit 仅在用户明确要求时执行；计划中的 commit 步骤默认跳过

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/rule_backtest.py` | RuleSpec 校验/归一化、条件求值、单标的仿真、多标的聚合、分段 |
| `backend/app/advisor/rule_optimize.py` | 目标打分、邻域扰动、试验预算、选最优 |
| `backend/app/advisor/agent/tools.py` | 注册 `compile_knowledge_rules` / `run_rule_backtest` / `optimize_knowledge_rules` |
| `backend/app/advisor/agent/graph.py` | SYSTEM_PROMPT 增加规则 17 |
| `backend/app/advisor/config.yaml` | `rule_backtest` 配置段 |
| `backend/tests/test_rule_backtest.py` | DSL / 仿真 / 分段 |
| `backend/tests/test_rule_optimize.py` | 目标 C / 预算 / 无效试验 |
| `backend/tests/test_rule_backtest_tools.py` | 工具 JSON 契约（mock 行情） |

---

### Task 1: RuleSpec 校验与条件求值

**Files:**
- Create: `backend/app/advisor/rule_backtest.py`
- Create: `backend/tests/test_rule_backtest.py`

**Interfaces:**
- Produces:
  - `ALLOWED_FACTORS: frozenset[str]`
  - `ALLOWED_OPS: frozenset[str]`
  - `validate_rule_spec(raw: dict) -> tuple[dict | None, list[str]]`  
    成功返回 `(normalized_spec, [])`；失败 `(None, errors)`
  - `eval_condition(cond: dict, factors: dict[str, float]) -> bool`
  - `entry_matches(spec: dict, factors: dict[str, float]) -> bool`

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_rule_backtest.py
from app.advisor import rule_backtest as rb


def test_validate_rule_spec_ok_normalizes_defaults():
    raw = {
        "name": "动量",
        "entry": {"all": [{"factor": "mom_5", "op": ">=", "value": 0.03}]},
    }
    spec, errs = rb.validate_rule_spec(raw)
    assert errs == []
    assert spec["version"] == 1
    assert spec["action"] == "buy"
    assert spec["hold_days"] == 1
    assert spec["exit"]["any"] == [{"type": "hold_days"}]


def test_validate_rule_spec_rejects_bad_factor():
    raw = {
        "entry": {"all": [{"factor": "foo", "op": ">=", "value": 1}]},
    }
    spec, errs = rb.validate_rule_spec(raw)
    assert spec is None
    assert any("factor" in e for e in errs)


def test_eval_condition_between_and_nan_false():
    assert rb.eval_condition(
        {"factor": "mom_5", "op": "between", "value": [0.01, 0.05]},
        {"mom_5": 0.03},
    )
    assert not rb.eval_condition(
        {"factor": "mom_5", "op": ">=", "value": 0.0},
        {"mom_5": float("nan")},
    )


def test_entry_matches_all():
    spec = {
        "entry": {
            "all": [
                {"factor": "mom_5", "op": ">=", "value": 0.02},
                {"factor": "ma20_bias", "op": ">", "value": 0.0},
            ]
        }
    }
    assert rb.entry_matches(spec, {"mom_5": 0.03, "ma20_bias": 0.01})
    assert not rb.entry_matches(spec, {"mom_5": 0.01, "ma20_bias": 0.01})
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest.py::test_validate_rule_spec_ok_normalizes_defaults -v`

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现最小模块**

```python
# backend/app/advisor/rule_backtest.py
from __future__ import annotations

import copy
import math
from typing import Any

ALLOWED_FACTORS = frozenset({
    "mom_1", "mom_5", "mom_10", "mom_20",
    "ma20_bias", "vol_z", "vol_ratio", "rs_300", "low_vol",
})
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


def validate_rule_spec(raw: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
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
    if not isinstance(entry, dict) or not isinstance(entry.get("all"), list) or not entry["all"]:
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
        any_exits = (exit_block.get("any") if isinstance(exit_block, dict) else None) or []
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
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 2: 单标的仿真与指标聚合（合成 K 线）

**Files:**
- Modify: `backend/app/advisor/rule_backtest.py`
- Modify: `backend/tests/test_rule_backtest.py`

**Interfaces:**
- Consumes: `validate_rule_spec`, `entry_matches`
- Produces:
  - `simulate_symbol(df, bench_df, spec, *, sample_step=1) -> dict`  
    含 `trades: list[{entry_i, exit_i, ret}]`, `equity_rets: list[float]`（按日仓位收益序列，空仓为 0）
  - `metrics_from_trades(trades, equity_rets) -> dict`  
    keys: `total_return`, `max_drawdown`, `sharpe`, `hit_rate`, `trade_count`, `sample_count`
  - `split_bar_range(n_bars: int, train_ratio: float = 0.7) -> tuple[int, int]`  
    返回 `(train_end_exclusive, n_bars)`；train 用 `[0, train_end)`，valid 用 `[train_end, n)`

说明：仿真用「信号日收盘买入、持有 hold_days 后收盘卖出」；持仓重叠时 MVP **忽略新开仓**（flat-to-flat）。止损/止盈在持有期内按日收盘检查（相对入场价）。

- [ ] **Step 1: 写失败单测（合成上涨序列）**

```python
import numpy as np
import pandas as pd


def _synth_df(n: int = 80, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # 构造后期动量偏强，便于 mom_5 触发
    rets = rng.normal(0.002, 0.01, size=n)
    close = 100 * np.cumprod(1 + rets)
    vol = rng.integers(1_000_000, 2_000_000, size=n).astype(float)
    # 放大末段成交量，抬高 vol_ratio
    vol[-10:] *= 3
    times = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "time": times.strftime("%Y-%m-%d"),
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": vol,
        "amount": close * vol,
    })


def test_split_bar_range_70_30():
    train_end, n = rb.split_bar_range(100, 0.7)
    assert n == 100
    assert train_end == 70


def test_simulate_symbol_produces_trades():
    from app.advisor.features import compute_factors

    df = _synth_df(90)
    spec, errs = rb.validate_rule_spec({
        "hold_days": 1,
        "entry": {"all": [{"factor": "mom_5", "op": ">", "value": -1.0}]},  # 几乎总能触发
    })
    assert errs == []
    out = rb.simulate_symbol(df, None, spec, sample_step=1)
    assert out["trade_count"] >= 5
    m = rb.metrics_from_trades(out["trades"], out["equity_rets"])
    assert "total_return" in m and "sharpe" in m and "max_drawdown" in m
    assert m["trade_count"] == out["trade_count"]
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest.py::test_simulate_symbol_produces_trades -v`

Expected: FAIL（缺函数）

- [ ] **Step 3: 实现仿真与指标**

在 `rule_backtest.py` 追加（核心逻辑）：

```python
import numpy as np
import pandas as pd

from .features import compute_factors


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
    # 累计权益
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
    """在 [index_lo, index_hi) 的 bar 上评估开仓；平仓可越过 hi（用全量 df）。"""
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
        # 把交易收益摊到退出日（简化）
        equity[exit_i] += float(ret)
        next_free = exit_i + 1
    return {
        "trades": trades,
        "equity_rets": equity,
        "trade_count": len(trades),
    }
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 3: 多标的回测入口 + 默认抽样池

**Files:**
- Modify: `backend/app/advisor/rule_backtest.py`
- Modify: `backend/app/advisor/config.yaml`
- Modify: `backend/tests/test_rule_backtest.py`

**Interfaces:**
- Produces:
  - `resolve_symbols(symbols: list[str] | None) -> list[str]`
  - `run_rule_backtest_report(spec, *, symbols=None, segment="all"|"train"|"valid", sample_step=None) -> dict`  
    返回 `{ok, symbols, segment, metrics, per_symbol?, error?}`；`segment=all` 时额外返回 `in_sample` / `out_of_sample` 两套 metrics（对同一标的列表按各自 df 长度 70/30 切，再聚合交易）

聚合策略（MVP）：各标的 `trades` 合并后算 hit_rate/trade_count；`equity_rets` 按日 **等权平均** 各标的当日收益（缺行情日视为 0），再算 total_return/max_dd/sharpe。

- [ ] **Step 1: 写失败单测（mock fetch）**

```python
def test_run_rule_backtest_report_split(monkeypatch):
    df = _synth_df(100)

    def fake_fetch(symbol):
        return symbol, df.copy()

    monkeypatch.setattr(rb, "fetch_daily_df", fake_fetch)
    monkeypatch.setattr(rb, "load_benchmark", lambda: None)
    monkeypatch.setattr(rb, "resolve_symbols", lambda symbols=None: ["AAA", "BBB"])

    spec, _ = rb.validate_rule_spec({
        "hold_days": 1,
        "entry": {"all": [{"factor": "mom_5", "op": ">", "value": -1.0}]},
    })
    report = rb.run_rule_backtest_report(spec, symbols=["AAA", "BBB"], segment="all")
    assert report["ok"] is True
    assert "in_sample" in report and "out_of_sample" in report
    assert report["in_sample"]["trade_count"] >= 0
    assert report["metrics"]["trade_count"] == (
        report["in_sample"]["trade_count"] + report["out_of_sample"]["trade_count"]
    ) or report["metrics"]["trade_count"] >= report["in_sample"]["trade_count"]
```

（实现时：`metrics` 为全样本；`in_sample`/`out_of_sample` 为分段；不必强制 trade_count 相加等于全样本——重叠持仓边界可能导致差异。断言改为：`in_sample` 与 `out_of_sample` 均含 `trade_count`/`total_return`。）

修正断言：

```python
    assert set(report["in_sample"]) >= {"total_return", "sharpe", "max_drawdown", "trade_count"}
    assert set(report["out_of_sample"]) >= {"total_return", "sharpe", "max_drawdown", "trade_count"}
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest.py::test_run_rule_backtest_report_split -v`

Expected: FAIL

- [ ] **Step 3: 配置 + 实现**

在 `config.yaml` 追加：

```yaml
# ---------- 知识规则回测 / 调优（Agent 工具）----------
rule_backtest:
  lookback_bars: 240
  sample_step: 5
  train_ratio: 0.7
  min_trades: 5
  max_trials: 20
  max_trials_hard: 30
  max_symbols_per_board: 8
  default_min_sharpe: 0.0
  default_max_dd: 0.25
  boards:
    - etf
    - hs
```

实现要点：

```python
def resolve_symbols(symbols: list[str] | None = None) -> list[str]:
    from .config_loader import load_config
    from .universe import build_universe  # 或 iter_build_universe_events 取 done

    if symbols:
        out = []
        seen = set()
        for s in symbols:
            s = str(s or "").strip()
            if len(s) == 6 and s not in seen:
                seen.add(s)
                out.append(s)
        return out or ["510300", "510500", "159915"]

    cfg = load_config().get("rule_backtest") or {}
    per_board = int(cfg.get("max_symbols_per_board", 8))
    board_ids = [str(b) for b in (cfg.get("boards") or ["etf", "hs"])]
    # 与 backtest.py 相同：取候选池每板前 per_board
    # 优先调用已有 build_universe()/缓存；失败则 fallback ETF
    ...
```

`run_rule_backtest_report`：对每个 symbol `fetch_daily_df` → 截断 lookback → 按 segment 调 `simulate_symbol` → 聚合。`segment="all"` 时再分别跑 train/valid 窗口填入 `in_sample`/`out_of_sample`。

- [ ] **Step 4: 跑测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 4: 目标函数与邻域优化

**Files:**
- Create: `backend/app/advisor/rule_optimize.py`
- Create: `backend/tests/test_rule_optimize.py`

**Interfaces:**
- Consumes: `validate_rule_spec`, `run_rule_backtest_report`
- Produces:
  - `score_objective(metrics: dict, objective: str, *, min_sharpe: float, max_dd: float) -> tuple[bool, float]`  
    返回 `(feasible, score)`；A→`total_return`；B→`sharpe`；C→可行时 `total_return`，不可行 `feasible=False` 且 score 为惩罚距离（越小越好仅用于「最接近」）
  - `perturb_rule(spec: dict, rng) -> dict`  
    扰动 entry 阈值（±20% 或加减小步）、`hold_days` ±1（夹紧 1..20）、止损/止盈 ±20%
  - `optimize_rules(spec, *, objective="C", symbols=None, min_sharpe=0.0, max_dd=0.25, max_trials=20, seed=0) -> dict`  
    结构：
    ```python
    {
      "ok": True,
      "objective": "C",
      "feasible": bool,
      "truncated": bool,  # 触达预算
      "trials_run": int,
      "best_spec": dict | None,
      "in_sample": dict | None,
      "out_of_sample": dict | None,
      "closest": dict | None,  # 无可行解时
      "trial_log": [{"trial": 0, "feasible": bool, "score": float, "metrics": dict}],
    }
    ```

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_rule_optimize.py
from app.advisor import rule_optimize as ro


def test_score_objective_c_feasibility():
    ok_m = {"total_return": 0.1, "sharpe": 0.5, "max_drawdown": 0.1, "trade_count": 10}
    bad_m = {"total_return": 0.5, "sharpe": -1.0, "max_drawdown": 0.5, "trade_count": 10}
    f1, s1 = ro.score_objective(ok_m, "C", min_sharpe=0.0, max_dd=0.25)
    f2, s2 = ro.score_objective(bad_m, "C", min_sharpe=0.0, max_dd=0.25)
    assert f1 and s1 == 0.1
    assert not f2


def test_optimize_respects_trial_budget(monkeypatch):
    from app.advisor import rule_backtest as rb

    spec, _ = rb.validate_rule_spec({
        "entry": {"all": [{"factor": "mom_5", "op": ">=", "value": 0.02}]},
    })
    calls = {"n": 0}

    def fake_report(s, **kwargs):
        calls["n"] += 1
        return {
            "ok": True,
            "metrics": {
                "total_return": 0.01 * calls["n"],
                "sharpe": 0.2,
                "max_drawdown": 0.05,
                "trade_count": 10,
                "hit_rate": 0.5,
                "sample_count": 50,
            },
            "in_sample": {
                "total_return": 0.01 * calls["n"],
                "sharpe": 0.2,
                "max_drawdown": 0.05,
                "trade_count": 10,
            },
            "out_of_sample": {
                "total_return": 0.005,
                "sharpe": 0.1,
                "max_drawdown": 0.08,
                "trade_count": 3,
            },
        }

    monkeypatch.setattr(rb, "run_rule_backtest_report", fake_report)
    # optimize_rules 内部应 import 并调用 run_rule_backtest_report
    out = ro.optimize_rules(spec, objective="A", max_trials=5, seed=1)
    assert out["trials_run"] <= 5
    assert out["truncated"] is True
    assert out["best_spec"] is not None
```

注意：`optimize_rules` 应对 **in_sample metrics** 打分；选中最优后用 best_spec 再跑一次 `segment=all`（或单独 valid）填 `out_of_sample`。试验次数计入每次 in-sample 评估（含 trial0 基线）。

无效试验：`trade_count < min_trades` → 跳过，不更新 best，但仍计 `trials_run`。

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_optimize.py -v`

Expected: FAIL

- [ ] **Step 3: 实现 `rule_optimize.py`**

```python
# 关键实现要点
def score_objective(metrics, objective, *, min_sharpe, max_dd):
    tr = float(metrics.get("total_return") or 0)
    sh = float(metrics.get("sharpe") or 0)
    dd = float(metrics.get("max_drawdown") or 0)
    obj = (objective or "C").upper()
    if obj == "A":
        return True, tr
    if obj == "B":
        return True, sh
    # C
    feasible = (sh >= min_sharpe) and (dd <= max_dd)
    if feasible:
        return True, tr
    # 惩罚：越大越差；用于 closest
    penalty = 0.0
    if sh < min_sharpe:
        penalty += (min_sharpe - sh)
    if dd > max_dd:
        penalty += (dd - max_dd)
    return False, -penalty


def perturb_rule(spec, rng):
    s = copy.deepcopy(spec)
    for cond in s["entry"]["all"]:
        if cond["op"] == "between":
            lo, hi = float(cond["value"][0]), float(cond["value"][1])
            span = max(1e-6, hi - lo)
            lo2 = lo + float(rng.normal(0, 0.1 * span))
            hi2 = hi + float(rng.normal(0, 0.1 * span))
            cond["value"] = [min(lo2, hi2), max(lo2, hi2)]
        else:
            v = float(cond["value"])
            scale = 0.2 * (abs(v) if abs(v) > 1e-6 else 0.01)
            cond["value"] = v + float(rng.normal(0, scale))
    if rng.random() < 0.5:
        s["hold_days"] = int(max(1, min(20, s["hold_days"] + int(rng.choice([-1, 1])))))
    ...
    return validate_rule_spec(s)[0] or spec


def optimize_rules(...):
    # trial 0 = baseline on in-sample
    # trials 1..max_trials-1 = perturb
    # track best feasible by score; also track closest infeasible
    # after loop: evaluate best on out_of_sample
    # hard cap min(max_trials, max_trials_hard from config)
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_optimize.py tests/test_rule_backtest.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 5: Agent 工具注册

**Files:**
- Modify: `backend/app/advisor/agent/tools.py`（在 `load_knowledge` 附近新增三工具，并加入 `build_tools` 返回列表）
- Create: `backend/tests/test_rule_backtest_tools.py`

**Interfaces:**
- Produces tools（均返回 JSON 字符串）:
  - `compile_knowledge_rules(rule_json: str, text: str = "", knowledge_id: str = "") -> str`  
    解析 `rule_json` → `validate_rule_spec`；若 `knowledge_id` 则校验存在并写入 `source_knowledge_id`；附带 `text`/`body` 摘要到 `natural_language_summary`（若为空则用 text 前 200 字）。**不落库。**
  - `run_rule_backtest(rule_json: str, symbols: str = "", segment: str = "all") -> str`  
    `symbols` 为逗号分隔；空则默认池。
  - `optimize_knowledge_rules(rule_json: str, objective: str = "C", symbols: str = "", min_sharpe: float = 0.0, max_dd: float = 0.25, max_trials: int = 20) -> str`

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_rule_backtest_tools.py
import json
from app.advisor.agent.tools import build_tools


def _tool_map(user_id="u1"):
    return {t.name: t for t in build_tools(user_id)}


def test_compile_knowledge_rules_validates():
    tools = _tool_map()
    raw = json.dumps({
        "name": "t",
        "entry": {"all": [{"factor": "mom_5", "op": ">=", "value": 0.02}]},
    })
    out = json.loads(tools["compile_knowledge_rules"].invoke({
        "rule_json": raw,
        "text": "五日动量过滤",
    }))
    assert out["ok"] is True
    assert out["rule"]["hold_days"] == 1
    assert "五日动量" in (out["rule"].get("natural_language_summary") or "五日动量过滤")


def test_compile_knowledge_rules_bad_factor():
    tools = _tool_map()
    raw = json.dumps({
        "entry": {"all": [{"factor": "nope", "op": ">=", "value": 1}]},
    })
    out = json.loads(tools["compile_knowledge_rules"].invoke({"rule_json": raw}))
    assert out["ok"] is False
    assert out["errors"]


def test_optimize_knowledge_rules_json(monkeypatch):
    from app.advisor import rule_optimize as ro

    def fake_opt(spec, **kwargs):
        return {
            "ok": True,
            "objective": "C",
            "feasible": True,
            "truncated": True,
            "trials_run": 2,
            "best_spec": spec,
            "in_sample": {"total_return": 0.1, "sharpe": 0.2, "max_drawdown": 0.05, "trade_count": 8},
            "out_of_sample": {"total_return": 0.02, "sharpe": 0.1, "max_drawdown": 0.1, "trade_count": 3},
            "closest": None,
            "trial_log": [],
        }

    monkeypatch.setattr(ro, "optimize_rules", fake_opt)
    tools = _tool_map()
    raw = json.dumps({
        "entry": {"all": [{"factor": "mom_5", "op": ">=", "value": 0.02}]},
    })
    out = json.loads(tools["optimize_knowledge_rules"].invoke({
        "rule_json": raw,
        "objective": "C",
        "symbols": "510300",
    }))
    assert out["ok"] is True
    assert out["feasible"] is True
    assert "in_sample" in out
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest_tools.py -v`

Expected: FAIL（工具未注册）

- [ ] **Step 3: 在 `tools.py` 实现并注册**

放在 `load_knowledge` 之前或之后，模式与现有工具一致：

```python
    @tool
    def compile_knowledge_rules(
        rule_json: str,
        text: str = "",
        knowledge_id: str = "",
    ) -> str:
        """将 Agent 起草的规则 JSON 校验为 RuleSpec。
        输入 rule_json（必填）；可选 knowledge_id/text 做溯源。
        不写入知识库。失败返回 errors 列表供澄清。"""
        ...

    @tool
    def run_rule_backtest(
        rule_json: str,
        symbols: str = "",
        segment: str = "all",
    ) -> str:
        """对 RuleSpec 做机械历史回测。symbols 逗号分隔，空=默认抽样池。
        segment=all|train|valid；all 时返回 in_sample 与 out_of_sample。"""
        ...

    @tool
    def optimize_knowledge_rules(
        rule_json: str,
        objective: str = "C",
        symbols: str = "",
        min_sharpe: float = 0.0,
        max_dd: float = 0.25,
        max_trials: int = 20,
    ) -> str:
        """在参数邻域内有限次搜索（默认≤20）。按 objective=A|B|C 在样本内选优，
        并返回样本外指标。无可行解时 feasible=false 且带 closest，勿自动写库。"""
        ...
```

`build_tools` 列表追加三工具（建议紧挨知识库工具）。

解析 `rule_json` 失败时返回 `{"ok": false, "error": "rule_json 不是合法 JSON"}`。

- [ ] **Step 4: 跑测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest_tools.py tests/test_rule_optimize.py tests/test_rule_backtest.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 6: SYSTEM_PROMPT 指引 + 写回文案约定

**Files:**
- Modify: `backend/app/advisor/agent/graph.py`（`SYSTEM_PROMPT` 规则列表）
- Modify: `backend/tests/test_rule_backtest_tools.py`（可选：断言 prompt 含关键词；或单独轻量测）

**Interfaces:**
- Produces: SYSTEM_PROMPT 新规则 **17**（原 14–16 编号保持；新规则插在知识库规则 13 后，后续顺延也可以——**本计划采用追加为 17**，避免大范围改号）

- [ ] **Step 1: 写失败单测**

```python
def test_system_prompt_mentions_rule_backtest():
    from app.advisor.agent.graph import SYSTEM_PROMPT
    assert "compile_knowledge_rules" in SYSTEM_PROMPT
    assert "optimize_knowledge_rules" in SYSTEM_PROMPT
    assert "样本内" in SYSTEM_PROMPT or "in_sample" in SYSTEM_PROMPT
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest_tools.py::test_system_prompt_mentions_rule_backtest -v`

Expected: FAIL

- [ ] **Step 3: 追加 SYSTEM_PROMPT 规则**

在规则 13 之后追加：

```
17. 知识规则回测/调优：先询问优化目标（A 收益 / B 夏普 / C 约束下收益，默认 C）与标的（可跳过用默认池）→
   将知识起草为 rule_json 并调用 compile_knowledge_rules →
   再 run_rule_backtest 或 optimize_knowledge_rules →
   必须同时向用户展示样本内与样本外指标；objective=C 且 feasible=false 时说明无可行解，不得写库 →
   写回知识库：默认新建（标题可加「（回测优化）」），正文含自然语言结论 + 样本内外指标 + RuleSpec 附录，
   经 save_knowledge(confirm=false) 预览，用户确认后再 confirm=true。未确认不得声称已写入。
```

写回正文模板（供 Agent 遵循，非代码强制）：

```markdown
## 结论
...

## 样本内
- total_return / sharpe / max_drawdown / trade_count

## 样本外
- ...

## 附录 RuleSpec
```json
{...}
```
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_rule_backtest_tools.py tests/test_rule_optimize.py tests/test_rule_backtest.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

## Self-review（对照 spec）

| Spec 要求 | 任务 |
|-----------|------|
| NL → DSL → 机械回测 | Task 1–3；Agent 起草 + compile 校验 |
| 目标 A/B/C，默认 C | Task 4 + Task 5 工具参数 |
| 指定标的或默认池 | Task 3 `resolve_symbols` |
| 写回预览确认 | Task 6 + 复用 `save_knowledge`（不新建写库工具） |
| 样本内/外报告 | Task 3 `segment=all` + Task 4 optimize 返回 |
| 拆分三工具 | Task 5 |
| ≤20 试验 / 硬上限 30 | Task 4 + config |
| min trades=5 | Task 4 + config |
| 非目标：委员会/逐日 LLM/前端 | 未列入任务 |

**占位符扫描：** 无 TBD；`resolve_symbols` 内 `build_universe` 实现时对照 `backtest.iter_backtest_summary_events` 抽样循环抄写，避免悬空 API。

**类型一致性：** `validate_rule_spec` → `run_rule_backtest_report` → `optimize_rules` → 工具层均使用同一 normalized `dict` RuleSpec。

---

## 验收对照（手工）

1. 对话：「用这段知识回测并优化」→ Agent 问目标 → compile → optimize → 展示双栏指标  
2. `list_knowledge` 取 id → compile(knowledge_id=...) → optimize  
3. `symbols="510300,510500"` 可跑通  
4. C 无解 → `feasible=false`，Agent 不写库  
5. `save_knowledge(confirm=false)` 仅预览  
6. `trials_run ≤ max_trials`
