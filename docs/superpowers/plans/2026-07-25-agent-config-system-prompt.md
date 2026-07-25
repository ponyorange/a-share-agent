# Agent 配置（系统提示词 + 知识库注入调整）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将「知识库」升级为「Agent 配置」：用户可配置追加式系统提示词（≤6000 字）；必选知识改为每轮注入消息上下文；可选知识逻辑不变；路由改为 `/agent/config`。

**Architecture:** 新模块 `agent_config.py` 存取 per-user `system_prompt`；`knowledge.py` 拆分 system 目录片段与 always 消息正文；`graph.build_system_prompt` 追加用户文案且只含可选目录；聊天组装时前置 `SystemMessage` 必选知识（不入库）；前端 `KnowledgePage` 改造为 Agent 配置页并换路由。

**Tech Stack:** FastAPI、PyMongo、LangChain `SystemMessage`、React + Vitest（frontend-advisor）

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-agent-config-system-prompt-design.md`
- 用户系统提示：追加在产品 `SYSTEM_PROMPT` 之后，不可替换/关闭工具与安全规则
- 用户 `system_prompt` ≤ **6000** Unicode 字符；空字符串合法
- 知识库上限不变：单条 body ≤ 8000；启用 always 合计 ≤ 6000；启用 on_demand ≤ 50
- 必选知识：运行时注入 messages，**不**写入 chat store
- 路由：`/agent/config`；`/agent/knowledge` → redirect 到 `/agent/config`
- DeepSeek `/agent/settings` 不动
- Commit 仅在用户明确要求时执行；计划中的 commit 步骤默认跳过

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/agent_config.py` | 用户 system_prompt 存取与校验 |
| `backend/app/db.py` | `user_agent_config` 索引 |
| `backend/app/advisor/knowledge.py` | 拆分 always 正文 / on_demand 目录构建 |
| `backend/app/advisor/agent/graph.py` | 组装 system + 前置必选 SystemMessage |
| `backend/app/advisor/routes.py` | GET/PUT system-prompt |
| `backend/tests/test_agent_config.py` | system_prompt 校验与存取（mock db） |
| `backend/tests/test_knowledge.py` | 更新 prompt 拆分断言 |
| `frontend-advisor/src/agentApi.ts` | fetch/save system prompt |
| `frontend-advisor/src/pages/KnowledgePage.tsx` | Agent 配置 UI（可保留文件名或重命名） |
| `frontend-advisor/src/pages/KnowledgePage.test.tsx` | 文案与系统提示区块测试 |
| `frontend-advisor/src/App.tsx` | 路由与导航 |
| `frontend-advisor/src/App.test.tsx` | 导航断言 |
| `frontend-advisor/src/components/MobileAgentMoreMenu.tsx` | 移动端菜单文案/路径 |

---

### Task 1: agent_config 模块 + 单测

**Files:**
- Create: `backend/app/advisor/agent_config.py`
- Create: `backend/tests/test_agent_config.py`
- Modify: `backend/app/db.py`（`ensure_indexes` 增加集合索引）

**Interfaces:**
- Produces:
  - `SYSTEM_PROMPT_LIMIT = 6000`
  - `get_system_prompt(user_id: str) -> str`  # 无文档或空 → `""`
  - `public_system_prompt(user_id: str) -> dict`  # `{ "system_prompt": str, "updated_at": str | None }`
  - `save_system_prompt(user_id: str, text: str) -> dict`  # 校验后 upsert；raises `ValueError`
  - 集合名：`user_agent_config`；文档字段：`user_id`, `system_prompt`, `updated_at`

- [ ] **Step 1: 写失败单测**

