# 知识规则回测与调优

日期：2026-07-25  
状态：已确认设计

## 问题

用户希望把一段知识（或知识库条目）交给 Agent，用历史数据回测，并迭代调整决策以提升收益，最后把高收益知识写回知识库；也可指定某条知识做测试/调优。  
现有能力缺口：

- Agent **不能**调用回测工具  
- 知识库是自然语言，回测引擎吃的是可执行信号/参数  
- 无「编译 → 回测 → 搜索 → 写回」闭环  

## 目标

1. 自然语言知识 → **轻量规则 DSL** → 机械历史回测（可复现）  
2. 有限次参数搜索，按用户可选目标优化（默认综合目标 C）  
3. 支持指定标的，或默认抽样池  
4. 优化结果经预览确认后写入知识库（复用 `save_knowledge`）  
5. 报告同时给出样本内最优与样本外表现，降低过拟合误导  

## 非目标

- 每天用 LLM 读自然语言下单再回测（方案 A）  
- 委员会级组合回测 / AKQuant 强制对账门禁  
- 向量检索、全市场无上限穷举  
- 无确认自动写库  
- 保证实盘盈利（仅研究工具）  

## 方案概要

采用 **混合路径（NL → DSL → 机械回测）** + **拆分 Agent 工具**：

```
知识(NL 或 knowledge_id)
  → compile_knowledge_rules → RuleSpec(JSON)
  → run_rule_backtest / optimize_knowledge_rules
  → 报告（in-sample / out-of-sample）
  → save_knowledge(confirm=false → true) 写回
```

## 对话约定

开始前 Agent 询问（用户可跳过用默认）：

| 项 | 默认 |
|----|------|
| 优化目标 | **C**（见下） |
| 标的 | 未指定则用默认抽样池 |
| 写回 mode | 询问；未指定默认 `on_demand` |

优化结束后：先展示预览（自然语言总结 + DSL + 指标），用户确认后再落库。

## 优化目标

用户可选：

- **A.** 最大化 `total_return`  
- **B.** 最大化 `sharpe`  
- **C（默认）.** 在 `sharpe ≥ min_sharpe` 且 `max_drawdown ≤ max_dd` 下最大化 `total_return`  
  - 默认：`min_sharpe=0`，`max_dd=0.25`（询问时可改）  
  - 无可行解：返回「约束下无解」+ 最接近解，**不**自动写库  

## 规则 DSL（MVP）

JSON `RuleSpec`，字段示例：

```json
{
  "version": 1,
  "name": "短线动量过滤",
  "action": "buy",
  "hold_days": 1,
  "entry": {
    "all": [
      {"factor": "mom_5", "op": ">=", "value": 0.03},
      {"factor": "ma20_bias", "op": ">", "value": 0.0},
      {"factor": "vol_ratio", "op": ">=", "value": 1.2}
    ]
  },
  "exit": {
    "any": [
      {"type": "hold_days"},
      {"type": "stop_loss", "value": 0.05}
    ]
  },
  "source_knowledge_id": null,
  "natural_language_summary": "…"
}
```

**MVP 允许的 factor**（复用 `compute_factors` 已有字段）：  
`mom_1`, `mom_5`, `mom_10`, `mom_20`, `ma20_bias`, `vol_z`, `vol_ratio`, `rs_300`, `low_vol`（以 `features.py` 实际输出为准）。

**op：** `>`, `>=`, `<`, `<=`, `between`（between 时 `value` 为 `[lo, hi]`）。

**动作：** MVP 以 **多头信号** 为主：`action=buy`，持有 `hold_days`（默认 1，对齐现有次日事件研究）；可选简易 `stop_loss` / `take_profit`。  
空头/复杂组合仓位本版不做。

**编译失败：** 返回需澄清的字段，不进入回测。

## 回测引擎（MVP）

新模块建议：`backend/app/advisor/rule_backtest.py`（或 `knowledge_rules/`）。

对每个标的、每个交易日（可 `sample_step`）：

