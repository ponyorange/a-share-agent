# Agent 联网搜索与网页抓取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为主 Agent 增加可开关的 DeepSeek `web_research` 与 Tavily `web_search`+`fetch_url`，并在 DeepSeek 配置页管理开关与 Tavily Key。

**Architecture:** 扩展 `user_llm_settings`；`build_tools` 按挂载判定条件注册工具；`web_research` 用 httpx 调 DeepSeek Anthropic 兼容端点并声明 `web_search_20250305`；Tavily 与 SSRF 安全抓取为独立模块；进度步增加 `web_research` / `web_search` / `fetch_url`。

**Tech Stack:** FastAPI、Mongo `user_llm_settings`、httpx、LangChain `@tool`、pytest、React/Vite（AgentSettingsPage）。

## Global Constraints

- 默认：`web_research_enabled=true`，`tavily_enabled=false`；可双开/双关。
- 挂载：`web_research` ⟺ 开关开且 DeepSeek Key 已配置；`web_search`+`fetch_url` ⟺ Tavily 开关开且 Tavily Key 已配置。
- `web_research` 复用用户 DeepSeek Key，模型固定 `config.agent_web.web_research.model`（与主对话模型解耦）。
- 无域名白名单；`fetch_url` 必须防 SSRF（私网/本机/端口）。
- 不新增 MCP 子进程；不强制引入 `anthropic` SDK（用 httpx）；不把密钥回显给前端。
- 不要 git commit（除非用户明确要求）。

## File Structure

- `backend/app/advisor/config.yaml`：新增 `agent_web` 段。
- `backend/app/advisor/agent/web_limits.py`：读 config、每轮调用计数 ContextVar。
- `backend/app/advisor/agent/web_fetch.py`：SSRF 校验 + `fetch_url` 实现。
- `backend/app/advisor/agent/web_tavily.py`：Tavily search + Key 校验。
- `backend/app/advisor/agent/web_research.py`：DeepSeek Anthropic `web_search_20250305`。
- `backend/app/advisor/agent/web_tools.py`：组装三个 LangChain tool + 挂载判定。
- `backend/app/advisor/llm_settings.py`：读写新字段、清除 Tavily。
- `backend/app/advisor/routes.py`：扩展 GET/PUT，新增 DELETE tavily。
- `backend/app/advisor/agent/tools.py`：`build_tools` 合并 web tools。
- `backend/app/advisor/agent/graph.py`：prompt 规则 20；每轮 reset 计数。
- `backend/app/advisor/agent/progress.py` + 前端进度类型/文案。
- `frontend-advisor/src/agentApi.ts`、`pages/AgentSettingsPage.tsx`（及测试）。
- 测试：`backend/tests/test_web_fetch.py`、`test_web_tavily.py`、`test_web_research.py`、`test_llm_web_settings.py`、`test_web_tools_mount.py`。

---

### Task 1: config.yaml `agent_web` + 限额辅助

**Files:**
- Modify: `backend/app/advisor/config.yaml`
- Create: `backend/app/advisor/agent/web_limits.py`
- Test: `backend/tests/test_web_limits.py`

**Interfaces:**
- Produces:
  - `get_agent_web_config() -> dict[str, Any]`（从 `load_config()["agent_web"]`，缺省用内置 DEFAULT）
  - `reset_web_turn_counters() -> None`
  - `consume_web_quota(kind: Literal["web_research","web_search","fetch_url"]) -> str | None`  
    未超限返回 `None`；超限返回错误字符串 `"已达本轮调用上限"`。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_web_limits.py
from app.advisor.agent import web_limits

def test_consume_web_quota_blocks_after_max(monkeypatch):
    monkeypatch.setattr(
        web_limits,
        "get_agent_web_config",
        lambda: {
            "web_research": {"max_calls_per_turn": 2},
            "web_search": {"max_calls_per_turn": 5},
            "fetch_url": {"max_calls_per_turn": 8},
        },
    )
    web_limits.reset_web_turn_counters()
    assert web_limits.consume_web_quota("web_research") is None
    assert web_limits.consume_web_quota("web_research") is None
    assert web_limits.consume_web_quota("web_research") == "已达本轮调用上限"
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/orange/Desktop/code/share-data/backend
python -m pytest -q tests/test_web_limits.py
```

Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

在 `config.yaml` 追加（与规格一致）：

```yaml
agent_web:
  web_research:
    model: deepseek-v4-flash
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
    max_bytes: 524288
    max_text_chars: 80000
    max_redirects: 3
    allowed_ports: [80, 443]
    max_calls_per_turn: 8
