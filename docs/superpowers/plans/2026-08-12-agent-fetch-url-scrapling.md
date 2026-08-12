# Agent fetch_url Scrapling 增强抓取 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `fetch_url` 上实现 httpx → Scrapling Fetcher → StealthyFetcher 自动升级，并在任意联网能力开启时挂载精读工具。

**Architecture:** 保留 L1 `fetch_url_text`；新建 `web_fetch_escalation.py` 编排升级判定与 L2/L3（可注入 mock）；`build_web_tools` 将 `fetch_url` 从 Tavily 解耦；进度增加 `fetch_url_l2` / `fetch_url_l3`；Docker 安装 `scrapling[fetchers]` 与浏览器。

**Tech Stack:** Python 3.12、httpx、scrapling[fetchers]==0.4.14、Playwright/Chromium（via `scrapling install`）、pytest、现有 LangChain tools / SSE progress

## Global Constraints

- Spec：`docs/superpowers/specs/2026-08-12-agent-fetch-url-scrapling-design.md`
- 工具名必须仍为 `fetch_url`；不对 Agent 暴露新工具名
- SSRF 拒绝时禁止升级到 L2/L3
- 一次 `fetch_url` 调用只消耗 1 次 `consume_web_quota("fetch_url")`
- Docker 镜像标签仍为 `share-data:amd64`（禁止部署默认用 `latest`）
- 不做 CSS/XPath 结构化抽数、不做独立 Scrapling 用户开关、不引入 MCP
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/config.yaml` | `agent_web.fetch_url.escalation` 配置段 |
| `backend/app/advisor/agent/web_fetch.py` | 复用 L1 + 导出 `_html_to_text` / `is_url_safe_for_fetch`（必要时把 `_html_to_text` 改为公开 `html_to_text`） |
| `backend/app/advisor/agent/web_fetch_escalation.py` | 升级判定 + 流水线 + Scrapling 适配（新建） |
| `backend/app/advisor/agent/web_tools.py` | 挂载解耦；`fetch_url` 调用 escalation |
| `backend/app/advisor/agent/progress.py` | 登记 `fetch_url_l2` / `fetch_url_l3` 与中文 message |
| `backend/app/advisor/agent/graph.py` | Prompt 规则 20 微调 |
| `backend/requirements.txt` | `scrapling[fetchers]==0.4.14` |
| `deploy/Dockerfile` | 系统依赖 + `scrapling install` |
| `frontend-advisor/src/agentApi.ts` | Progress step 联合类型 |
| `frontend-advisor/src/pages/AgentChatPage.tsx` | WEB_STEPS / 中文标签 |
| `frontend-advisor/src/pages/AgentSettingsPage.tsx` | 联网说明文案一句 |
| `backend/tests/test_web_fetch_escalation.py` | 升级判定与流水线单测（新建） |
| `backend/tests/test_web_tools_mount.py` | 挂载用例更新 |
| `backend/tests/test_web_progress.py` | 新 step 冒烟 |
| `backend/tests/test_web_fetch.py` | 回归保留 |

---

### Task 1: 配置 + 升级判定纯函数

**Files:**
- Modify: `backend/app/advisor/config.yaml`（`agent_web.fetch_url` 下追加 `escalation`）
- Modify: `backend/app/advisor/agent/web_fetch.py`（将 `_html_to_text` 公开为 `html_to_text`，旧名可保留别名）
- Create: `backend/app/advisor/agent/web_fetch_escalation.py`（本任务只放判定与配置读取）
- Create: `backend/tests/test_web_fetch_escalation.py`

**Interfaces:**
- Produces:
  - `DEFAULT_BLOCK_PATTERNS: list[str]`
  - `get_escalation_config(cfg: dict | None = None) -> dict`  
    键：`enabled`, `min_text_chars`, `max_total_seconds`, `l2_timeout_seconds`, `l3_timeout_seconds`, `enable_stealth`, `solve_cloudflare`, `headless`, `block_patterns`
  - `is_ssrf_or_policy_error(text: str) -> bool`  
    `text` 以 `错误：禁止` 开头（或含「内网」「本机」「仅允许 http」等既有禁止文案）→ True
  - `needs_escalation(text: str, *, min_text_chars: int, block_patterns: list[str]) -> bool`  
    规则：`is_ssrf_or_policy_error` → **False**（调用方负责直接返回）；否则若以 `错误：` 开头 / 去 meta 后正文长度 `< min_text_chars` / 正文小写匹配任一 `block_patterns` → True
  - `strip_fetch_via_meta(text: str) -> str`
  - `with_fetch_via(text: str, via: Literal["httpx","scrapling","stealth"]) -> str`  
    成功正文前加 `# fetch_via: {via}\n`

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_web_fetch_escalation.py
from app.advisor.agent.web_fetch_escalation import (
    is_ssrf_or_policy_error,
    needs_escalation,
    with_fetch_via,
    strip_fetch_via_meta,
)

