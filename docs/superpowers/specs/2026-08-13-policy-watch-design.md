# 政策雷达设计

日期：2026-08-13  
状态：已确认（实现计划见 `docs/superpowers/plans/2026-08-13-policy-watch.md`）

## 目标

1. 每个用户可开启「政策雷达」：监控预置官方栏目，也可自行粘贴列表页 URL。
2. 新文章全部进入站内收件箱；仅当 LLM 判断「可能影响 A 股股价」且达到该用户灵敏度时，才发邮件。
3. 邮件先报事件本身，再附可能相关的板块与个股（不是只评估自选/持仓）。
4. 用户可配置扫描时段（全天 / 仅交易时间 / 仅非交易时间）以及交易时段、非交易时段的扫描间隔。

## 已确认决策

| 项 | 决策 |
|----|------|
| 架构 | 方案 1：挂在现有 `monitor-worker` 末尾，独立模块与集合，不写入 `agent_monitor_jobs` |
| 来源 | 预置官方栏目为主，允许自定义列表页 URL（最多 8 条） |
| 发现新文 | 确定性：列表页抽链 + URL 指纹；不靠 Agent 逛官网首页 |
| 收件箱 vs 邮件 | 全量新文进列表；达灵敏度才发信 |
| 灵敏度 | `low` / `medium` / `high`，默认 `medium`，用户可改 |
| 入口 | Agent 顶栏新页 `/agent/policy-watch`（政策雷达） |
| 扫描日程 | 每用户一套：`scan_mode` + 两档间隔；不按栏目单独设频率 |
| 交易时间 | 与盯盘窗口一致：A 股交易日 **09:15–15:05**（北京时间，含午休） |
| 开启回放 | 新开雷达或新加源：当前列表只标已见，不解读、不发信 |
| 抓取/解读 | 按规范化 URL **全站共享**；每人按自己的灵敏度决定发信 |
| 对话工具 | 第一版不提供 Agent 创建/修改雷达的工具 |
| 立即重扫 | 不提供「立即全量重扫」按钮 |

## 非目标（本版）

- 短信 / App 推送 / SSE 推资讯
- 官网首页自动展开全站、无限翻页、sitemap 爬虫
- 人民网数据站等非时间线站点作为预置源（用户可自行贴栏目页）
- 对话里用 Agent 工具创建雷达
- 按每个栏目单独设频率
- 自动下单或改持仓
- 把解读表述为投资建议

## 架构

```text
用户「政策雷达」页
  开关 / 灵敏度 / 扫描日程 / 预置源 / 自定义栏目 URL
        │
        ▼
Mongo
  policy_watch_settings   每用户一份配置
  policy_watch_articles   全站共享文章 + 解读
  policy_watch_items      每用户收件箱（指向共享文章）
  policy_watch_seen       源 + URL 指纹（开启不回放、跨用户去重）
        │
        ▼
monitor-worker 每个 tick 末尾（有时间预算，可跳过）
  1. 到期的源：拉列表页（预置适配器 或 通用抽链）
  2. 与 seen 比较，筛出新 URL
  3. 精读新文（复用 fetch_url，每 tick 有篇数上限）
  4. 轻量 LLM：impact_score / 方向 / 板块 / 个股 / 摘要
  5. 对「当前处于自己扫描窗口」的订阅用户扇出 items
  6. 达到该用户灵敏度 → 已验证邮箱发信（同 tick 多篇合成一封）
```

要点：

- 发现新文是确定性流水线；LLM 只做解读和是否发信。
- 抓取与解读按 URL 共享：多人勾选同一预置源时，列表和正文只抓一次、LLM 只调用一次。
- 不挡盯盘：每 tick 最多处理有限个到期源、有限篇新文；超时或预算用尽留到下一轮。Worker 仍盘中约 25 秒、盘后约 60 秒转一圈；雷达源的扫描间隔由用户配置，且受全局地板约束。
- 发信前提：用户已绑定并验证邮箱。未验证则只进收件箱。
- 无 DeepSeek Key：可收录标题，不解读、不发信。

