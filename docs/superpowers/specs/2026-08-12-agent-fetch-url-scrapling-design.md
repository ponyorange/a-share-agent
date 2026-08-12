# Agent fetch_url Scrapling 增强抓取设计

## 目标

1. 加强主 Agent「精读网页」能力：在现有 httpx `fetch_url` 遇到空壳页、反爬或硬失败时，自动升级到 Scrapling（轻量 HTTP → 无头浏览器）。
2. 只要任意联网能力开启（`web_research` 或 Tavily），即挂载 `fetch_url`，使仅开 DeepSeek 联网时也能精读 URL。
3. 工具名与 Agent 调用方式不变；升级对模型透明。

## 已确认决策

| 项 | 决策 |
|----|------|
| 范围 | 仅加强读网页（不做 CSS/XPath 结构化抽数） |
| 策略 | httpx 默认；失败/空壳后再升级 |
| 挂载 | `web_research \|\| tavily` 任一开启 → 挂 `fetch_url`；`web_search` 仍仅 Tavily |
| 升级深度 | L1 httpx → L2 Scrapling `Fetcher` → L3 `StealthyFetcher` |
| 用户开关 | 不新增 Scrapling 独立开关；跟联网能力走 |
| 架构 | `fetch_url` 内部流水线（方案 1） |
| 非目标 | 结构化抽取工具、MCP、拆独立 fetch 镜像、替换 web_research/Tavily |

与 [2026-07-26-agent-web-search-design.md](./2026-07-26-agent-web-search-design.md) 的关系：本设计修订其中「`fetch_url` 仅随 Tavily 挂载」与「本版不做 JS 渲染」两项；其余联网架构不变。

## 架构

```text
用户开关
  web_research_enabled (+ DeepSeek Key)  ──┐
  tavily_enabled (+ Tavily Key)          ──┼─→ 任一为真
                                           │
                                           ▼
                                    挂载 fetch_url
                                           │
                     ┌─────────────────────┴─────────────────────┐
                     │  fetch_url(url) 内部流水线（对 Agent 透明）   │
                     │  L1 httpx → L2 Scrapling Fetcher            │
                     │       → L3 StealthyFetcher（浏览器）         │
                     └───────────────────────────────────────────┘

仍保持：
  web_research  ⟺ web_research 开关 + DeepSeek Key
  web_search    ⟺ tavily 开关 + Tavily Key
```

要点：

1. 工具名仍是 `fetch_url`；Agent / Prompt 无需学习新工具。
2. 解耦 Tavily：仅开 DeepSeek 联网时也可精读用户粘贴的链接或 `web_research` 返回的 sources。
3. 新模块建议 `web_fetch_escalation.py`（或扩展 `web_fetch.py`）：编排流水线；L1 继续复用现有实现。
4. L2/L3 与 L1 共用 `is_url_safe_for_fetch`；SSRF 拒绝时不升级。
5. 进度：一次调用可多次 `emit_progress`（如 `fetch_url` → `fetch_url_l2` / `fetch_url_l3`），最终一次 completed/failed。

## 升级判定与返回契约

### 何时升级（L1→L2 或 L2→L3）

任一成立即升级：

| 条件 | 说明 |
|------|------|
| 硬失败 | 返回以 `错误：` 开头（超时、HTTP≥400、异常等） |
| 正文过短 | 清洗后文本长度 `< min_text_chars`（默认 200） |
| 疑似拦截页 | 正文匹配内置/可配关键词（大小写不敏感），如 `just a moment`、`cf-browser-verification`、`attention required`、`access denied`、`verify you are human`、`checking your browser` |

不升级：

- SSRF / 非法协议 / 非法端口 → 直接失败，不升级。
- L3 之后仍失败或仍空壳 → 返回可读错误（标明已尝试的级别）。

### 各级行为

| 级 | 实现 | 默认超时 | 说明 |
|----|------|----------|------|
| L1 | 现有 `fetch_url_text`（httpx） | 20s | SSRF、重定向复检、截断 |
| L2 | Scrapling `Fetcher` | 30s | 无浏览器；可 TLS/浏览器指纹 impersonate |
| L3 | Scrapling `StealthyFetcher` | 60s | headless；`solve_cloudflare` 可配；`network_idle` 默认开 |

三级共用：`max_text_chars`、`allowed_ports`、SSRF。`escalation.enable_stealth: false` 时最多到 L2。缺依赖或无浏览器时跳过对应级并降级，不拖垮进程。

### 工具返回

成功时正文前可带一行 meta：

```text
# fetch_via: httpx|scrapling|stealth
（清洗后正文…）
```

失败仍返回 `错误：…` 字符串，不抛崩 Agent。