```python
# backend/tests/test_agent_config.py
from unittest.mock import MagicMock, patch

import pytest

from app.advisor import agent_config as ac


def test_validate_rejects_over_limit():
    with pytest.raises(ValueError, match="6000"):
        ac.validate_system_prompt("x" * 6001)


def test_validate_accepts_empty_and_limit():
    assert ac.validate_system_prompt("") == ""
    assert ac.validate_system_prompt("  hello  ") == "hello"
    assert len(ac.validate_system_prompt("y" * 6000)) == 6000


def test_get_system_prompt_missing_returns_empty():
    col = MagicMock()
    col.find_one.return_value = None
    with patch.object(ac, "_col", return_value=col):
        assert ac.get_system_prompt("u1") == ""


def test_save_system_prompt_upserts():
    col = MagicMock()
    col.find_one.return_value = {
        "user_id": "u1",
        "system_prompt": "自称小顾",
        "updated_at": None,
    }
    with patch.object(ac, "_col", return_value=col):
        out = ac.save_system_prompt("u1", "自称小顾")
    col.update_one.assert_called_once()
    assert out["system_prompt"] == "自称小顾"
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_agent_config.py -v`

Expected: FAIL（模块不存在或 import 失败）

- [ ] **Step 3: 实现模块**

```python
# backend/app/advisor/agent_config.py
"""Per-user agent system prompt (appended after product SYSTEM_PROMPT)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import get_db

SYSTEM_PROMPT_LIMIT = 6000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _col():
    return get_db().user_agent_config


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def validate_system_prompt(text: str) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) > SYSTEM_PROMPT_LIMIT:
        raise ValueError(
            f"系统提示词不能超过 {SYSTEM_PROMPT_LIMIT} 字（当前 {len(cleaned)}）"
        )
    return cleaned


def get_system_prompt(user_id: str) -> str:
    doc = _col().find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        return ""
    return str(doc.get("system_prompt") or "")


def public_system_prompt(user_id: str) -> dict[str, Any]:
    doc = _col().find_one({"user_id": user_id}, {"_id": 0})
    if not doc:
        return {"system_prompt": "", "updated_at": None}
    return {
        "system_prompt": str(doc.get("system_prompt") or ""),
        "updated_at": _iso(doc.get("updated_at")),
    }


def save_system_prompt(user_id: str, text: str) -> dict[str, Any]:
    cleaned = validate_system_prompt(text)
    now = _now()
    _col().update_one(
        {"user_id": user_id},
        {"$set": {"system_prompt": cleaned, "updated_at": now}},
        upsert=True,
    )
    return public_system_prompt(user_id)
```

在 `backend/app/db.py` 的 `ensure_indexes` 中，在 `user_knowledge_items` 索引块后增加：