def test_ssrf_error_is_policy():
    assert is_ssrf_or_policy_error("错误：禁止：目标为内网或本机地址") is True
    assert is_ssrf_or_policy_error("错误：HTTP 403") is False

def test_needs_escalation_short_and_block():
    assert needs_escalation("ok " * 5, min_text_chars=200, block_patterns=["just a moment"]) is True
    long_ok = "正文内容" * 100
    assert needs_escalation(long_ok, min_text_chars=200, block_patterns=["just a moment"]) is False
    assert needs_escalation(
        "Just a Moment... please wait",
        min_text_chars=200,
        block_patterns=["just a moment"],
    ) is True
    assert needs_escalation("错误：HTTP 503", min_text_chars=200, block_patterns=[]) is True

def test_fetch_via_meta_roundtrip():
    body = with_fetch_via("hello world", "scrapling")
    assert body.startswith("# fetch_via: scrapling\n")
    assert strip_fetch_via_meta(body) == "hello world"
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd backend && python -m pytest tests/test_web_fetch_escalation.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现配置段 + 纯函数**

在 `config.yaml` 的 `fetch_url` 下追加：

```yaml
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

实现 `web_fetch_escalation.py` 中上述函数；`get_escalation_config` 从 `get_agent_web_config()["fetch_url"]["escalation"]` 读，缺省用上表默认值。

将 `web_fetch._html_to_text` 重命名/导出为 `html_to_text`（保持 `_html_to_text = html_to_text` 兼容）。

- [ ] **Step 4: 跑测确认通过**

Run: `cd backend && python -m pytest tests/test_web_fetch_escalation.py -v`  
Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add backend/app/advisor/config.yaml \
  backend/app/advisor/agent/web_fetch.py \
  backend/app/advisor/agent/web_fetch_escalation.py \
  backend/tests/test_web_fetch_escalation.py
git commit -m "feat: add fetch_url escalation decision helpers"
```

---

### Task 2: 升级流水线（可注入 L1/L2/L3）

**Files:**
- Modify: `backend/app/advisor/agent/web_fetch_escalation.py`
- Modify: `backend/tests/test_web_fetch_escalation.py`

**Interfaces:**
- Consumes: Task 1 全部函数；`is_url_safe_for_fetch`、`fetch_url_text`、`html_to_text` from `web_fetch`
- Produces:
  - `fetch_url_with_escalation(url: str, *, cfg: dict | None = None, l1=None, l2=None, l3=None, on_level=None) -> str`  
    - `l1/l2/l3`: 可选 `Callable[[str], str]`，默认分别为 httpx / scrapling HTTP / stealth 适配器  
    - `on_level`: 可选 `Callable[[str], None]`，在进入级别时回调 `"httpx"|"scrapling"|"stealth"`（供 progress 用）  
    - 行为：  
      1. 先 `is_url_safe_for_fetch`；失败 → `错误：{reason}`，**不调用** l1/l2/l3  
      2. 若 `escalation.enabled` 为 false → 只跑 l1，成功则 `with_fetch_via(..., "httpx")`  
      3. 否则按 L1→L2→L3；每级结果若 `is_ssrf_or_policy_error` → 立即返回该错误  
      4. 若 `needs_escalation` → 升下一级；若 `enable_stealth` 为 false 则跳过 L3  
      5. L2/L3 默认实现若 `ImportError` / 浏览器缺失 → 跳过该级（不抛崩）  
      6. 全部失败 → `错误：抓取失败（已尝试 …）: …`  
      7. 成功 → `with_fetch_via(strip_meta(text), via)`  
      8. 用 `time.monotonic()` 尊重 `max_total_seconds`；超时则返回当前最优或错误  
  - `scrapling_http_fetch(url: str, *, timeout: float, max_text_chars: int) -> str`  
  - `scrapling_stealth_fetch(url: str, *, timeout: float, max_text_chars: int, headless: bool, solve_cloudflare: bool) -> str`  
    Scrapling 调用约定（0.4.x）：
    ```python
    from scrapling.fetchers import Fetcher, StealthyFetcher
    page = Fetcher.get(url)  # L2；若 API 为 fetch 则以安装版为准，单测 mock 不依赖真库
    page = StealthyFetcher.fetch(url, headless=..., solve_cloudflare=..., network_idle=True)
    # 文本：优先 page.body / html_content / get_all_text；统一经 html_to_text 截断
    ```
    实现时以已安装 `scrapling==0.4.14` 的实际属性为准；适配器内部 try/except 转 `错误：…`。