## 预置源与发现

第一版预置四个源。栏目 URL 写在 `config.yaml`，站点改版只改配置/适配器。

| ID | 名称 | 取列表方式 |
|----|------|------------|
| `gov_zhengce` | 中国政府网 · 最新政策 | 固定栏目页（默认 `https://www.gov.cn/zhengce/zuixin/`），适配器抽标题+链接 |
| `scio_news` | 国新办 · 新闻发布 | 固定栏目页（URL 写在 yaml），同样抽链 |
| `cctv` | 新闻联播 | 复用 `fetch_market_cctv_news`，不爬网页 |
| `macro` | 宏观政策快照 | 复用 `fetch_macro_china_snapshot` |

用户首次打开设置时的默认勾选：`gov_zhengce`、`scio_news`。`enabled` 默认为 `false`，需用户显式开启。

### 自定义栏目页

- 每用户最多 **8** 条。
- 只接受 `http`/`https`，复用现有 SSRF 校验（禁私网/本机、端口仅 80/443）。
- 通用抽链：抓该页 → 抽出同站、像文章的链接（路径含日期 / `content` / `zhengce` 等，或标题足够长）→ 取前 **20** 条。
- 抽不到链接：该源记 `last_error`（例如「该页不像列表，请换栏目 URL」），不造假新闻。
- 用户只贴官网首页且抽链质量差：同样记失败并提示改贴栏目页。不承诺从首页展开全部栏目。

### 何谓「新」

- 指纹主键：规范化 URL（去掉常见追踪参数 `utm_*`、`from`、`spm` 等）。辅键：同一 `source_key` 下标题归一化（去空白、全半角）用于相似去重。
- `source_key`：预置为 preset id；自定义为列表页规范化 URL。
- 写入 `policy_watch_seen`。刚开启或新加源：把**当前**列表标成已见，不精读、不解读、不发信。
- 正文抓取复用 `fetch_url`（含 Scrapling / 无头升级）。PDF / 纯附件：只保留标题+链接，LLM 按标题判断，条目与邮件注明「未取到正文」。
- 每 tick 全局最多精读 **5** 篇新文（`policy_watch.max_fetch_per_tick`）。

## 扫描日程

每用户一套，不按栏目再拆。

| 字段 | 默认 | 限制 |
|------|------|------|
| `scan_mode` | `always` | `always` / `trading_only` / `offhours_only` |
| `interval_trading_min` | 15 | 夹到 **5–180** |
| `interval_offhours_min` | 60 | 夹到 **15–360** |

页面：`scan_mode=trading_only` 时灰掉非交易间隔；`offhours_only` 时灰掉交易间隔。间隔不是整数 → 400；越界则**夹紧到上下限并回写**（例如 4 → 5）。

**交易时间：** A 股交易日 09:15–15:05（北京时间），含午休。周末、节假日、该窗口之外均为非交易时间。用现有交易日历，不另维护一份。

**窗口与扇出：**

- 用户当前不在自己的 `scan_mode` 窗口内：不往其收件箱扇出、不发信。共享抓取仍可能因其他用户到期而进行。
- 进入窗口后：把「共享库里他尚未收入件箱的新文章」补进 items；是否发信仍看当时灵敏度与去重。周末积压会在周一开盘后出现在「仅交易时间」用户的列表里，达标则进当次汇总邮件。
- 全站抓取间隔：取「当前窗口内、开启了该源的用户」适用间隔的**最小值**，再与全局地板取 max。地板默认：交易 5 分钟、非交易 15 分钟（yaml 可调）。防止单用户把 worker 打满。
- 预置源与自定义栏目共用该用户的两档间隔。

## LLM 判断与灵敏度

