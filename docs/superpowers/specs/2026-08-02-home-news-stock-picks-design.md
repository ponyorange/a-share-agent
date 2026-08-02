# 首页 Agent 解读：资讯驱动观察股

日期：2026-08-02  
状态：已确认设计（待用户审阅 spec 正文）  
关联：`docs/superpowers/specs/2026-08-02-home-news-brief-design.md`

## 目标

在首页「Agent 解读」中强化 **资讯驱动观察股**：Agent 根据今日资讯（及可选联网舆情）自行判断未来约 **3–5 个交易日** 可能有势头的标的，**最多 5 只、证据不足可更少**。

**禁止**使用「今日关注」多因子推荐及其归档作为选股输入或工具来源。

## 已确认决策

| 项 | 决策 |
|----|------|
| 选股方式 | 资讯 + 工具查标的；开启时可用 `web_research` |
| 数量 | 目标最多 5；有几只出几只，禁止无依据硬凑 |
| 时间窗口 | 未来约 3–5 个交易日 |
| 实现结构 | 两段流水线：简报 LLM → 选股小 Agent |
| 与今日关注 | 硬隔离：工具黑名单排除推荐类 API |

## 非目标

- 不读取 / 不展示今日关注列表或推荐分
- 不自动下单、不加自选、不改策略
- 不做盘中 SSE 推送观察股
- 不引入完整主 Agent 会话到首页刷新
- 不把观察股表述为必涨或投资建议（沿用页脚免责）

## 架构

### 刷新解读两段流水线

1. **简报段（现有）**  
   共享新闻包 →（可选 web）→ 单次 LLM JSON → `summary` / `bullets` / `sectors`

2. **选股段（新增）**  
   输入：截断资讯包 + 简报 `sectors` + 可选 web 要点  
   进程：短生命周期 ReAct Agent + **工具白名单**  
   输出：解析 JSON `symbols[]`（≤5）写入同一 `home_news_briefs` 文档

选股段失败时：简报仍可 `status=ready`，`symbols=[]`，可选 `symbols_note`（如「证据不足」）。仅当简报段失败才将整次任务标为 `failed`。

### 工具白名单（允许）

- `delegate_data_agent` 与/或 Python 沙箱：行业/概念成分、涨跌等（不读推荐库）
- `get_leaderboard_brief`：涨幅/资金榜作势头旁证
- `get_stock_quotes`、`fetch_symbol_daily_ma`、`fetch_stock_news`
- 用户开启联网时：`web_research` 及已挂载 web 工具

### 工具黑名单（硬排除）

- `get_today_recommendations`
- `get_recommendation_archive`
- `list_recommendation_dates`
- 持仓 / 纸交易 / 策略改写 / 盯盘写入等与本任务无关的工具

实现上复用 `build_*_tools(..., exclude=...)`（或等价白名单组装），单测断言推荐类工具未挂载。

## 数据模型变更

在既有 `home_news_briefs` 上扩展：

| 字段 | 说明 |
|------|------|
| `symbols` | `{ symbol, name, reason, horizon? }[]`，`horizon` 默认语义为 `3-5d` |
| `symbols_note` | 可选；空列表或选股失败时的说明文案 |

`reason` 须能点出与今日资讯/题材的关联；服务端校验 6 位 A 股代码，无法核验则丢弃该条。

## API

路径不变：

- `GET /api/advisor/home/news-brief`
- `POST /api/advisor/home/news-brief/refresh`

响应增加可选 `symbols_note`。行为：refresh 在后台跑完两段后再置 `ready`（前端仍短轮询）。

## 前端

`HomeNewsSection` ready 态：

1. summary / bullets / 板块 chips（不变）
2. 标题改为 **「资讯驱动观察股」**
3. 副文案：`基于今日资讯 · 观察窗口约 3–5 个交易日 · 非投资建议`
4. 列表展示代码、简称、理由；可选链到 K 线
5. 0 只：展示 `symbols_note` 或「暂无足够证据的观察股」
6. 不出现「今日关注」字样或推荐分字段

交互：默认不自动 refresh；`running` 禁用「刷新解读」。

## 测试

- 选股工具组装：推荐类工具不在挂载列表中
- 解析：非法代码丢弃；≤5 截断；空列表 + `symbols_note` 合法
- 选股段抛错时 brief 仍可为 ready 且 `symbols=[]`
- 前端：有 symbols 时展示新标题；空列表展示空态文案；无自动 POST refresh

## 验收标准

1. 刷新后可出现 ≤5 只资讯关联观察股，理由含题材/资讯线索  
2. 服务端选股路径未使用今日关注数据或推荐工具  
3. 证据不足时允许空列表且模块仍 ready  
4. 单源/选股失败不导致整页空白  

## 实现备注

- 优先在 `home_news_brief.py` 拆出选股步骤，避免拖入完整聊天会话存储  
- Agent 轮次/超时设上限（实现计划里给具体值），超时按选股失败降级为空列表  
- 文案避免「必涨」「稳赚」；用「观察 / 势头 / 研究参考」  