```

`web_limits.py`：ContextVar 存 `{"web_research":0,"web_search":0,"fetch_url":0}`；`consume` 先 +1 再与 config 比较（或先比较再 +1，二选一并在测试中固定：采用**先检查再递增**）。

- [ ] **Step 4: 跑测试通过**

```bash
python -m pytest -q tests/test_web_limits.py
```

Expected: PASS。

- [ ] **Step 5: 不 commit**

---

### Task 2: `fetch_url` SSRF 安全抓取

**Files:**
- Create: `backend/app/advisor/agent/web_fetch.py`
- Test: `backend/tests/test_web_fetch.py`

**Interfaces:**
- Produces:
  - `is_url_safe_for_fetch(url: str, *, allowed_ports: list[int]) -> tuple[bool, str]`  
    不安全时第二个值为中文原因。
  - `fetch_url_text(url: str, *, cfg: dict[str, Any] | None = None) -> str`  
    成功返回正文；失败返回以 `错误：` 开头的可读字符串（不抛）。

- [ ] **Step 1: 写失败测试**

```python
from app.advisor.agent.web_fetch import is_url_safe_for_fetch, fetch_url_text

def test_reject_localhost():
    ok, reason = is_url_safe_for_fetch("http://127.0.0.1/", allowed_ports=[80, 443])
    assert ok is False
    assert "内网" in reason or "本机" in reason or "禁止" in reason

def test_reject_private_ip():
    ok, _ = is_url_safe_for_fetch("http://192.168.1.1/", allowed_ports=[80, 443])
    assert ok is False

def test_reject_file_scheme():
    ok, _ = is_url_safe_for_fetch("file:///etc/passwd", allowed_ports=[80, 443])
    assert ok is False

def test_fetch_url_text_rejects_unsafe_without_network():
    out = fetch_url_text("http://127.0.0.1/")
    assert out.startswith("错误：")
```

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest -q tests/test_web_fetch.py
```

Expected: FAIL。

- [ ] **Step 3: 实现最小逻辑**

`web_fetch.py` 要点：

1. `urllib.parse.urlparse`：仅 `http`/`https`；hostname 必填；port 缺省按 scheme（80/443），否则须在 `allowed_ports`。
2. `socket.getaddrinfo` 解析全部 A/AAAA；任一地址属于以下则拒绝：`ipaddress.ip_address(...).is_private / is_loopback / is_link_local / is_reserved / is_multicast / is_unspecified`。
3. `httpx.Client(follow_redirects=False)` 手动跳转最多 `max_redirects`；**每次跳转后对新 URL 再跑安全检查**。
4. 流式读至多 `max_bytes`；Content-Type 若存在且明显非 text/html/json/xml/plain 可拒绝（无 Content-Type 则继续）。
5. 用正则去掉 `<script>...</script>`、`<style>...</style>`，再去标签：`re.sub(r"<[^>]+>", " ", html)`，空白折叠，截断 `max_text_chars`。
6. 配置从 `get_agent_web_config()["fetch_url"]` 读取。

- [ ] **Step 4: 跑测试通过**

```bash
python -m pytest -q tests/test_web_fetch.py
```

Expected: PASS。

- [ ] **Step 5: 不 commit**

---

### Task 3: Tavily search + Key 校验

**Files:**
- Create: `backend/app/advisor/agent/web_tavily.py`
- Test: `backend/tests/test_web_tavily.py`

**Interfaces:**
- Produces:
  - `validate_tavily_key(api_key: str, *, cfg: dict | None = None) -> None`  
    失败抛 `ValueError`（中文消息）。
  - `tavily_search(api_key: str, query: str, *, max_results: int = 5, cfg: dict | None = None) -> str`  
    成功返回 JSON 数组字符串；失败返回 `错误：...`。

- [ ] **Step 1: 写失败测试（httpx mock）**