LLM 不找新闻，只对已确认的新文章打分。使用该文章**首次需要解读时**对应订阅用户的 DeepSeek Key（任选一名已配置 Key 且当时需要解读的用户）；结果写入共享 `policy_watch_articles`。若当时没有任何订阅用户配置 Key：文章可先以标题入库，`interpret_status=pending`，待有 Key 的用户进入窗口再解读。

输入：来源名 + 标题 + 正文（截断到 `max_article_chars`，默认 8000）+ 固定分类说明。  
输出必须是可解析 JSON：

| 字段 | 含义 |
|------|------|
| `impact_score` | 0–1 |
| `direction` | `up` / `down` / `mixed` / `unclear` |
| `summary` | 一句话：先事件，再市场含义 |
| `sectors` | `{ name, reason }[]`，最多 5 |
| `symbols` | `{ symbol, name, reason, direction }[]`，最多 8 |
| `category` | `policy` / `regulation` / `macro` / `news` / `other` |

个股代码用现有行情/名称表核对；对不上的标「待核实」或丢弃，禁止编造代码。  
解读失败：`interpret_status=failed`，再试 1 次（记 `interpret_attempts`）；仍失败则条目可进收件箱但不发信。

**发信阈值（默认中）：**

| 档 | 条件（需同时满足） |
|----|-------------------|
| 低 | `impact_score ≥ 0.75` 且 `category ∈ {policy, regulation, macro}` |
| 中 | `impact_score ≥ 0.50` |
| 高 | `impact_score ≥ 0.30` 且至少有一个板块或一只**已核实**个股 |

同一 `(user_id, url_key)` 终身只发一次。同一用户、同一 `source_key`、标题高度相似、24 小时内已发过 → 只进列表不发信。  
同一 tick、同一用户多篇达标：**合成一封**。  
用户改严灵敏度：不撤回已发邮件，只影响之后新扇出的文章。  
用户关掉某源：已进收件箱的保留，不再扇出该源新文。

## 邮件

直接发送，不走聊天 `confirm`。主题与盯盘/定点任务前缀区分：`[政策雷达]`。

```text
主题：[政策雷达] 利好/利空/分化/影响不明 · {短标题}
      （同 tick 多篇： [政策雷达] {N}条可能影响市场 · {首条短标题}）

来源：中国政府网 · 最新政策
原文：https://...

{一句话摘要}

可能方向：利好
相关板块：……
相关个股：300750 宁德时代（待核实则标明）

未取到正文时注明「仅依据标题」。
免责：研究参考，不构成投资建议。
```

方向文案：`up`→利好，`down`→利空，`mixed`→分化，`unclear`→影响不明。  
SMTP 失败：该批 items 标 `notify_failed`，`settings.last_error` 可见；**不在下一 tick 自动重发**（避免失败重试变成邮件洪水）。用户可在页上看到失败标记。

## 数据模型

### `policy_watch_settings`（每用户一份，`user_id` 唯一）

```text
user_id
enabled: bool                         # 默认 false
sensitivity: "low" | "medium" | "high"
scan_mode: "always" | "trading_only" | "offhours_only"
interval_trading_min: int
interval_offhours_min: int
preset_ids: [str]
custom_sources: [{ id, url, title? }]
notify_email: str | null              # 开启时快照已验证邮箱；未验证则为 null
last_fanout_at: datetime | null       # 该用户上次扇出时间（用于间隔）
last_error: str | null
created_at, updated_at
```

源级状态（上次成功/失败）可嵌在 settings 的 `source_status: { source_key: { last_ok_at, last_error } }`，避免再开一张用户×源表。

### `policy_watch_articles`（全站共享，`url_key` 唯一）

```text
url_key, url, title
source_key, source_label
body_excerpt: str | null
body_ok: bool                         # false = 仅标题
interpretation: { ... } | null
interpret_status: "pending" | "ready" | "failed"
interpret_attempts: int
fetched_at, interpreted_at
```