```python
    try:
        db.user_agent_config.create_index(
            [("user_id", ASCENDING)], unique=True
        )
    except Exception:
        pass
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_agent_config.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 2: 拆分知识库 prompt / 必选消息构建

**Files:**
- Modify: `backend/app/advisor/knowledge.py`
- Modify: `backend/tests/test_knowledge.py`

**Interfaces:**
- Consumes: 现有 `list_raw` / items 结构
- Produces:
  - `format_always_knowledge_section(items) -> str`  # 仅 enabled always 正文；无则 `""`
  - `format_on_demand_catalog_section(items) -> str`  # 仅 enabled on_demand 目录；无则 `""`
  - `format_knowledge_prompt_section(items) -> str`  # **仅** on_demand 目录（兼容旧名，行为变更）
  - `build_knowledge_prompt_section(user_id) -> str`  # 同上，读库
  - `build_always_knowledge_text(user_id) -> str`  # 读库后的 always 正文块

- [ ] **Step 1: 改写失败/更新单测**

将 `test_build_prompt_section_splits_modes` 改为：

```python
def test_format_sections_split_always_and_catalog():
    items = [
        {
            "id": "1",
            "title": "纪律",
            "mode": "always",
            "enabled": True,
            "description": "",
            "body": "不加杠杆",
        },
        {
            "id": "2",
            "title": "茅台笔记",
            "mode": "on_demand",
            "enabled": True,
            "description": "贵州茅台基本面",
            "body": "长文…",
        },
        {
            "id": "3",
            "title": "关闭",
            "mode": "always",
            "enabled": False,
            "description": "",
            "body": "不应出现",
        },
    ]
    always = kn.format_always_knowledge_section(items)
    catalog = kn.format_on_demand_catalog_section(items)
    system = kn.format_knowledge_prompt_section(items)

    assert "不加杠杆" in always
    assert "用户必选知识" in always
    assert "茅台笔记" not in always

    assert "茅台笔记" in catalog
    assert "贵州茅台基本面" in catalog
    assert "长文" not in catalog
    assert "不加杠杆" not in catalog
    assert "load_knowledge" in catalog

    assert system == catalog
    assert "不应出现" not in always and "不应出现" not in catalog
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge.py::test_format_sections_split_always_and_catalog -v`

Expected: FAIL（缺新函数或旧 `format_knowledge_prompt_section` 仍含 always）

- [ ] **Step 3: 实现拆分**

替换 `knowledge.py` 中 `format_knowledge_prompt_section` 及相关辅助：

```python
def format_always_knowledge_section(items: list[dict[str, Any]]) -> str:
    always = [
        x for x in items if x.get("enabled") and x.get("mode") == "always"
    ]
    if not always:
        return ""
    parts: list[str] = ["## 用户必选知识"]
    for x in always:
        parts.append(f"### {x.get('title')}\n{x.get('body') or ''}")
    return "\n\n".join(parts).strip()


def format_on_demand_catalog_section(items: list[dict[str, Any]]) -> str:
    optional = [
        x for x in items if x.get("enabled") and x.get("mode") == "on_demand"
    ]
    if not optional:
        return ""
    parts: list[str] = [
        "## 用户可选知识目录",
        "需要细则时调用 load_knowledge(id)；勿编造目录外知识；"
        "必选知识已在消息上下文中（若有），无需对必选条目重复 load。",
    ]
    for x in optional:
        parts.append(
            f"- id: {x.get('id')} | title: {x.get('title')} | desc: {x.get('description') or ''}"
        )
    return "\n\n".join(parts).strip()


def format_knowledge_prompt_section(items: list[dict[str, Any]]) -> str:
    """System 侧片段：仅可选知识目录（必选改走消息）。"""
    return format_on_demand_catalog_section(items)


def build_knowledge_prompt_section(user_id: str) -> str:
    return format_knowledge_prompt_section(list_raw(user_id))


def build_always_knowledge_text(user_id: str) -> str:
    return format_always_knowledge_section(list_raw(user_id))
```

- [ ] **Step 4: 跑全文件测试**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 3: graph 组装 system + 必选 SystemMessage；API 路由

**Files:**
- Modify: `backend/app/advisor/agent/graph.py`
- Modify: `backend/app/advisor/routes.py`
- Create or modify: `backend/tests/test_build_system_prompt.py`（小纯测，可放 `test_agent_config.py` 末尾）

**Interfaces:**
- Consumes: `agent_config.get_system_prompt`, `knowledge.build_knowledge_prompt_section`, `knowledge.build_always_knowledge_text`
- Produces:
  - `build_system_prompt(user_id) -> str` = 产品规则 + 用户 system_prompt + 可选目录
  - 聊天路径：`lc_messages` 在历史前插入必选 `SystemMessage`（若有正文）
  - `GET/PUT /api/advisor/agent-config/system-prompt`

- [ ] **Step 1: 写 build_system_prompt 单测（mock）**

追加到 `backend/tests/test_agent_config.py`：

```python
def test_build_system_prompt_appends_user_and_catalog():
    from app.advisor.agent import graph as agent_graph

    with (
        patch(
            "app.advisor.agent_config.get_system_prompt",
            return_value="请自称小顾。",
        ),
        patch(
            "app.advisor.knowledge.build_knowledge_prompt_section",
            return_value="## 用户可选知识目录\n- id: x",
        ),
    ):
        text = agent_graph.build_system_prompt("u1")
    assert text.startswith(agent_graph.SYSTEM_PROMPT.rstrip()[:20])
    assert "请自称小顾。" in text
    assert "用户可选知识目录" in text
    assert "必选知识已在系统提示中" not in agent_graph.SYSTEM_PROMPT
    assert "消息上下文" in agent_graph.SYSTEM_PROMPT or "必选" in agent_graph.SYSTEM_PROMPT