- [ ] **Step 1: 追加失败单测（mock 注入）**

```python
# 追加到 backend/tests/test_web_fetch_escalation.py
from app.advisor.agent.web_fetch_escalation import fetch_url_with_escalation

def test_ssrf_does_not_call_backends(monkeypatch):
    calls = []
    def boom(_url):
        calls.append(1)
        return "不应调用"
    out = fetch_url_with_escalation(
        "http://127.0.0.1/",
        l1=boom, l2=boom, l3=boom,
        cfg={"allowed_ports": [80, 443], "escalation": {"enabled": True}},
    )
    assert out.startswith("错误：")
    assert calls == []

def test_escalate_l1_short_to_l2():
    levels = []
    out = fetch_url_with_escalation(
        "https://example.com/ok",
        l1=lambda u: "short",
        l2=lambda u: "B" * 250,
        l3=lambda u: "should-not-run",
        on_level=levels.append,
        cfg={
            "allowed_ports": [80, 443],
            "escalation": {
                "enabled": True,
                "min_text_chars": 200,
                "max_total_seconds": 90,
                "enable_stealth": True,
                "block_patterns": [],
            },
        },
    )
    assert "# fetch_via: scrapling" in out
    assert "B" * 50 in out
    assert levels == ["httpx", "scrapling"]

def test_escalate_to_stealth_when_l2_blocked():
    out = fetch_url_with_escalation(
        "https://example.com/cf",
        l1=lambda u: "错误：HTTP 403",
        l2=lambda u: "Just a Moment cloudflare",
        l3=lambda u: "REAL ARTICLE " + ("x" * 200),
        cfg={
            "allowed_ports": [80, 443],
            "escalation": {
                "enabled": True,
                "min_text_chars": 200,
                "enable_stealth": True,
                "block_patterns": ["just a moment"],
            },
        },
    )
    assert "# fetch_via: stealth" in out

def test_escalation_disabled_keeps_short_l1():
    out = fetch_url_with_escalation(
        "https://example.com/x",
        l1=lambda u: "tiny",
        l2=lambda u: "L2" * 200,
        l3=lambda u: "L3" * 200,
        cfg={"allowed_ports": [80, 443], "escalation": {"enabled": False, "min_text_chars": 200}},
    )
    assert "# fetch_via: httpx" in out
    assert "tiny" in out
```

注意：`fetch_url_with_escalation` 在调用 l1 前仍会做 SSRF；对 `https://example.com/...` 需 mock `is_url_safe_for_fetch` 返回 `(True, "")`，避免测试环境 DNS 波动：

```python
monkeypatch.setattr(
    "app.advisor.agent.web_fetch_escalation.is_url_safe_for_fetch",
    lambda url, allowed_ports: (True, ""),
)
```
（在上述非 SSRF 用例里加上 monkeypatch 参数。）

- [ ] **Step 2: 跑测确认新用例失败**

Run: `cd backend && python -m pytest tests/test_web_fetch_escalation.py -v`  
Expected: 新用例 FAIL（函数未实现）

- [ ] **Step 3: 实现流水线 + 默认 Scrapling 适配器**

默认 `l1=lambda url: fetch_url_text(url, cfg=...)`（不要自动加 fetch_via；由流水线统一加）。  
L2/L3 包装超时可用线程/`signal` 或简单依赖 scrapling 自身超时参数；墙钟用 `max_total_seconds` 在级别之间检查。

- [ ] **Step 4: 跑测通过 + 回归**

Run:

```bash
cd backend && python -m pytest tests/test_web_fetch_escalation.py tests/test_web_fetch.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add backend/app/advisor/agent/web_fetch_escalation.py backend/tests/test_web_fetch_escalation.py
git commit -m "feat: implement fetch_url httpx/scrapling/stealth pipeline"
```