### `policy_watch_items`（每用户收件箱，`(user_id, article_id)` 唯一）

```text
user_id, article_id
created_at
read_at: datetime | null
notified_at: datetime | null
notify_status: "skipped" | "sent" | "failed"
# skipped = 未达灵敏度、未验证邮箱、或无可用解读
```

### `policy_watch_seen`

```text
source_key, url_key, title_norm, first_seen_at
```

索引：`settings.user_id` 唯一；`articles.url_key` 唯一；`items (user_id, created_at desc)`；`items (user_id, article_id)` 唯一；`seen (source_key, url_key)` 唯一。

## API

均需登录。前缀 `/api/advisor/policy-watch`。

| 方法 | 路径 | 行为 |
|------|------|------|
| GET | `/presets` | 预置源目录：`id / name / description / list_url?`（结构化源可无 `list_url`） |
| GET | `/settings` | 当前用户配置；无文档则返回上述默认值（不强制先写库） |
| PUT | `/settings` | 局部更新：开关、灵敏度、扫描日程、`preset_ids`、增删后的完整 `custom_sources` |
| GET | `/items` | 收件箱。查询：`filter=all\|emailed\|inbox`（inbox=未发信），`cursor`，`limit`（默认 30，最大 50） |
| POST | `/items/{id}/read` | 标已读 |

PUT 校验：

1. `custom_sources` 超过 8 条 → 400。
2. URL 非法或 SSRF → 400。
3. 间隔非整数 → 400；越界则夹紧并回写（与测试「4 → 5」一致）。
4. `enabled=true` 不强制已有邮箱或 DeepSeek Key；页上分别提示能力降级。
5. `enabled` 从 false→true，或新增源：对该源做一次「只标已见」的种子扫描（可在 worker 下一 tick 完成，设置里 `source_status` 标 `seeding`）。

GET items 返回文章标题、来源、时间、解读摘要、方向、分数、板块、个股、原文链接、`notify_status`、`read_at`。不回显完整正文（列表够用；需要看原文走外链）。

## 全局配置（`config.yaml` 的 `policy_watch`）

```yaml
policy_watch:
  max_custom_sources: 8
  max_list_links: 20
  max_fetch_per_tick: 5
  max_sources_per_tick: 4
  max_tick_seconds: 8
  max_article_chars: 8000
  similar_title_hours: 24
  interval_trading_min: 5
  interval_trading_max: 180
  interval_offhours_min: 15
  interval_offhours_max: 360
  default_interval_trading: 15
  default_interval_offhours: 60
  trading_start: "09:15"
  trading_end: "15:05"
  presets:
    gov_zhengce:
      name: 中国政府网 · 最新政策
      list_url: https://www.gov.cn/zhengce/zuixin/
    scio_news:
      name: 国新办 · 新闻发布
      list_url: https://www.scio.gov.cn/xwfb/
    cctv:
      name: 新闻联播
      via: fetch_market_cctv_news
    macro:
      name: 宏观政策快照
      via: fetch_macro_china_snapshot
```

用户开关与个人间隔不进 yaml。

## UI

路由：`/agent/policy-watch`。  
顶栏 `AGENT_NAV_LINKS`：在「定时任务」后增加「政策雷达」。

```text
[ 开启雷达 ]   灵敏度 低/中/高    邮箱：已验证 xxx@ / 去 /account 绑定
扫描时段：全天 | 仅交易时间 | 仅非交易时间
交易间隔：N 分钟    非交易间隔：N 分钟
DeepSeek：已配置 / 去 /agent/settings（未配置则不解读、不发信）

预置：☑ 政府网最新政策  ☑ 国新办  ☐ 联播  ☐ 宏观快照
自定义栏目：[ URL ] [添加]   已加列表可删
各源状态：上次成功时间 / seeding / 失败原因

收件箱  [全部] [已发信] [仅收录]
· 标题    来源 · 时间 · 利好/利空 · 分数 · 已发信|仅收录|发信失败
  一句话摘要
  板块 chips · 个股 · 原文链接
```

