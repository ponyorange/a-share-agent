# A 股市场状态总闸门与打板情绪体系设计

日期：2026-08-02  
状态：已确认设计（待用户审阅 spec 正文）

## 目标

1. 产出可解释的 **A 股市场状态**（趋势档 + 打板情绪周期），作为顾问推荐与 Agent 的**总闸门**（先定能不能干、干多大，再谈选股）。
2. 半硬策略可配置、可审计：危险阶段压仓位 / 缩池 / 偏观望；用户显式「仍要看票」可 override。
3. 打板情绪达到交易员可用的完整度（不止涨停列表展示）：家数、封炸、高度、晋级率、温度、周期。
4. 与现有 `limitup` 展示、规则推荐、Agent 工具打通；状态由规则+指标算出，LLM 只解释与编排。

## 已确认决策

| 项 | 决策 |
|----|------|
| 主价值链 | 市场状态总闸门 |
| 闸门硬度 | 半硬（B）：危险阶段自动压仓位/缩池/偏观望；用户显式「仍要看票」放开 |
| 信号底座 | 轻量趋势混合（C）+ **完整打板情绪体系** |
| 交付策略 | 方案 3：同一版本一次交付趋势包、打板情绪包、半硬闸门、Agent/推荐接入、仪表盘 |
| 状态主判据 | 规则+指标；禁止 LLM「感觉」当主分 |
| 与投委会关系 | 不替换委员会硬风控；闸门作用在对话/推荐层 |

## 非目标

- 实盘自动下单 / 券商跟单
- 美股 / 港股 regime
- 账户级收益教练、错题本飞轮（后续子项目）
- 用 LLM 网页舆情当情绪主输入
- 替换投委会 `risk_limits`
- 龙虎榜席位博弈作为状态主输入
- 逐笔封单 / 盘口微观结构

## 状态机

两轴合成，避免「情绪高潮但指数破位」误判：

```text
trend_regime     ∈ { uptrend, range, downtrend }
sentiment_cycle  ∈ { ice, repair, strengthen, climax, ebb }
        │
        ▼
gate_level       ∈ { aggressive, normal, defensive, risk_off }
position_cap     ∈ [0, 1]    # 建议总仓位上限
pool_policy      ∈ { full, shrink, defense_only }
```

### 默认合成表（可配置）

| 趋势 \ 情绪 | ice / repair | strengthen | climax | ebb |
|-------------|--------------|------------|--------|-----|
| uptrend | normal | aggressive | normal（附防守提示） | defensive |
| range | defensive | normal | defensive | risk_off |
| downtrend | risk_off | defensive | risk_off | risk_off |

### 半硬动作

| `gate_level` | 行为 |
|--------------|------|
| `aggressive` | 仓位上限高，推荐池完整 |
| `normal` | 默认上限；附 regime 元数据 |
| `defensive` | 压 `position_cap`、缩池或降买入阈值，话术偏持有/观望 |
| `risk_off` | 默认不推新买入，只给防守/风控；`override=true` 时按 `defensive` 出池并强制 `warnings[]` |

- 趋势轴以日线 / 多日宽度为主，避免分钟噪声乱切。
- 情绪轴允许盘中更新，须带平滑或滞回，避免周期分钟级乱跳。

## 指标与数据来源

原则：状态可复现；复用 `get_market_score`（北向/指数趋势/宏观）与 `limitup`（涨停+炸板）；补齐历史序列与情绪衍生指标。

### A. 趋势包（轻量混合）

| 指标 | 用途 | 主要来源 |
|------|------|----------|
| 基准指数位置 | 相对 MA20/MA60、距阶段高点回撤 | 现有指数日线 / 扩展 `fetch_benchmark_trend_score` |
| 量能趋势 | 两市成交额 vs 5/20 日均 | AKShare 市场成交额类接口 |
| 上涨宽度 | 上涨家数占比、涨跌比 | AKShare 涨跌家数；失败则降级为行业涨跌宽度 |
| 北向/外资 | 辅助，不单独定闸门 | 现有 `fetch_northbound_net_score` |
| 主线强度（轻） | Top 行业相对强弱是否集中 | 现有 `fetch_industry_strength_map` |

产出：`trend_regime` + 分项证据。

### B. 完整打板情绪包

