# Agent 联网搜索与网页抓取设计

## 目标

1. 主 Agent 可按用户配置进行联网调研与网页精读，补齐开放 Web 信息（与现有 AKShare 新闻/联播工具并存）。
2. 默认启用 DeepSeek 原生 `web_research`（服务端 `web_search_20250305`），无需第三方搜索 Key。
3. 可选启用 Tavily 搜索 + 后端 `fetch_url`；用户自备 Tavily API Key。
4. 两路能力可同时开启或同时关闭；开关与密钥在现有 DeepSeek 配置页管理。

## 已确认决策

| 项 | 决策 |
|----|------|
| 架构 | 方案 A：主 Agent 按开关条件挂载工具 |
| 默认 | `web_research` 开；Tavily 路径关 |
| 双开/双关 | 允许；双关则不挂任何联网工具 |
| DeepSeek 路径 | 复用用户 DeepSeek API Key；研究调用固定轻量模型（与主对话模型解耦） |
| Tavily 路径 | `web_search` + `fetch_url` 两个工具 |
| 爬虫范围 | 无域名白名单；仅协议 / SSRF / 超时 / 大小限制 |
| 配置入口 | `/agent/settings`（现有 DeepSeek 配置面板） |
| 非目标 | 域名白名单、JS 渲染、Brave/SearXNG、MCP 子进程、委员会图联网 |

## 架构

```text
用户开关 (user_llm_settings)
        │
        ▼
build_tools(user_id)
  ├─ web_research_enabled && DeepSeek Key  → 挂 web_research
  └─ tavily_enabled && Tavily Key         → 挂 web_search + fetch_url

主 Agent (现有 OpenAI 兼容 ReAct，路径不变)
  ├─ web_research  → DeepSeek Anthropic 兼容端点
  │                    + 服务端工具 web_search_20250305
  │                    固定轻量模型（config）
  │                    返回 answer + sources[]
  ├─ web_search    → Tavily Search API（用户 Key）
  │                    返回 title / url / snippet 列表
  └─ fetch_url     → 后端 httpx（SSRF/超时/大小）
                       返回清洗后正文（可截断）
```

要点：

- 主对话仍走 `ChatOpenAI` + `https://api.deepseek.com`；`web_research` 为工具内另一次 Anthropic 兼容调用。
- 不引入 MCP 子进程；社区 `websearch-deepseek` 仅作协议参考。
- 现有 `fetch_stock_news` / `fetch_market_cctv_news` 等专用工具保留；开放网页用联网工具。

## 数据模型

扩展集合 `user_llm_settings`（与 DeepSeek Key 同文档）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `web_research_enabled` | bool | `true` | DeepSeek 联网综述 |
| `tavily_enabled` | bool | `false` | Tavily + fetch_url |
| `tavily_api_key_enc` | str \| null | null | Fernet 加密（同 `LLM_ENCRYPTION_KEY`） |
| `tavily_key_hint` | str \| null | null | 脱敏提示 |
| `tavily_validated_at` | datetime \| null | null | 上次校验时间 |

文档中若缺省布尔字段，读取时按上表默认值解释（兼容旧用户）。

### 挂载判定

```text
挂 web_research          ⟺  web_research_enabled && DeepSeek Key 已配置
挂 web_search + fetch_url ⟺  tavily_enabled && Tavily Key 已配置
```

清除 DeepSeek Key **不**自动清除 Tavily；提供独立「清除 Tavily Key」（同时将 `tavily_enabled=false`）。

## API

扩展现有 `/api/advisor/llm/settings`：

### GET

在现有公开字段上增加：

- `web_research_enabled`
- `tavily_enabled`
- `tavily_configured`（bool）
- `tavily_key_hint`
- `tavily_validated_at`（可选 ISO 字符串）

不回显任何完整 API Key。

### PUT

支持与 DeepSeek 配置同一请求局部更新，body 可含：

- 现有：`api_key`、`model`、`base_url`
- 新增：`web_research_enabled`、`tavily_enabled`、`tavily_api_key`（可选，空则保留原 Key）

校验：

1. `tavily_enabled=true` 且保存后仍无有效 Tavily Key → **400**，提示先填写 Key。
2. 提交非空 `tavily_api_key` 时做一次轻量 Tavily Search 探测；失败则整次保存失败。
3. `web_research_enabled` 不强制 DeepSeek 已配置（Agent 聊天入口仍要求 DeepSeek Key）。

### 清除 Tavily

`DELETE /api/advisor/llm/settings/tavily`（或 PUT 显式 `clear_tavily=true`）：删除 `tavily_api_key_enc` / hint / validated_at，并设 `tavily_enabled=false`。不删除 DeepSeek 配置。

实现任选一种，规格要求行为等价；推荐独立 DELETE 路径以降低误清 DeepSeek Key 的风险。

## 全局配置（`config.yaml`）

用户开关不进 yaml；yaml 仅放默认与限额，例如：