打开页面约 **10 秒**轮询 GET items（及 settings 的 `source_status`）。不使用 SSE。  
空态（未开启）：说明「勾选来源并开启后，新文章会出现在这里；只有可能影响股价的才发邮件。刚开启不会把旧闻刷进来。」

视觉沿用顾问现有表单/列表样式，不新开设计体系。

## Worker 集成

在 `run_monitor_tick` **末尾**调用 `policy_watch.tick()`：

1. 墙钟超过 `max_tick_seconds` 则停止，已完成的源/文保留进度。
2. 先共享抓取到期源（最多 `max_sources_per_tick`），再解读 pending 文章，再按用户间隔+窗口扇出。
3. 异常记日志，不得让整个 monitor tick 崩溃（外层已有 tick 级 catch；本模块仍须内部吞掉可预期错误）。
4. 不要求 Redis/RQ。

## 错误处理

| 场景 | 行为 |
|------|------|
| 某源列表失败 / 不像列表 | 该源 `last_error`；其它源继续 |
| 正文失败 | 标题解读，注明未取到正文 |
| LLM 失败 | 收件箱可进，不发信；再试 1 次 |
| 无 DeepSeek Key | 可收录标题，不解读、不发信 |
| 邮箱未验证 | 可收录+解读，不发信 |
| SMTP 失败 | `notify_failed`，不自动重发洪水 |
| 预算用尽 | 留到下轮 |
| 自定义源指向内网 | PUT 拒绝；worker 侧再次 SSRF 拒绝 |

解读与邮件必须带免责声明。

## 测试要点

- 开启/新加源：当前列表只标已见，不回放、不发信。
- 共享文章：两用户同一预置源，精读+LLM 一次。
- 灵敏度低/中/高：同一 `impact_score` + `category` 发信结果不同。
- `trading_only` 在周末不扇出、不发信；进入交易窗口后补 items。
- 间隔：传入 4 → 存 5；第 9 条自定义 URL → 400。
- 自定义源 `http://127.0.0.1/` → 400。
- 无 Key / 未验证邮箱不发信。
- 同 tick 两篇达标 → 一封汇总邮件。
- 前端：顶栏入口、开关、三档、扫描时段、收件箱筛选、空态文案。

## 实现落点（指引）

| 区域 | 路径（预期） |
|------|----------------|
| 模块 | `backend/app/advisor/policy_watch/`（settings、store、discover、interpret、fanout、mailer、tick） |
| Worker | `backend/app/advisor/monitor/engine.py` 的 `run_monitor_tick` 末尾 |
| 抓取 | 复用 `agent/web_fetch_escalation.py` 的 `fetch_url_with_escalation` |
| 结构化源 | 复用 `fetch_market_cctv_news` / `fetch_macro_china_snapshot` |
| 配置 | `backend/app/advisor/config.yaml` → `policy_watch` |
| 路由 | `backend/app/advisor/routes.py` |
| 前端 | `frontend-advisor/src/pages/PolicyWatchPage.tsx`、`api.ts`、`App.tsx`、`TopbarNav.tsx` |
| 测试 | `backend/tests/test_policy_watch_*.py`、前端相关 vitest |

## 验收标准

1. 用户可在 `/agent/policy-watch` 开启雷达、勾选预置源、添加合法栏目 URL、调节灵敏度与扫描日程。
2. 开启后不会把栏目上已有旧闻做成邮件。
3. 新文章出现在收件箱；仅达灵敏度且邮箱已验证、解读成功时发信。
4. 邮件含来源、原文链接、摘要、方向、板块/个股、免责声明。
5. 盯盘/定点任务在雷达 tick 超时或失败时仍能继续跑。