```

- [ ] **Step 2: 跑测确认失败（文案或拼接未改）**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_agent_config.py::test_build_system_prompt_appends_user_and_catalog -v`

Expected: FAIL

- [ ] **Step 3: 更新 SYSTEM_PROMPT 规则与 build_system_prompt**

在 `graph.py`：

1. 将规则 12 改为类似：

```text
12. 用户知识：消息上下文中可能含「用户必选知识」，须遵守；若系统提示含「用户可选知识目录」，需要细则时调用 load_knowledge(id)；勿编造目录外内容。
```

2. 替换 `build_system_prompt`：

```python
def build_system_prompt(user_id: str) -> str:
    from ..agent_config import get_system_prompt
    from ..knowledge import build_knowledge_prompt_section

    parts = [SYSTEM_PROMPT.rstrip()]
    user_sp = (get_system_prompt(user_id) or "").strip()
    if user_sp:
        parts.append("## 用户系统提示词\n" + user_sp)
    catalog = (build_knowledge_prompt_section(user_id) or "").strip()
    if catalog:
        parts.append(catalog)
    return "\n\n".join(parts) + "\n"
```

3. 在 `_iter_agent_chat_events_sync` 组装 `lc_messages` 处，在历史循环**之前**：

```python
from langchain_core.messages import SystemMessage
from ..knowledge import build_always_knowledge_text

lc_messages: list[Any] = []
always_text = (build_always_knowledge_text(user_id) or "").strip()
if always_text:
    lc_messages.append(SystemMessage(content=always_text))
for h in history:
    ...
```

确认 `SystemMessage` 已加入文件顶部的 messages import。

- [ ] **Step 4: 增加 routes**

在 `routes.py`（knowledge 路由附近）增加：

```python
class AgentSystemPromptBody(BaseModel):
    system_prompt: str = ""


@router.get("/agent-config/system-prompt")
def agent_system_prompt_get(
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .agent_config import public_system_prompt

    uid = _bind(user)
    return public_system_prompt(uid)


@router.put("/agent-config/system-prompt")
def agent_system_prompt_put(
    body: AgentSystemPromptBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    from .agent_config import save_system_prompt

    uid = _bind(user)
    try:
        return save_system_prompt(uid, body.system_prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 5: 跑测**

Run:

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_agent_config.py tests/test_knowledge.py tests/test_agent_chat_progress.py -v
```

Expected: PASS（`test_agent_chat_progress` 仍 mock `build_system_prompt`）

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 4: 前端 API + Agent 配置页

**Files:**
- Modify: `frontend-advisor/src/agentApi.ts`
- Modify: `frontend-advisor/src/pages/KnowledgePage.tsx`（页面职责变为 Agent 配置；文件名可保留以减少 churn，或重命名为 `AgentConfigPage.tsx` 并改 import——**推荐重命名**）
- Modify: `frontend-advisor/src/pages/KnowledgePage.test.tsx`（若重命名则同步）

**Interfaces:**
- Produces:
  - `fetchAgentSystemPrompt(): Promise<{ system_prompt: string; updated_at?: string | null }>`
  - `saveAgentSystemPrompt(system_prompt: string): Promise<...>`
  - 页面：系统提示词区块 + 知识库区块

- [ ] **Step 1: 先改测试期望（TDD）**

若保留 `KnowledgePage.tsx` 文件名，更新 `KnowledgePage.test.tsx`：

