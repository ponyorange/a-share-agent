# 规则引擎：量比自由 N + 阴阳因子

日期：2026-07-25  
状态：已确认设计

## 问题

知识规则「缩量阴不接 / 缩量阳不卖」依赖：

1. 成交量相对前 N 日均量（N 可自由指定）  
2. 阴阳线（收盘 vs 开盘）

现有 DSL 虽有 `vol_ratio`（固定前 5 日），但 Agent 常用 `volume` / `volume_ratio` 等别名被拒；且无阴阳因子，无法表达该知识。

## 目标

1. 支持 `is_yin` / `is_yang`（收盘 vs 开盘）  
2. `vol_ratio` 支持条件级自由 `lookback`（N），默认 5  
3. 编译时别名映射，降低 Agent 写错名的概率  

## 非目标

- 裸 `volume` / `turn` 绝对值比较（跨股不可比）  
- 「满足则禁止开仓」的 `forbid` / 过滤语义（仍为「entry 满足则买入」；禁止类知识靠叙事或反向条件表达）  
- 相对昨收的阴阳定义  
- 任意因子的通用 `lookback`（本版仅 `vol_ratio`）

## 因子与条件

### 阴阳

| 因子 | 定义 | 值 |
|------|------|-----|
| `is_yin` | `close < open` | 1 / 0 |
| `is_yang` | `close > open` | 1 / 0 |

平盘（`close == open`）：二者均为 0。

示例：`{"factor":"is_yin","op":">=","value":1}`

### 量比

含义：当日 `volume` ÷ **前 N 日**均量（不含当日）。

条件字段：

```json
{"factor": "vol_ratio", "lookback": 10, "op": "<", "value": 0.8}
```

- `lookback` 可选，默认 **5**  
- 合法范围：**2 ≤ N ≤ 60**  
- 数据不足（有效 volume 根数 < N+1）→ 该条件为 false（与 NaN 行为一致）  
- 保留无 `lookback` 的旧写法，行为不变  

### 别名（validate / compile 时规范为标准名）

| 别名 | 规范名 |
|------|--------|
| `volume_ratio` | `vol_ratio` |
| `vol` | `vol_ratio` |

不接受：`volume`、`turn`（返回明确错误，提示用 `vol_ratio` + `lookback`）。

### 示例：缩量阴

```json
{
  "entry": {
    "all": [
      {"factor": "is_yin", "op": ">=", "value": 1},
      {"factor": "vol_ratio", "lookback": 5, "op": "<", "value": 1.0}
    ]
  }
}
```

说明：MVP 引擎语义仍是「条件全满足 → 开多」；「不接」类纪律需 Agent 在自然语言知识里说明，或把规则写成可交易的正向过滤（本版不新增 forbid）。

## 实现要点

| 位置 | 变更 |
|------|------|
| `features.compute_factors` | 增加 `is_yin` / `is_yang`；抽出 `volume_ratio_at(df, n)` 或等价，供仿真按条件 N 计算 |
| `rule_backtest` | `ALLOWED_FACTORS` 增加阴阳；校验 `lookback`；`eval_condition` / `simulate_symbol` 对 `vol_ratio` 按 lookback 取值 |
| `rule_optimize.perturb_rule` | 扰动 `vol_ratio` 的 value 与 lookback（夹紧 2..60） |
| `graph.SYSTEM_PROMPT` / 工具 docstring | 注明新因子、lookback、别名 |

仿真路径：每个 bar 评估 entry 时，对带 `lookback` 的 `vol_ratio` 条件按该 N 计算，不必把所有 N 预写进 factor dict。

## 验收

1. `is_yin`/`is_yang` 可编译、可回测  
2. `vol_ratio` + `lookback=10` 与默认 5 结果可区分  
3. `volume_ratio` 别名可被接受并规范为 `vol_ratio`  
4. `lookback=1` 或 `99` 返回校验错误  
5. 既有无 lookback 的 `vol_ratio` 规则行为不变  

## 明确不在本版

forbid 语义、昨收阴阳、任意因子 lookback、换手率绝对值因子。