| 指标 | 定义 | 数据 |
|------|------|------|
| 涨停 / 跌停家数 | 情绪温度基础 | `stock_zt_pool_em` + `stock_zt_pool_dtgc_em` |
| 封板率 | 仍封 /（涨停+炸板） | 涨停池 + `stock_zt_pool_zbgc_em` |
| 炸板率 | 1 − 封板率 | 同上 |
| 连板高度 | 当日最高连板数 | 现有 ladder `board_count` max |
| 高度板数量 | ≥N 连板只数（N 可配，默认 3） | ladder |
| 晋级率 | 今日 k 连 / 昨 k−1 连 | **按交易日归档的涨停快照** |
| 空间板质量 | 高位板晋级 vs 断层 | 归档序列 + ladder |
| 回封/烂板粗分 | 曾炸后是否回封（尽力） | 盘中快照或东财字段；无则 `degraded` |
| 情绪温度分 | 0~1 综合分 | 上述加权 |
| 情绪周期 | ice→repair→strengthen→climax→ebb | 温度 + 高度 + 晋级率规则机 |

**工程硬需求：** 落库按交易日的涨停/炸板/跌停摘要快照（不能仅靠 6s 内存缓存），否则晋级率与周期无法稳定计算。盘后定版；盘中用 intraday 快照刷新情绪轴。

### C. 数据质量

| `data_quality` | 含义 | 闸门行为 |
|----------------|------|----------|
| `ok` | 核心指标齐 | 按合成表 |
| `degraded` | 缺历史晋级率/跌停池等 | 仍给 `gate_level`，更保守，证据标明缺项 |
| `failed` | 涨停核心源失败 | 不编造周期；回退 `defensive` 并明示不可用 |

本版不做：逐笔封单微观结构、龙虎席位主输入、LLM 网页情绪主分。

## 架构

```text
AKShare 涨停/炸板/跌停 + 指数/宽度/量能 + 北向/行业
                    │
                    ▼
         regime/collector（采集 + 日归档）
                    │
                    ▼
         regime/engine（规则状态机 → MarketRegime）
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   REST API    推荐改写层     Agent 工具
   仪表盘      apply_regime_gate
```

模块位置：`backend/app/advisor/regime/`（`collector` / `engine` / `store` / `gate`）。

与现有模块关系：

- `limitup.py`：继续服务打板 UI；regime collector 可复用其解析逻辑，避免两套列名兼容分叉失控。
- `market_context.get_market_score`：过渡期保留；推荐路径以 regime 为准；`market_score` 提供兼容映射，避免双轨打架。
- 投委会硬风控不变；委员会只读 regime 作上下文为可选，本版非必须。

## 数据模型

### 日归档（Mongo，概念名 `market_regime_daily`）

每交易日一条：日期、涨停/炸板/跌停家数、高度、晋级率输入、宽度/量能摘要、定版 `MarketRegime`、`created_at` / `updated_at`。  
索引：`trade_date` 唯一。

### 盘中快照

短缓存（内存或短 TTL 文档）：同上字段 + `as_of`；周期性可落库供复盘，但不替代日定版。

### `MarketRegime` 对外结构

```json
{
  "as_of": "2026-08-02T10:00:00+08:00",
  "trade_date": "2026-08-02",
  "trend_regime": "range",
  "sentiment_cycle": "ebb",
  "sentiment_score": 0.32,
  "gate_level": "risk_off",
  "position_cap": 0.25,
  "pool_policy": "defense_only",
  "data_quality": "ok",
  "evidence": [{"key": "limit_up_count", "value": "12", "note": "..."}],
  "override_allowed": true
}
```

涨跌幅类数字若出现在证据或下游推荐中，继续遵守产品约定：比例用小数（`0.10` = 10%），对用户展示用百分数。

## API