```yaml
agent_web:
  web_research:
    model: deepseek-v4-flash          # 与主对话模型解耦
    anthropic_base_url: https://api.deepseek.com/anthropic
    server_tool_type: web_search_20250305
    max_tokens: 8192
    timeout_seconds: 120
    max_query_chars: 500
    max_calls_per_turn: 3
  web_search:
    max_results_default: 5
    max_results_cap: 10
    max_calls_per_turn: 5
    validate_query: "ping"
  fetch_url:
    timeout_seconds: 20
    max_bytes: 524288                 # 原始下载上限
    max_text_chars: 80000             # 清洗后文本上限
    max_redirects: 3
    allowed_ports: [80, 443]
    max_calls_per_turn: 8
```

字段名可在实现时微调，语义不变。

## 工具契约

### `web_research(query: str) -> str`

- 使用用户 DeepSeek Key + `agent_web.web_research` 配置。
- 调用 Anthropic 兼容 Messages API，声明服务端工具类型 `web_search_20250305`；由 DeepSeek 服务端完成搜索/抓取/回灌。
- `query` 超长按 `max_query_chars` 截断。
- 成功返回 JSON 文本：`{"answer":"...","sources":["https://..."]}`；失败返回可读错误字符串，不抛崩 Agent。
- 进度：`phase=main_agent`，`step=web_research`。
- 每轮对话调用次数超过 `max_calls_per_turn` → 返回「已达上限」。

### `web_search(query: str, max_results: int = 5) -> str`

- Tavily Search；`max_results` clamp 到 `[1, max_results_cap]`。
- 成功返回 JSON 数组：`[{ "title", "url", "content", "score"? }, ...]`。
- 进度：`step=web_search`；同样受每轮次数上限约束。

### `fetch_url(url: str) -> str`

- 仅 `http`/`https`。
- SSRF：禁止解析到私网、本机、链路本地、未指定地址；连接前再次解析校验（防 DNS rebinding）；端口仅允许配置列表（默认 80/443）。
- 限超时、最大重定向、最大下载字节；优先接受 HTML/文本 Content-Type。
- 去脚本/样式后抽文本，截断到 `max_text_chars`。
- 无域名白名单。
- 进度：`step=fetch_url`；受每轮次数上限约束。

## Prompt 增量

在主 Agent system prompt 增加简短规则（编号顺延）：

1. 需要带引用的综合调研且已挂载 `web_research` → 优先使用之。
2. 需要自行筛选来源或精读某页 → `web_search` 再 `fetch_url`。
3. 回答中引用须带来源 URL；禁止编造链接。
4. 结构化 A 股新闻/联播/指数点位等仍优先现有专用工具；开放网页与政策外网检索用联网工具。

双关时不注入上述工具相关条文（或注入「当前未启用联网搜索」一句即可）。

## UI（`/agent/settings`）

在 DeepSeek Key/模型下方增加 **「联网搜索」** 区块：

1. **DeepSeek 联网综述（web_research）**  
   - 开关，默认开  
   - 文案：复用上方 DeepSeek Key；使用固定轻量模型做服务端搜索  

2. **Tavily 搜索 + 网页抓取**  
   - 开关，默认关  
   - Tavily API Key 输入（password；已配置显示 hint）  
   - 外链至 Tavily 文档  
   - 未配置 Key 时打开开关：前端拦截或保存时显示后端错误  
   - 「清除 Tavily Key」：清密钥并关开关  

与 DeepSeek 配置同一保存按钮提交（一次 PUT）。

## 错误处理

| 场景 | 行为 |
|------|------|
| Tavily Key 无效（保存） | PUT 400，不写入 |
| Tavily/DeepSeek 运行时失败 | 工具返回错误字符串 |
| SSRF / 非法 URL | `fetch_url` 明确拒绝文案 |
| 超每轮限额 | 工具返回「已达上限」 |
| 双关 | 不挂联网工具；其它 Agent 能力照常 |

## 测试要点

- 旧用户无新字段：默认等价 `web_research` 开、Tavily 关。
- 仅 DeepSeek：工具含 `web_research`，不含 Tavily 二者。
- 双关：三者皆无。
- 只开 Tavily（有 Key）：有 `web_search`+`fetch_url`，无 `web_research`。
- 双开：三者皆有。
- `fetch_url("http://127.0.0.1/")` / 私网 IP → 拒绝。
- Tavily Key 错误 → 保存失败。
- GET 设置不回显明文 Key；加密存储可测。
- 进度事件含对应 `step`。

## 非目标（本版不做）

- 域名白名单或用户自定义域名列表  
- 无头浏览器 / JS 渲染  
- Brave、SearXNG、其它搜索后端实现（接口可扩展，本版不实现）  
- 通过 MCP 子进程调用 `websearch-deepseek`  
- 委员会（committee）图内联网  

## 实现落点（指引）

| 区域 | 路径（预期） |
|------|----------------|
| 设置读写 | `backend/app/advisor/llm_settings.py`、`routes.py` |
| DeepSeek research | 新模块如 `backend/app/advisor/agent/web_research.py` |
| Tavily + fetch | 新模块如 `backend/app/advisor/agent/web_fetch.py` |
| 工具挂载 | `backend/app/advisor/agent/tools.py`、`graph.py` prompt |
| 配置 | `backend/app/advisor/config.yaml` |
| 前端 | `frontend-advisor/src/pages/AgentSettingsPage.tsx`、`agentApi.ts` |
| 测试 | `backend/tests/`、前端相关 vitest |