配额：一次用户侧 `fetch_url` 调用只计 **1 次**（升级不另计）。总墙钟受 `escalation.max_total_seconds`（默认 90）约束；超时则停在当前级并返回已有结果或错误。

### Prompt 微调

- 挂载条件改为「任一联网开启」。
- 文案：有候选 URL 或用户给出链接时可用 `fetch_url` 精读；不必绑定「必须先 web_search」。
- `web_research` 的 sources 可跟 `fetch_url` 精读。

## 配置（`config.yaml`）

用户开关不进 yaml。在 `agent_web.fetch_url` 下增加：

```yaml
fetch_url:
  timeout_seconds: 20
  max_bytes: 524288
  max_text_chars: 80000
  max_redirects: 3
  allowed_ports: [80, 443]
  max_calls_per_turn: 8
  escalation:
    enabled: true
    min_text_chars: 200
    max_total_seconds: 90
    l2_timeout_seconds: 30
    l3_timeout_seconds: 60
    enable_stealth: true
    solve_cloudflare: true
    headless: true
    block_patterns:
      - "just a moment"
      - "cf-browser-verification"
      - "attention required"
      - "access denied"
      - "verify you are human"
      - "checking your browser"
```

字段名实现时可微调，语义不变。

## 依赖与 Docker

- `backend/requirements.txt` 增加钉版本的 `scrapling`。
- `deploy/Dockerfile` runtime：安装 Chromium 运行库；`pip install` 后执行 Scrapling/Playwright 浏览器安装（以官方文档为准）。
- 镜像标签仍为 `share-data:amd64`（禁止 `latest` 作为部署默认标签）。
- 本版不拆独立 fetch 镜像；接受主镜像体积增大。
- 本地缺浏览器时 L3 自动跳过并返回明确错误。

### 运维降级

| 情况 | 行为 |
|------|------|
| `escalation.enabled=false` | 仅 L1 |
| 未安装 scrapling | 跳过 L2/L3 |
| 无 Chromium | 跳过 L3，保留 L2 结果 |
| 超 `max_total_seconds` | 停止升级，返回当前最优正文或错误 |

设置页不新增 Scrapling 开关；可补一句说明：「精读网页在联网开启时可用，困难页面会自动增强抓取」。

## 安全

- L1/L2/L3 统一 SSRF：仅 http/https、端口白名单、禁私网/本机等；重定向每跳复检。
- L3 不关闭 SSRF；不支持 `file://`；不注入本站 Cookie/Session。
- 正文截断到 `max_text_chars`；下载体积受上限约束。
- 进度事件不回传完整 HTML。

## 错误处理

| 场景 | 行为 |
|------|------|
| SSRF / 非法 URL | `错误：禁止：…`，不升级 |
| L1 失败且 escalation 开 | 升 L2（不立刻把 L1 错误返回，除非后续全失败） |
| 全级失败 | `错误：抓取失败（已尝试 httpx/scrapling/stealth）: …` |
| 配额用尽 | 现有 `consume_web_quota("fetch_url")` |
| 缺依赖 | 跳过对应级；全无则「增强抓取不可用」类文案 |
| 未捕获异常 | 转为错误字符串，不抛崩图 |

## 测试要点

- 仅 `web_research` → 有 `fetch_url`、无 `web_search`；双关 → 无 `fetch_url`；仅 tavily → 有二者。
- `http://127.0.0.1/` → 拒绝且不升级。
- `escalation.enabled=false` → 短正文不升级。
- mock L1 空壳 → 调 L2；L2 仍壳 → 调 L3。
- 成功正文含 `# fetch_via:`。
- 一次调用内 L1→L3 只消耗 1 次配额。
- 无 scrapling 时不 crash。
- 现有 SSRF / Content-Type / 截断回归通过。

## 非目标（本版不做）

- CSS/XPath 结构化抽数工具
- 独立 Scrapling 用户开关
- 通过 MCP 子进程调用 Scrapling
- 拆 `share-data-fetch` 独立镜像
- 替换或削弱 `web_research` / Tavily

## 实现落点（指引）

| 区域 | 路径（预期） |
|------|----------------|
| L1 复用 | `backend/app/advisor/agent/web_fetch.py` |
| 升级流水线 | `backend/app/advisor/agent/web_fetch_escalation.py`（新建） |
| 工具挂载 | `backend/app/advisor/agent/web_tools.py` |
| Prompt | `backend/app/advisor/agent/graph.py` |
| 进度 step | `backend/app/advisor/agent/progress.py`（若需登记新 step） |
| 配置 | `backend/app/advisor/config.yaml` |
| 依赖 | `backend/requirements.txt` |
| 镜像 | `deploy/Dockerfile` |
| 测试 | `backend/tests/test_web_fetch*.py` 等 |
| 设置文案（可选） | `frontend-advisor` Agent 设置页 |