```tsx
vi.mock('../agentApi', () => ({
  listKnowledge,
  createKnowledge: vi.fn(),
  updateKnowledge: vi.fn(),
  deleteKnowledge: vi.fn(),
  fetchAgentSystemPrompt: vi.fn().mockResolvedValue({ system_prompt: '' }),
  saveAgentSystemPrompt: vi.fn(),
}))

it('Agent 配置页展示系统提示与知识库说明', async () => {
  render(<KnowledgePage />) // 或 AgentConfigPage
  expect(
    screen.getByText(/追加在产品规则之后|可覆盖称呼|系统提示词/),
  ).toBeInTheDocument()
  expect(
    screen.getByText(/必选知识会注入.*消息|每轮注入消息/),
  ).toBeInTheDocument()
  expect(await screen.findByText('交易纪律')).toBeInTheDocument()
})
```

- [ ] **Step 2: 跑前端测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/frontend-advisor && npm test -- --run src/pages/KnowledgePage.test.tsx`

Expected: FAIL（文案/ mock 未齐）

- [ ] **Step 3: agentApi 增加客户端**

```ts
export type AgentSystemPrompt = {
  system_prompt: string
  updated_at?: string | null
}

export function fetchAgentSystemPrompt(): Promise<AgentSystemPrompt> {
  return authFetch('/api/advisor/agent-config/system-prompt')
}

export function saveAgentSystemPrompt(
  system_prompt: string,
): Promise<AgentSystemPrompt> {
  return authFetch('/api/advisor/agent-config/system-prompt', {
    method: 'PUT',
    body: JSON.stringify({ system_prompt }),
  })
}
```

- [ ] **Step 4: 改造页面**

在知识库列表之上增加系统提示词区块（要点）：

- state：`systemPrompt`, `spLoading`, `spSaving`, `spMsg`/`spError`
- mount 时 `fetchAgentSystemPrompt` + 现有 `loadKnowledge`
- textarea：`value={systemPrompt}`，下方字数 `{systemPrompt.length}/6000`
- hint：追加在产品规则之后，可覆盖称呼/性格/纪律；工具与写操作确认规则始终保留
- 按钮「保存系统提示词」→ `saveAgentSystemPrompt`
- 知识库 hero/hint 改为：必选每轮注入消息上下文；可选目录 + Agent 按需加载；上限文案保留
- 必选 option 文案：`必选（注入消息上下文）`；badge title 同步

（可选）将文件重命名为 `AgentConfigPage.tsx` 并更新所有 import；测试文件同步改名。

- [ ] **Step 5: 跑前端测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/frontend-advisor && npm test -- --run src/pages/KnowledgePage.test.tsx`

Expected: PASS（若已重命名则跑新路径）

- [ ] **Step 6: Commit（默认跳过）**

---

### Task 5: 路由 `/agent/config` + 导航文案

**Files:**
- Modify: `frontend-advisor/src/App.tsx`
- Modify: `frontend-advisor/src/App.test.tsx`
- Modify: `frontend-advisor/src/components/MobileAgentMoreMenu.tsx`

**Interfaces:**
- Produces: NavLink「Agent 配置」→ `/agent/config`；Route 渲染配置页；`/agent/knowledge` → `<Navigate to="/agent/config" replace />`

- [ ] **Step 1: 更新 App 测试**

将「知识库」相关断言改为「Agent 配置」与 `/agent/config`：

```tsx
expect(screen.getByRole('link', { name: 'Agent 配置' })).toHaveAttribute(
  'href',
  '/agent/config',
)

it('Agent 配置路由渲染配置页且非 chat shell', () => {
  // initialEntries: ['/agent/config']
  // 断言页面上系统提示或知识库相关文案 / heading
})
```