均需登录（与顾问 API 一致）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/advisor/regime/current` | 当前状态（盘中刷新；盘后用定版日） |
| GET | `/api/advisor/regime/history?limit=` | 近 N 日周期/闸门 |
| GET | `/api/advisor/regime/sentiment` | 打板情绪分项明细 |

推荐相关 API 增加查询参数：`regime_override=true`（用户/Agent 显式才带）。

在 `get_recommendations` / 快照写出前调用：

`apply_regime_gate(recs, regime, *, override=false)`

| 条件 | 改写 |
|------|------|
| `aggressive` / `normal` | 不改或仅附 `regime` 元数据 |
| `defensive` | 下调买入类 action、提高 buy 阈值、截断 TopK、写入 `position_cap` |
| `risk_off` 且非 override | 买入→观望；`pool_policy=defense_only`；`gate_blocked_buys=true` |
| `risk_off` 且 override | 按 `defensive` 出池 + 强制 `warnings[]` |

## Agent 接入

### 工具

- `get_market_regime`：仓位 / 能否买 / 大盘情绪类问题优先调用
- `get_sentiment_dashboard`：需要打板分项细节时调用

`get_today_recommendations`（及等价路径）支持 `override`；与 HTTP `regime_override` 语义一致。

### System prompt 硬规则（增补）

1. 买卖/仓位类问题先 `get_market_regime`。
2. `risk_off` 且用户未要求「仍要看票」→ 不主动推买入名单。
3. 用户明确 override → 工具带 `override=true`，并复述风险与 `position_cap`。
4. 展示须含：阶段、仓位上限、`data_quality`、1～3 条 evidence。

定时任务：本版交付 **一条默认早盘 regime 简报** 的 `run_at` 任务模板（prompt 固定读 `get_market_regime` 并摘要；复用现有 monitor 邮件）。用户可在定时任务页启用/改时间；不强制自动创建到每个老用户账户。

## 配置

在 `advisor/config.yaml` 增加 `regime:` 节（不另拆文件），包含：

- 双轴合成表
- `position_cap` 按 `gate_level` 映射
- 缩池 K、买入阈值上调幅度
- 晋级率窗口、高度板阈值 N
- 情绪周期阈值与滞回
- 采集/缓存 TTL

修改后生效方式与现网 config 一致（重启或热载）。

## 前端

### 市场状态仪表盘（`frontend-advisor`）

- 顾问**主导航**新增「市场状态」路由页（不只做 Agent 内嵌卡片，避免入口过深）。
- 首屏一构图：`gate_level` 文案 + `position_cap` + 趋势/情绪双轴标签。
- 次级：情绪分项 + 近 N 日周期；`data_quality !== ok` 显著提示。
- `risk_off` 提供「仍要看今日关注」→ `regime_override=true`。

### 打板页

- 保留现有涨停/连板；顶部增加情绪周期条 + 温度分，可深链状态页。
- 完整历史图不塞打板首屏。

### Agent / 推荐面板

- 对话无单独炫技 UI；决策卡：结论 → 闸门 → 证据 → 动作。
- 今日关注展示 `regime` 角标与 override 入口。

## 测试要点

- 合成表：给定 trend + sentiment → 期望 `gate_level`
- 半硬：`risk_off` 无 override 无新买入；有 override 有 warnings
- 缺历史 → `degraded` 且更保守
- 核心源失败 → `failed`，不编造周期
- Agent 工具字段稳定；比例小数约定不变
- API：current / history / sentiment 鉴权与基本响应形状

## 验收标准

| # | 标准 |
|---|------|
| 1 | `/regime/current` 返回双轴 + `gate_level` + `position_cap` + `evidence` |
| 2 | 交易日归档写入；至少连续 2 个交易日数据后晋级率可算 |
| 3 | 打板情绪分项可查（家数、封/炸、高度、晋级、温度、周期） |
| 4 | `risk_off` 默认推荐无新买入；override 后可看池且带 warnings |
| 5 | `defensive` 缩池或降买入阈值，响应含 `position_cap` |
| 6 | Agent 具备 `get_market_regime`；仓位/能否买类问题先走闸门 |
| 7 | 源缺失 → `degraded`/`failed`，行为更保守 |
| 8 | 状态仪表盘 + 打板页情绪条可见；合成表可配置 |
| 9 | 单测覆盖合成、闸门改写、降级；关键 API/工具有测试 |

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 东财接口不稳 / 字段漂移 | 列名兼容解析；失败降级；缓存 + 日归档 |
| 方案 3 体量大 | 模块边界固定（collector/engine/gate/UI）；按验收表逐条勾 |
| 情绪规则主观过拟合 | 阈值进 config；证据可解释；本版不自动改阈值 |
| 与旧 `market_score` 双轨 | 兼容映射 + 推荐路径以 regime 为准 |
| 盘中周期抖动 | 趋势日频为主；情绪平滑/滞回 |

## 范围冻结

**做：** 趋势包 + 完整打板情绪 + 日归档 + 半硬闸门 + 推荐改写 + Agent 工具/prompt + 状态仪表盘 + 打板页情绪条 + 配置与测试。

**不做：** 见「非目标」。