```python
import json
import httpx
import pytest
from app.advisor.agent import web_tavily

def test_validate_tavily_key_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/search")
        return httpx.Response(200, json={"results": []})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        web_tavily,
        "_client",
        lambda **kw: httpx.Client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    # 若实现直接 httpx.post，则 monkeypatch httpx.Client 或模块内 _post
    web_tavily.validate_tavily_key("tvly-test")

def test_tavily_search_shapes_results(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "A", "url": "https://example.com", "content": "hi", "score": 0.9}
                ]
            },
        )
    monkeypatch.setattr(web_tavily, "_request_search", lambda **kw: handler(None) and {
        "results": [{"title": "A", "url": "https://example.com", "content": "hi", "score": 0.9}]
    })
    # 更清晰：实现暴露 _request_search(api_key, payload) -> dict
```

实现时请固定内部函数：

```python
def _request_search(api_key: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    ...
```

测试 monkeypatch `_request_search`：

```python
def test_tavily_search_json(monkeypatch):
    monkeypatch.setattr(
        web_tavily,
        "_request_search",
        lambda api_key, payload, timeout: {
            "results": [
                {"title": "A", "url": "https://example.com/a", "content": "hi", "score": 0.9}
            ]
        },
    )
    out = json.loads(web_tavily.tavily_search("k", "q", max_results=3))
    assert out[0]["url"] == "https://example.com/a"

def test_validate_tavily_key_raises(monkeypatch):
    def boom(*a, **k):
        raise httpx.HTTPStatusError("bad", request=httpx.Request("POST", "https://api.tavily.com/search"), response=httpx.Response(401))
    monkeypatch.setattr(web_tavily, "_request_search", boom)
    with pytest.raises(ValueError):
        web_tavily.validate_tavily_key("bad")
```

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest -q tests/test_web_tavily.py
```

Expected: FAIL。

- [ ] **Step 3: 实现**

- POST `https://api.tavily.com/search`，JSON：`{"api_key", "query", "max_results", "search_depth": "basic"}`。
- `validate_tavily_key`：用 config `validate_query`（默认 `"ping"`），`max_results=1`；非 2xx 或异常 → `ValueError("Tavily API Key 无效或不可用")`。
- `tavily_search`：clamp `max_results` 到 `[1, max_results_cap]`；映射 results 字段；`json.dumps(..., ensure_ascii=False)`。

- [ ] **Step 4: 跑测试通过**

```bash
python -m pytest -q tests/test_web_tavily.py
```

Expected: PASS。

- [ ] **Step 5: 不 commit**

---

### Task 4: DeepSeek `web_research`（Anthropic 兼容）

**Files:**
- Create: `backend/app/advisor/agent/web_research.py`
- Test: `backend/tests/test_web_research.py`

**Interfaces:**
- Produces:
  - `run_web_research(api_key: str, query: str, *, cfg: dict | None = None) -> str`  
    成功：`json.dumps({"answer": str, "sources": list[str]}, ensure_ascii=False)`；失败：`错误：...`。

- [ ] **Step 1: 写失败测试**

```python
import json
from app.advisor.agent import web_research

def test_run_web_research_parses_text_and_sources(monkeypatch):
    monkeypatch.setattr(
        web_research,
        "_post_messages",
        lambda **kw: {
            "content": [
                {"type": "text", "text": "结论正文"},
                {
                    "type": "web_search_tool_result",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://example.com/1",
                            "title": "T1",
                        }
                    ],
                },
            ]
        },
    )
    out = json.loads(web_research.run_web_research("sk-x", "什么是科创板？"))
    assert "结论" in out["answer"]
    assert out["sources"] == ["https://example.com/1"]

def test_run_web_research_truncates_query(monkeypatch):
    seen = {}
    def capture(**kw):
        seen["messages"] = kw["messages"]
        return {"content": [{"type": "text", "text": "ok"}]}
    monkeypatch.setattr(web_research, "_post_messages", capture)
    monkeypatch.setattr(
        web_research,
        "get_agent_web_config",
        lambda: {
            "web_research": {
                "model": "deepseek-v4-flash",
                "anthropic_base_url": "https://api.deepseek.com/anthropic",
                "server_tool_type": "web_search_20250305",
                "max_tokens": 100,
                "timeout_seconds": 10,
                "max_query_chars": 10,
            }
        },
    )
    web_research.run_web_research("sk", "abcdefghijklmnopqrstuvwxyz")
    assert len(seen["messages"][0]["content"]) == 10
```

（若 `get_agent_web_config` 从 `web_limits` 导入，则 monkeypatch `web_limits.get_agent_web_config`。）

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest -q tests/test_web_research.py
```

Expected: FAIL。

- [ ] **Step 3: 实现**

```python
def _post_messages(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    timeout: float,
) -> dict:
    url = base_url.rstrip("/") + "/v1/messages"
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "tools": tools,
            },
        )
        r.raise_for_status()
        return r.json()