移动端菜单用例若断言「知识库」，同步改为「Agent 配置」。

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/frontend-advisor && npm test -- --run src/App.test.tsx`

Expected: FAIL

- [ ] **Step 3: 改路由与导航**

`App.tsx`：

```tsx
<NavLink to="/agent/config">Agent 配置</NavLink>
...
<Route path="/agent/config" element={<KnowledgePage />} />
{/* 或 AgentConfigPage */}
<Route
  path="/agent/knowledge"
  element={<Navigate to="/agent/config" replace />}
/>
```

注意：这两条必须写在 `<Route path="/agent/*" …>` **之前**。

`MobileAgentMoreMenu.tsx`：

```ts
{ to: '/agent/config', end: false, label: 'Agent 配置' },
```

- [ ] **Step 4: 跑相关前端测**

Run:

```bash
cd /Users/orange/Desktop/code/share-data/frontend-advisor && npm test -- --run src/App.test.tsx src/pages/KnowledgePage.test.tsx
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 6: 冒烟与回归核对

**Files:** 无新文件；手工/脚本冒烟

- [ ] **Step 1: 后端导入与 prompt 冒烟**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python - <<'PY'
from app.advisor.agent.graph import build_system_prompt, SYSTEM_PROMPT
from app.advisor.knowledge import format_always_knowledge_section, format_knowledge_prompt_section

assert "必选知识已在系统提示中" not in SYSTEM_PROMPT
items = [{
  "id": "1", "title": "A", "mode": "always", "enabled": True,
  "description": "", "body": "BODY",
}, {
  "id": "2", "title": "B", "mode": "on_demand", "enabled": True,
  "description": "desc", "body": "LONG",
}]
assert "BODY" in format_always_knowledge_section(items)
assert "BODY" not in format_knowledge_prompt_section(items)
assert "desc" in format_knowledge_prompt_section(items)
print("ok", build_system_prompt("nobody")[:40])
PY
```

Expected: 打印 `ok` 且无异常

- [ ] **Step 2: 后端测试套件相关子集**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_agent_config.py tests/test_knowledge.py tests/test_agent_chat_progress.py tests/test_data_agent_delegate.py -v
```

Expected: PASS（`test_data_agent_delegate` 若断言旧规则 12 文案则按新文案微调断言）

- [ ] **Step 3: 前端测试**

```bash
cd /Users/orange/Desktop/code/share-data/frontend-advisor && npm test -- --run src/App.test.tsx src/pages/KnowledgePage.test.tsx
```

Expected: PASS

- [ ] **Step 4: 手工验收清单（对照 spec）**

1. 空系统提示 + 无必选：聊天正常  
2. 保存系统提示「请自称小顾」→ 新对话自称变化；工具规则仍在  
3. 6001 字保存失败  
4. 启用必选 → 助手能引用必选内容；system 拼接逻辑不再含必选正文（可用临时 log 或问「你的必选知识有哪些」）  
5. 可选 `load_knowledge` 仍可用  
6. 导航「Agent 配置」→ `/agent/config`；访问 `/agent/knowledge` 跳到 `/agent/config`

- [ ] **Step 5: Commit（默认跳过；用户要求时再提交）**

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| Agent 配置页两区块 | 4 |
| 用户 system 追加、≤6000 | 1, 3, 4 |
| 称呼可覆盖默认 | 3（追加顺序） |
| 必选 → 消息 SystemMessage、不入库 | 2, 3 |
| 可选目录 + load_knowledge | 2, 3 |
| 路由 `/agent/config` + 旧路径 redirect | 5 |
| DeepSeek 页不动 | （无改动） |
| SYSTEM_PROMPT 文案调整 | 3 |
| 验收项 | 6 |

## Placeholder / consistency notes

- 函数名统一：`format_always_knowledge_section` / `format_on_demand_catalog_section` / `build_always_knowledge_text` / `get_system_prompt` / `save_system_prompt`
- 集合名统一：`user_agent_config`
- API 路径统一：`/api/advisor/agent-config/system-prompt`
- 前端路由统一：`/agent/config`