1. 计算当日因子  
2. `entry` 全满足 → 开仓（或记信号）  
3. 按 `hold_days` / 止盈止损平仓，累计收益与交易  

聚合指标：`total_return`, `max_drawdown`, `sharpe`, `hit_rate`, `trade_count`, `sample_count`；可选简短 equity 摘要。

**数据：** `fetch_daily_df` + `compute_factors`；lookback 默认 `config.backtest.lookback_bars`。

**标的池：**

- 用户指定 `symbols[]`，或  
- 默认：复用简单回测宇宙抽样逻辑（`config.backtest.boards` + 每板上限；MVP 可略收紧如每板 ≤ 8 以控耗时）

**分段：**

- 时间序前 **70%** 为调参集（in-sample）  
- 后 **30%** 为验证集（out-of-sample，不参与选参）  
- 最终报告两者都展示  

**无效试验：** `trade_count < 5`（可配置）视为无效，不参与最优选取。

## 优化搜索

`optimize_knowledge_rules`：

- 在 RuleSpec 的数值参数邻域内搜索（阈值、`hold_days` 等）  
- **最多 20 次**试验（可配置，硬上限建议 30）  
- 按选定目标在 **in-sample** 选最优，再在 **out-of-sample** 评估一次并返回  
- 超时/耗尽返回当前最优 + `truncated: true`

不在本版做贝叶斯优化；随机/网格邻域即可。

## Agent 工具

| 工具 | 职责 |
|------|------|
| `compile_knowledge_rules` | 输入 NL 或 `knowledge_id` → `RuleSpec` 草稿（可 `confirm` 仅预览语义，编译本身不落库） |
| `run_rule_backtest` | `RuleSpec` + symbols/目标区间 → 单次指标（可指定是否只用 in-sample） |
| `optimize_knowledge_rules` | `RuleSpec` + objective + symbols → 试验日志 + 最优规则 + in/out 指标 |
| `save_knowledge` | 现有；写回须 `confirm=true` |

**SYSTEM_PROMPT 指引：**  
用户要求知识回测/调优时：先问目标与标的（可默认）→ compile → optimize/run → 展示报告 → 预览写库 → 确认后保存。未确认不得声称已写入。

## 写回格式

- 标题：原标题 + `（回测优化）` 或用户指定  
- 正文建议结构：  
  1. 自然语言结论（何时买/卖、关键阈值）  
  2. 样本内 / 样本外关键指标  
  3. 附录：最优 `RuleSpec` JSON  
- `mode`：用户指定，否则默认 `on_demand`  
- 可把 `source_knowledge_id` 写入 DSL 元数据；是否覆盖原条目由用户决定（默认 **新建**，避免误改）

## 模块落点（预期）

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/rule_backtest.py`（新） | DSL 校验、单标的仿真、聚合、分段 |
| `backend/app/advisor/rule_optimize.py`（新） | 邻域搜索 / 试验预算 |
| `backend/app/advisor/agent/tools.py` | 注册三工具 |
| `backend/app/advisor/agent/graph.py` | SYSTEM_PROMPT |
| `backend/tests/test_rule_backtest.py` 等 | 规则匹配、分段、目标选择、预算 |

前端本版不改（纯 Agent 对话驱动）。

## 验收

1. 「用这段知识回测并优化」→ 询问目标（默认可跳过）→ 得到含样本内外指标的报告  
2. 指定知识库 id 调优 → 能 compile + optimize  
3. 指定 2～3 只标的可跑通；不指定则用默认池  
4. 目标 C 下无解时明确说明，不写库  
5. 写库必须预览确认；`confirm=false` 不落库  
6. `optimize` 试验次数不超过配置上限  

## 风险与说明

- DSL 表达力有限：无法覆盖全部自然语言知识；编译时应诚实说明「无法结构化」的部分  
- 样本外仍可能过拟合；报告必须双栏展示  
- 默认池回测耗时与标的数相关，需预算与进度（可复用 subagent_progress 或 tool 内阶段性返回）  

## 明确不在本版

委员会回测、LLM 逐日决策回测、自动覆盖原知识、实盘下单。