```

- `tools`：`[{"type": server_tool_type, "name": "web_search"}]`（`type` 来自 config，默认 `web_search_20250305`）。
- 解析：拼接所有 `type=="text"` 的 `text` 为 `answer`；从任意含 `url` 的嵌套结构收集 sources（去重保序）。若 answer 为空则 `错误：未返回研究内容`。
- 异常捕获为 `错误：DeepSeek web_research 失败: ...`。

- [ ] **Step 4: 跑测试通过**

```bash
python -m pytest -q tests/test_web_research.py
```

Expected: PASS。

- [ ] **Step 5: 不 commit**

---

### Task 5: llm_settings 扩展 + API

**Files:**
- Modify: `backend/app/advisor/llm_settings.py`
- Modify: `backend/app/advisor/routes.py`（`LlmSettingsBody`、GET/PUT、DELETE tavily）
- Test: `backend/tests/test_llm_web_settings.py`

**Interfaces:**
- Produces（`llm_settings`）:
  - `public_llm_settings` 增加：`web_research_enabled`、`tavily_enabled`、`tavily_configured`、`tavily_key_hint`、`tavily_validated_at`
  - `update_llm_settings(user_id, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None, web_research_enabled: bool | None = None, tavily_enabled: bool | None = None, tavily_api_key: str | None = None, validate_deepseek: bool = True) -> dict`
  - `clear_tavily_settings(user_id: str) -> dict`
  - `resolve_tavily_api_key(user_id: str) -> str | None`
  - `web_tool_flags(user_id: str) -> dict`  
    `{"web_research": bool, "tavily": bool}` 按挂载判定。

**PUT 行为变更（重要）：**
- `api_key` 改为可选；仅更新开关/Tavily 时可不传。
- 若传入非空 `api_key`：沿用现有 DeepSeek 校验并写入。
- 若传入非空 `tavily_api_key`：`validate_tavily_key` 后加密写入。
- 若最终 `tavily_enabled` 为 True 且无 Tavily Key → `ValueError("开启 Tavily 前请先填写有效的 API Key")`。
- `clear_llm_settings`（删 DeepSeek）**保留** Tavily 字段：改为只 `$unset` DeepSeek 相关字段，或先读出 tavily 再写回。规格：清除 DeepSeek **不**自动清 Tavily。  
  → 将 `clear_llm_settings` 改为删除整文档会丢 Tavily；**必须改成**仅清除 DeepSeek Key 字段并保留 web/tavily 字段（或 `delete` 后若需「完全清空」另议）。本任务要求：`DELETE /llm/settings` 清除 DeepSeek 凭证与 `configured`，但保留 `web_research_enabled` / tavily_*；若实现困难，可改为：clear 时先取出 tavily 字段，delete_one 后若有 tavily 再 upsert 回去。

- [ ] **Step 1: 写失败测试**

用现有测试风格 mock `get_db()`（参考 `backend/tests/test_auth_account.py` 或 `test_security_config.py`）。覆盖：

1. 无文档时 `public_llm_settings` → `web_research_enabled is True`，`tavily_enabled is False`，`tavily_configured is False`
2. `update` 打开 tavily 无 Key → ValueError
3. `update` 带假 Key 时 mock `validate_tavily_key` 成功 → `tavily_configured True`
4. `clear_tavily_settings` → enabled False、configured False
5. `clear_llm_settings` 后 tavily 仍在（若测路由则 TestClient）

路由测试：`DELETE /api/advisor/llm/settings/tavily` 返回公开设置。

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest -q tests/test_llm_web_settings.py
```

Expected: FAIL。

- [ ] **Step 3: 实现**

`routes.py`：

```python
class LlmSettingsBody(BaseModel):
    api_key: str | None = Field(default=None)
    model: str | None = Field(default=None)
    base_url: str | None = Field(default=None)
    web_research_enabled: bool | None = Field(default=None)
    tavily_enabled: bool | None = Field(default=None)
    tavily_api_key: str | None = Field(default=None)
```

PUT 调用 `update_llm_settings`；若 body 全空且无任何变更字段 → 400。  
规则：至少变更 DeepSeek（key/model/base）或 web 字段之一。  
兼容旧前端：若只传 `api_key`+`model` 仍可用。