---

### Task 3: 挂载解耦 + 工具接线 + 配额

**Files:**
- Modify: `backend/app/advisor/agent/web_tools.py`
- Modify: `backend/tests/test_web_tools_mount.py`
- Create or Modify: `backend/tests/test_web_tools_fetch_quota.py`（若更短可并入 mount 测试文件）

**Interfaces:**
- Consumes: `fetch_url_with_escalation`
- Produces: `build_web_tools` 行为变更：
  - `web_research` flag → 挂 `web_research`
  - `tavily` flag → 挂 `web_search`
  - `web_research or tavily` → 挂 `fetch_url`（调用 escalation；`consume_web_quota` 仍只在工具入口一次）

- [ ] **Step 1: 改挂载测试为期望新行为**

```python
# backend/tests/test_web_tools_mount.py
from app.advisor.agent.web_tools import build_web_tools

def test_mount_research_includes_fetch_url(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": True, "tavily": False},
    )
    names = {t.name for t in build_web_tools("u1")}
    assert names == {"web_research", "fetch_url"}

def test_mount_tavily_only(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": False, "tavily": True},
    )
    names = {t.name for t in build_web_tools("u1")}
    assert names == {"web_search", "fetch_url"}

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

- [ ] **Step 2: 跑测确认旧期望失败**

Run: `cd backend && python -m pytest tests/test_web_tools_mount.py -v`  
Expected: `test_mount_research_includes_fetch_url` FAIL（当前只有 `web_research`）

- [ ] **Step 3: 改 `web_tools.py`**

结构改为：

```python
if flags.get("web_research"):
    # 定义并 append web_research（保持现有实现）

if flags.get("tavily"):
    # 定义并 append web_search（保持现有实现）

if flags.get("web_research") or flags.get("tavily"):
    @tool
    def fetch_url(url: str) -> str:
        """抓取公网网页正文（http/https）。禁止内网/本机地址。
        用户给出链接、或 web_research/web_search 得到候选 URL 后可调用。
        困难页面会自动增强抓取。"""
        quota = consume_web_quota("fetch_url")
        if quota:
            return quota
        emit_progress(step="fetch_url", status="started", phase="main_agent")
        try:
            def on_level(via: str) -> None:
                step = {"httpx": "fetch_url", "scrapling": "fetch_url_l2", "stealth": "fetch_url_l3"}[via]
                if via != "httpx":
                    emit_progress(step=step, status="started", phase="main_agent")
            out = fetch_url_with_escalation(url, on_level=on_level)
            failed = out.startswith("错误：")
            emit_progress(
                step="fetch_url",
                status="failed" if failed else "completed",
                phase="main_agent",
                error_code="fetch_url_failed" if failed else None,
            )
            return out
        except Exception as exc:
            emit_progress(step="fetch_url", status="failed", phase="main_agent", error_code="fetch_url_failed")
            return f"错误：fetch_url 失败: {type(exc).__name__}"
    tools.append(fetch_url)
```

（若 Task 4 尚未登记 `fetch_url_l2`，本任务可暂只 emit `fetch_url`，Task 4 再补 `on_level`；**推荐本任务与 Task 4 同 PR 顺序：先做 Task 4 再接线 on_level**。实现顺序：若并行困难，本任务先不调用新 step，Task 4 完成后再加一行。）

**推荐实现顺序修正：** 先完成 Task 4 进度 step，再在本任务接线 `on_level`。若严格按任务号，Task 3 Step 3 可先不传 `on_level`，Task 4 末步回来补上。

- [ ] **Step 4: 配额单测（一次升级只计 1 次）**

```python
# backend/tests/test_web_tools_fetch_quota.py
from app.advisor.agent.web_limits import reset_web_quotas  # 若不存在则用现有测试里的 reset 方式
from app.advisor.agent import web_tools as wt