```python
@router.delete("/llm/settings/tavily")
def llm_settings_tavily_delete(...):
    return clear_tavily_settings(uid)
```

加密复用 `encrypt_api_key` / `key_hint`。

- [ ] **Step 4: 跑测试通过**

```bash
python -m pytest -q tests/test_llm_web_settings.py tests/test_security_config.py
```

Expected: PASS（不回归加密测试）。

- [ ] **Step 5: 不 commit**

---

### Task 6: 进度协议扩展

**Files:**
- Modify: `backend/app/advisor/agent/progress.py`
- Modify: `frontend-advisor/src/agentApi.ts`（`SubagentProgress["step"]`）
- Modify: `frontend-advisor/src/pages/AgentChatPage.tsx`（`STEP_LABELS`）
- Test: 扩展现有 progress 测试（若有 `test_*progress*`）；否则 `backend/tests/test_web_progress.py`

**Interfaces:**
- Produces: `PROGRESS_STEPS` 增加 `"web_research" | "web_search" | "fetch_url"`；`_stage_message` 中文文案：
  - web_research: 正在联网调研 / 联网调研完成 / 联网调研失败
  - web_search: 正在搜索网页 / 网页搜索完成 / 网页搜索失败
  - fetch_url: 正在抓取网页 / 网页抓取完成 / 网页抓取失败

- [ ] **Step 1: 写/改测试断言新 step 合法**

```python
from app.advisor.agent.progress import emit_progress, bind_progress_sink

def test_emit_web_research_step():
    events = []
    with bind_progress_sink(events.append):
        emit_progress(step="web_research", status="started", phase="main_agent")
    assert events[-1]["step"] == "web_research"
```

（若 `emit_progress` 签名不同，按现有 API 调用。）

- [ ] **Step 2–4: 实现后端 + 前端文案，跑测试**

```bash
python -m pytest -q tests/test_web_progress.py -k web_research
cd /Users/orange/Desktop/code/share-data/frontend-advisor && npm test -- --run src/pages/AgentChatPage.test.tsx
```

- [ ] **Step 5: 不 commit**

---

### Task 7: `web_tools` 挂载 + 接入 `build_tools` + prompt

**Files:**
- Create: `backend/app/advisor/agent/web_tools.py`
- Modify: `backend/app/advisor/agent/tools.py`（在 python tools 之前插入 `*build_web_tools(user_id)`）
- Modify: `backend/app/advisor/agent/graph.py`（规则 20；stream 开始时 `reset_web_turn_counters()`）
- Test: `backend/tests/test_web_tools_mount.py`

**Interfaces:**
- Produces: `build_web_tools(user_id: str) -> list[BaseTool]`  
  工具名：`web_research`、`web_search`、`fetch_url`。  
  每个工具：`emit_progress` started/completed/failed；`consume_web_quota`；调用对应模块。

- [ ] **Step 1: 写挂载测试**

```python
def test_mount_defaults_research_only(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": True, "tavily": False},
    )
    tools = build_web_tools("u1")
    names = {t.name for t in tools}
    assert names == {"web_research"}

def test_mount_both(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": True, "tavily": True},
    )
    names = {t.name for t in build_web_tools("u1")}
    assert names == {"web_research", "web_search", "fetch_url"}

def test_mount_none(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": False, "tavily": False},
    )
    assert build_web_tools("u1") == []
```

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest -q tests/test_web_tools_mount.py
```

- [ ] **Step 3: 实现工具与 prompt**

`web_research` 工具内：`resolve_llm_credentials(user_id)` 取 key → `run_web_research`。  
`web_search` / `fetch_url`：`resolve_tavily_api_key`；fetch 不需要 Tavily Key 但规格要求二者同开同挂，故仅在 tavily 挂载时注册 `fetch_url`。

`SYSTEM_PROMPT` 追加：

```text
20. 联网：若已挂载 web_research，综合调研优先用之；若已挂载 web_search/fetch_url，
   需自行筛选来源时先 web_search 再 fetch_url。引用须带来源 URL，禁止编造链接。
   A 股结构化新闻/联播/指数点位仍优先专用工具（规则 6–9）。
```

在 `stream_agent`（或创建 agent 前）调用 `reset_web_turn_counters()`。

- [ ] **Step 4: 跑测试**

```bash
python -m pytest -q tests/test_web_tools_mount.py tests/test_web_fetch.py tests/test_web_research.py
```

Expected: PASS。

- [ ] **Step 5: 不 commit**

---

### Task 8: 前端 DeepSeek 配置页「联网搜索」

**Files:**
- Modify: `frontend-advisor/src/agentApi.ts`
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.tsx`
- Create/Modify: `frontend-advisor/src/pages/AgentSettingsPage.test.tsx`

**Interfaces:**
- `LlmSettings` 增加 web 字段。
- `saveLlmSettings` body：`api_key?`、`web_research_enabled?`、`tavily_enabled?`、`tavily_api_key?`、`model?`。
- `clearTavilySettings(): Promise<LlmSettings>` → `DELETE /api/advisor/llm/settings/tavily`。

- [ ] **Step 1: 写前端测试**

- 加载后显示「联网搜索」与两个开关（research 默认 checked）。
- 未配置 Tavily 时打开 Tavily 开关点保存 → 显示错误（前端可先本地校验：`tavilyEnabled && !tavilyConfigured && !tavilyKeyInput`）。
- 点击清除 Tavily 调用 `clearTavilySettings`。

- [ ] **Step 2: 跑测试失败**

```bash
cd /Users/orange/Desktop/code/share-data/frontend-advisor && npm test -- --run src/pages/AgentSettingsPage.test.tsx
```

- [ ] **Step 3: 实现 UI**

在 DeepSeek 模型选择下方：

1. 标题「联网搜索」
2. checkbox：DeepSeek 联网综述（web_research）+ 说明文案
3. checkbox：Tavily 搜索 + 网页抓取
4. Tavily Key password 输入；hint 行；链接 `https://docs.tavily.com/`
5. 按钮「清除 Tavily Key」
6. 保存：PUT 合并 deepseek + web 字段；`api_key` 仅在非空时发送

注意：当前保存逻辑在 Key 为空时无法「只存开关」——改为允许 `configured` 已为 true 时 `api_key` 可空，只提交开关/Tavily。

- [ ] **Step 4: 跑前端测试通过**

```bash
npm test -- --run src/pages/AgentSettingsPage.test.tsx
```

- [ ] **Step 5: 不 commit**

---

### Task 9: 端到端验收（手工 + 轻量集成）

**Files:**
- 可选补充：`backend/tests/test_web_tools_runtime.py`（mock 下游，测工具返回与配额）

- [ ] **Step 1: 自动化**

```bash
cd /Users/orange/Desktop/code/share-data/backend
python -m pytest -q tests/test_web_limits.py tests/test_web_fetch.py tests/test_web_tavily.py tests/test_web_research.py tests/test_llm_web_settings.py tests/test_web_tools_mount.py tests/test_web_progress.py
cd /Users/orange/Desktop/code/share-data/frontend-advisor
npm test -- --run src/pages/AgentSettingsPage.test.tsx src/pages/AgentChatPage.test.tsx
```

Expected: 全 PASS。

- [ ] **Step 2: 手工检查清单**

1. 仅配 DeepSeek → Agent 工具含 `web_research`；问「用联网查一下今日央行相关公开报道要点」应触发 research（有额度/网络时）。
2. 设置页关闭 web_research、不开 Tavily → 双关；再问联网类问题不应出现上述工具。
3. 填写 Tavily Key 并开启 → 出现 `web_search`/`fetch_url`；`fetch_url` 对 `http://127.0.0.1` 返回错误。
4. GET settings 无明文 Key。
5. 进度条出现对应中文 step。

- [ ] **Step 3: 不 commit（交用户决定是否提交）**

---

## Spec Coverage Checklist

| 规格项 | 任务 |
|--------|------|
| 条件挂载三工具 | Task 7 |
| DeepSeek web_research + 固定模型 | Task 4, 7 |
| Tavily search + fetch_url | Task 2, 3, 7 |
| 设置字段/GET/PUT/清除 Tavily | Task 5, 8 |
| 默认开 research / 关 Tavily | Task 5 |
| 无白名单 + SSRF | Task 2 |
| config 限额 | Task 1 |
| 每轮调用上限 | Task 1, 7 |
| Prompt 规则 | Task 7 |
| 进度 step | Task 6 |
| UI `/agent/settings` | Task 8 |
| 清除 DeepSeek 保留 Tavily | Task 5 |
| 验收 | Task 9 |