def test_fetch_url_quota_once_across_escalation(monkeypatch):
    monkeypatch.setattr(wt, "web_tool_flags", lambda uid: {"web_research": True, "tavily": False})
    # reset counters — 对照 tests/test_web_limits.py 的写法
    from app.advisor.agent.web_limits import _COUNTERS
    _COUNTERS.set({"web_research": 0, "web_search": 0, "fetch_url": 0})

    calls = {"n": 0}
    def fake_escalation(url, on_level=None, **kwargs):
        calls["n"] += 1
        if on_level:
            on_level("httpx")
            on_level("scrapling")
        return "# fetch_via: scrapling\n" + ("Z" * 250)

    monkeypatch.setattr(wt, "fetch_url_with_escalation", fake_escalation)
    tool = next(t for t in wt.build_web_tools("u1") if t.name == "fetch_url")
    assert not tool.invoke({"url": "https://example.com"}).startswith("错误：")
    # 第二次仍应成功直至 cap；关键 counters["fetch_url"] == 1
    from app.advisor.agent.web_limits import _COUNTERS
    assert _COUNTERS.get()["fetch_url"] == 1
    assert calls["n"] == 1
```

若 `_COUNTERS` 为私有且测试不宜依赖，改为 mock `consume_web_quota` 计数调用次数 == 1。

- [ ] **Step 5: 跑测通过**

Run:

```bash
cd backend && python -m pytest tests/test_web_tools_mount.py tests/test_web_tools_fetch_quota.py -v
```

Expected: PASS

- [ ] **Step 6: Commit（仅当用户要求时）**

```bash
git add backend/app/advisor/agent/web_tools.py \
  backend/tests/test_web_tools_mount.py \
  backend/tests/test_web_tools_fetch_quota.py
git commit -m "feat: mount fetch_url whenever any web tool is enabled"
```

---

### Task 4: 进度 step + 前端标签

**Files:**
- Modify: `backend/app/advisor/agent/progress.py`
- Modify: `backend/tests/test_web_progress.py`
- Modify: `frontend-advisor/src/agentApi.ts`
- Modify: `frontend-advisor/src/pages/AgentChatPage.tsx`
- Modify: `backend/app/advisor/agent/web_tools.py`（补上 `on_level` → l2/l3 emit，若 Task 3 未接）

**Interfaces:**
- Produces: `ProgressStep` 含 `"fetch_url_l2" | "fetch_url_l3"`；`progress_message`：
  - l2 started → `正在增强抓取（Scrapling）`
  - l3 started → `正在浏览器增强抓取`
  - completed/failed 可复用简短文案或与 fetch_url 类似

- [ ] **Step 1: 扩展 progress 测试**

```python
def test_emit_fetch_url_escalation_steps():
    events: list[dict] = []
    with bind_progress_sink(events.append):
        emit_progress(step="fetch_url_l2", status="started", phase="main_agent")
        emit_progress(step="fetch_url_l3", status="started", phase="main_agent")
    assert "Scrapling" in events[0]["message"] or "增强" in events[0]["message"]
    assert "浏览器" in events[1]["message"] or "增强" in events[1]["message"]
```

- [ ] **Step 2: 跑测失败 → 实现 → 通过**

Run: `cd backend && python -m pytest tests/test_web_progress.py -v`

在 `ProgressStep` / `PROGRESS_STEPS` 加入新 step；`progress_to_tool_trace` / message 函数同步。

前端：

```typescript
// agentApi.ts Progress step union 增加 'fetch_url_l2' | 'fetch_url_l3'
// AgentChatPage.tsx
fetch_url_l2: '增强抓取',
fetch_url_l3: '浏览器抓取',
const WEB_STEPS = new Set(['web_research', 'web_search', 'fetch_url', 'fetch_url_l2', 'fetch_url_l3'])
```

- [ ] **Step 3: Commit（仅当用户要求时）**

```bash
git add backend/app/advisor/agent/progress.py backend/tests/test_web_progress.py \
  frontend-advisor/src/agentApi.ts frontend-advisor/src/pages/AgentChatPage.tsx \
  backend/app/advisor/agent/web_tools.py
git commit -m "feat: emit fetch_url escalation progress steps"
```

---

### Task 5: Prompt + 设置页文案

**Files:**
- Modify: `backend/app/advisor/agent/graph.py`（规则 20）
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.tsx`

- [ ] **Step 1: 改 Prompt**

将规则 20 改为语义等价于：

```text
20. 联网：若已挂载 web_research，综合调研优先用之；若已挂载 web_search，需自行筛选来源时先 web_search；
   若已挂载 fetch_url，用户给出链接或已有候选 URL 时可直接 fetch_url 精读（不必强绑必须先 web_search）。
   引用须带来源 URL，禁止编造链接。
   A 股结构化新闻/联播/指数点位仍优先专用工具（规则 6–9）；个股实时涨跌用 get_stock_quotes，勿用搜索页数字（遵守 2c）。
```

- [ ] **Step 2: 设置页**

在「联网搜索」说明段落后追加一句（不新增开关）：

```tsx
<p className="meta-line">
  精读网页在任一联网能力开启时可用；困难页面会自动增强抓取。
</p>
```

并把 Tavily 文案从「Tavily 搜索 + 网页抓取」改为「Tavily 搜索」（网页抓取已不专属于 Tavily），避免误导：

```tsx
Tavily 搜索（web_search）
```

- [ ] **Step 3: 目视确认 / 如有 vitest 设置页测试则跑一下**

- [ ] **Step 4: Commit（仅当用户要求时）**

```bash
git add backend/app/advisor/agent/graph.py frontend-advisor/src/pages/AgentSettingsPage.tsx
git commit -m "docs: clarify fetch_url availability in agent prompt and settings"
```

---

### Task 6: 依赖 + Docker

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `deploy/Dockerfile`
- Modify: `README.md`（简短运维说明，可选但推荐）

- [ ] **Step 1: 加依赖**

```text
scrapling[fetchers]==0.4.14
```

- [ ] **Step 2: 更新 Dockerfile runtime**

在现有 `apt-get install` 中增加 Playwright/Chromium 常用系统库（按 Scrapling / Playwright 官方依赖列表；至少包含字体与常见 `.so` 依赖）。示例骨架（实现时对照 `playwright install-deps` 输出裁剪）：

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        # playwright/chromium runtime libs (expand as needed)
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt \
    && scrapling install
```

构建标签仍：`docker build -f deploy/Dockerfile -t share-data:amd64 .`

- [ ] **Step 3: README 补一句**

在部署/Agent 相关段落注明：增强网页抓取依赖镜像内 Chromium；本地无浏览器时 L3 自动跳过。

- [ ] **Step 4: 本地验证导入（可不装浏览器）**

```bash
cd backend && python -c "from app.advisor.agent.web_fetch_escalation import fetch_url_with_escalation; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit（仅当用户要求时）**

```bash
git add backend/requirements.txt deploy/Dockerfile README.md
git commit -m "chore: add scrapling fetchers and browser install to image"
```

---

### Task 7: 回归清单

**Files:** 无新代码（验证）

- [ ] **Step 1: 跑相关后端测试**

```bash
cd backend && python -m pytest \
  tests/test_web_fetch.py \
  tests/test_web_fetch_escalation.py \
  tests/test_web_tools_mount.py \
  tests/test_web_tools_fetch_quota.py \
  tests/test_web_progress.py \
  tests/test_web_limits.py \
  -v
```

Expected: 全部 PASS

- [ ] **Step 2: 手工冒烟（可选）**

1. 仅开 `web_research`：Agent 工具列表含 `fetch_url`，无 `web_search`  
2. `fetch_url("http://127.0.0.1/")` → 禁止文案  
3. 对普通静态页 → `# fetch_via: httpx`  
4. mock/真实 Cloudflare 壳页 → 升到 scrapling/stealth（有浏览器时）

- [ ] **Step 3: 对照 spec 勾选完成**

---

## Spec coverage

| Spec 项 | Task |
|---------|------|
| L1→L2→L3 流水线 | Task 2 |
| 升级判定（短/壳/硬失败） | Task 1–2 |
| SSRF 不升级 | Task 2 |
| `fetch_via` meta | Task 1–2 |
| 任意联网挂载 `fetch_url` | Task 3 |
| `web_search` 仍仅 Tavily | Task 3 |
| 配额只计 1 次 | Task 3 |
| 进度 l2/l3 | Task 4 |
| Prompt / 设置文案 | Task 5 |
| scrapling 依赖 + Docker 浏览器 | Task 6 |
| 缺依赖降级不 crash | Task 2 适配器 try/except + Task 7 |
| 非目标（抽数/MCP/独立开关） | 不实现 |

## Self-review notes

- 无 TBD；Scrapling 版本钉死 `0.4.14`；Response 字段名要求实现时对照安装包（适配器隔离，单测不依赖真浏览器）
- Task 3 与 Task 4 的 `on_level` 接线：若分提交，先 Task 4 登记 step，再在 Task 3 或 Task 4 末步接线
- `test_web_tools_mount` 旧用例 `test_mount_defaults_research_only` 必须改为含 `fetch_url`
