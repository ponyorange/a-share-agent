# 投研助手用户知识库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每位用户提供可 CRUD 的知识库：必选正文注入动态 system prompt，可选目录 + `load_knowledge` 按需加载。

**Architecture:** 新模块 `knowledge.py` 负责 Mongo CRUD、校验与 prompt 片段；`routes.py` 暴露 REST；`graph.py` 用 `build_system_prompt(user_id)` 替代固定 `SYSTEM_PROMPT`；`tools.py` 注册 `load_knowledge`；设置页增加知识库区块。

**Tech Stack:** FastAPI、PyMongo、LangChain tools、React（frontend-advisor）

## Global Constraints

- 集合名：`user_knowledge_items`；字段：`id, user_id, title, mode(always|on_demand), enabled, description, body, created_at, updated_at`
- 单条 body ≤ 8000 字；title ≤ 80；description ≤ 200
- enabled+always 的 body 合计 ≤ 6000 字
- enabled+on_demand ≤ 50 条
- `on_demand` 必须非空 description；禁用条目不进 prompt、不进目录、load 返回错误
- UI 挂在 `AgentSettingsPage`，不新建独立路由
- Commit 仅在用户明确要求时执行（本仓库习惯）；计划中的 commit 步骤默认跳过

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/knowledge.py` | CRUD、校验、`build_knowledge_prompt_section`、`get_item` |
| `backend/app/db.py` | 索引 `(user_id, updated_at)` |
| `backend/app/advisor/routes.py` | REST |
| `backend/app/advisor/agent/graph.py` | 动态 prompt |
| `backend/app/advisor/agent/tools.py` | `load_knowledge` |
| `backend/tests/test_knowledge.py` | 校验与 prompt / CRUD（mock db） |
| `frontend-advisor/src/agentApi.ts` | 客户端 |
| `frontend-advisor/src/pages/AgentSettingsPage.tsx` | UI |
| `frontend-advisor/src/styles.css` | 必要样式（若现有 class 不够） |

---

### Task 1: knowledge 领域模块 + 单测

**Files:**
- Create: `backend/app/advisor/knowledge.py`
- Create: `backend/tests/test_knowledge.py`
- Modify: `backend/app/db.py`（`ensure_indexes` 增加索引）

**Interfaces:**
- Produces:
  - `ALWAYS_BODY_LIMIT = 6000`, `BODY_LIMIT = 8000`, `ON_DEMAND_ENABLED_LIMIT = 50`
  - `list_items(user_id: str, *, summary: bool = False) -> list[dict]`
  - `get_item(user_id: str, item_id: str) -> dict | None`
  - `create_item(user_id: str, payload: dict) -> dict`  # raises ValueError
  - `update_item(user_id: str, item_id: str, payload: dict) -> dict`  # raises ValueError / KeyError
  - `delete_item(user_id: str, item_id: str) -> bool`
  - `build_knowledge_prompt_section(user_id: str) -> str`  # only enabled
  - `public_item(doc: dict, *, include_body: bool = True) -> dict`

- [ ] **Step 1: 写失败单测（校验与 prompt 纯逻辑）**

```python
# backend/tests/test_knowledge.py
from unittest.mock import MagicMock, patch

import pytest

from app.advisor import knowledge as kn


def test_validate_on_demand_requires_description():
    with pytest.raises(ValueError, match="description"):
        kn.validate_payload(
            {
                "title": "笔记",
                "mode": "on_demand",
                "enabled": True,
                "description": "  ",
                "body": "正文",
            },
            existing_enabled=[],
            exclude_id=None,
        )


def test_validate_always_body_budget():
    existing = [
        {
            "id": "a",
            "mode": "always",
            "enabled": True,
            "body": "x" * 5000,
        }
    ]
    with pytest.raises(ValueError, match="6000"):
        kn.validate_payload(
            {
                "title": "纪律",
                "mode": "always",
                "enabled": True,
                "description": "",
                "body": "y" * 1500,
            },
            existing_enabled=existing,
            exclude_id=None,
        )


def test_build_prompt_section_splits_modes():
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
    text = kn.format_knowledge_prompt_section(items)
    assert "不加杠杆" in text
    assert "茅台笔记" in text
    assert "贵州茅台基本面" in text
    assert "长文" not in text
    assert "不应出现" not in text
    assert "load_knowledge" in text
```

- [ ] **Step 2: 跑测确认失败**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge.py -v
```

Expected: FAIL（模块/函数不存在）

- [ ] **Step 3: 实现 `knowledge.py`**

实现要点（完整文件写入仓库）：

```python
"""Per-user agent knowledge base (always / on_demand)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import get_db

ALWAYS_BODY_LIMIT = 6000
BODY_LIMIT = 8000
TITLE_LIMIT = 80
DESC_LIMIT = 200
ON_DEMAND_ENABLED_LIMIT = 50
MODES = frozenset({"always", "on_demand"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chars(s: str) -> int:
    return len(s or "")


def public_item(doc: dict[str, Any], *, include_body: bool = True) -> dict[str, Any]:
    out = {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "mode": doc.get("mode"),
        "enabled": bool(doc.get("enabled")),
        "description": doc.get("description") or "",
        "created_at": _iso(doc.get("created_at")),
        "updated_at": _iso(doc.get("updated_at")),
    }
    if include_body:
        out["body"] = doc.get("body") or ""
    return out


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def validate_payload(
    payload: dict[str, Any],
    *,
    existing_enabled: list[dict[str, Any]],
    exclude_id: str | None,
) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    mode = str(payload.get("mode") or "").strip()
    enabled = bool(payload.get("enabled", True))
    description = str(payload.get("description") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if _chars(title) > TITLE_LIMIT:
        raise ValueError(f"标题不能超过 {TITLE_LIMIT} 字")
    if mode not in MODES:
        raise ValueError("mode 必须是 always 或 on_demand")
    if not body:
        raise ValueError("正文不能为空")
    if _chars(body) > BODY_LIMIT:
        raise ValueError(f"单条正文不能超过 {BODY_LIMIT} 字")
    if _chars(description) > DESC_LIMIT:
        raise ValueError(f"描述不能超过 {DESC_LIMIT} 字")
    if mode == "on_demand" and not description:
        raise ValueError("可选知识必须填写 description")

    others = [x for x in existing_enabled if x.get("id") != exclude_id]
    if enabled and mode == "always":
        total = _chars(body) + sum(
            _chars(str(x.get("body") or ""))
            for x in others
            if x.get("mode") == "always" and x.get("enabled")
        )
        if total > ALWAYS_BODY_LIMIT:
            raise ValueError(
                f"必选知识启用正文合计不能超过 {ALWAYS_BODY_LIMIT} 字（当前将达到 {total}）"
            )
    if enabled and mode == "on_demand":
        n = 1 + sum(
            1
            for x in others
            if x.get("mode") == "on_demand" and x.get("enabled")
        )
        if n > ON_DEMAND_ENABLED_LIMIT:
            raise ValueError(
                f"启用的可选知识不能超过 {ON_DEMAND_ENABLED_LIMIT} 条"
            )
    return {
        "title": title,
        "mode": mode,
        "enabled": enabled,
        "description": description,
        "body": body,
    }


def format_knowledge_prompt_section(items: list[dict[str, Any]]) -> str:
    always = [
        x for x in items if x.get("enabled") and x.get("mode") == "always"
    ]
    optional = [
        x for x in items if x.get("enabled") and x.get("mode") == "on_demand"
    ]
    if not always and not optional:
        return ""
    parts: list[str] = []
    if always:
        parts.append("## 用户必选知识")
        for x in always:
            parts.append(f"### {x.get('title')}\n{x.get('body') or ''}")
    if optional:
        parts.append("## 用户可选知识目录")
        parts.append(
            "需要细则时调用 load_knowledge(id)；勿编造目录外知识；必选知识已在上方，无需重复加载。"
        )
        for x in optional:
            parts.append(
                f"- id: {x.get('id')} | title: {x.get('title')} | desc: {x.get('description') or ''}"
            )
    return "\n\n".join(parts).strip()


def _col():
    return get_db().user_knowledge_items


def list_raw(user_id: str) -> list[dict[str, Any]]:
    return list(
        _col().find({"user_id": user_id}, {"_id": 0}).sort("updated_at", -1)
    )


def list_items(user_id: str, *, summary: bool = False) -> list[dict[str, Any]]:
    return [
        public_item(d, include_body=not summary) for d in list_raw(user_id)
    ]


def get_item(user_id: str, item_id: str) -> dict[str, Any] | None:
    doc = _col().find_one({"user_id": user_id, "id": item_id}, {"_id": 0})
    return public_item(doc) if doc else None


def create_item(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    existing = list_raw(user_id)
    clean = validate_payload(
        payload, existing_enabled=existing, exclude_id=None
    )
    now = _now()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        **clean,
        "created_at": now,
        "updated_at": now,
    }
    _col().insert_one(doc)
    doc.pop("_id", None)
    return public_item(doc)


def update_item(
    user_id: str, item_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    existing = list_raw(user_id)
    if not any(x.get("id") == item_id for x in existing):
        raise KeyError(item_id)
    clean = validate_payload(
        payload, existing_enabled=existing, exclude_id=item_id
    )
    now = _now()
    _col().update_one(
        {"user_id": user_id, "id": item_id},
        {"$set": {**clean, "updated_at": now}},
    )
    doc = _col().find_one({"user_id": user_id, "id": item_id}, {"_id": 0})
    assert doc is not None
    return public_item(doc)


def delete_item(user_id: str, item_id: str) -> bool:
    res = _col().delete_one({"user_id": user_id, "id": item_id})
    return res.deleted_count > 0


def build_knowledge_prompt_section(user_id: str) -> str:
    return format_knowledge_prompt_section(list_raw(user_id))
```

在 `db.py` 的 `ensure_indexes()` 末尾（或 agent 相关索引旁）追加：

```python
db.user_knowledge_items.create_index(
    [("user_id", ASCENDING), ("updated_at", DESCENDING)]
)
db.user_knowledge_items.create_index(
    [("user_id", ASCENDING), ("id", ASCENDING)], unique=True
)
```

- [ ] **Step 4: 跑测确认通过**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge.py -v
```

Expected: 3 passed（可再补 mock CRUD 测，非必须）

- [ ] **Step 5: Commit（仅当用户要求）**

---

### Task 2: REST API

**Files:**
- Modify: `backend/app/advisor/routes.py`（在 llm/settings 路由附近插入）

**Interfaces:**
- Consumes: Task 1 的 CRUD 函数
- Produces: HTTP endpoints under `/api/advisor/knowledge`

- [ ] **Step 1: 增加 Pydantic body + 路由**

在 `routes.py` 中（`llm_settings_delete` 之后）加入：

```python
class KnowledgeBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)
    mode: str = Field(..., pattern="^(always|on_demand)$")
    enabled: bool = True
    description: str = ""
    body: str = Field(..., min_length=1, max_length=8000)


@router.get("/knowledge")
def knowledge_list(
    summary: bool = Query(default=False),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .knowledge import list_items

    uid = _bind(user)
    return {"items": list_items(uid, summary=summary)}


@router.post("/knowledge")
def knowledge_create(
    body: KnowledgeBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    from .knowledge import create_item

    uid = _bind(user)
    try:
        return create_item(uid, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge/{item_id}")
def knowledge_get(
    item_id: str, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    from .knowledge import get_item

    uid = _bind(user)
    item = get_item(uid, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return item


@router.put("/knowledge/{item_id}")
def knowledge_put(
    item_id: str,
    body: KnowledgeBody,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .knowledge import update_item

    uid = _bind(user)
    try:
        return update_item(uid, item_id, body.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="知识条目不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/knowledge/{item_id}")
def knowledge_delete(
    item_id: str, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    from .knowledge import delete_item

    uid = _bind(user)
    if not delete_item(uid, item_id):
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return {"ok": True}
```

确保文件顶部已有 `Query`、`Field`、`BaseModel`、`HTTPException` 导入（已有则不动）。

- [ ] **Step 2: 静态导入检查**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -c "from app.advisor.routes import knowledge_list; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit（仅当用户要求）**

---

### Task 3: Agent 动态 prompt + load_knowledge 工具

**Files:**
- Modify: `backend/app/advisor/agent/graph.py`
- Modify: `backend/app/advisor/agent/tools.py`
- Modify: `backend/tests/test_knowledge.py`（追加工具注册测）

**Interfaces:**
- Consumes: `build_knowledge_prompt_section(user_id)`, `get_item`
- Produces: `build_system_prompt(user_id: str) -> str`；工具 `load_knowledge`

- [ ] **Step 1: 更新 `graph.py`**

在 `SYSTEM_PROMPT` 规则中增加（编号接在现有最后一条免责声明前）：

```
N. 用户可选知识：若系统提示含「用户可选知识目录」，需要细则时调用 load_knowledge(id)；勿编造目录外内容。必选知识已在系统提示中。
```

（将 `N` 换成与现有规则连续的编号。）

新增：

```python
def build_system_prompt(user_id: str) -> str:
    from ..knowledge import build_knowledge_prompt_section

    extra = build_knowledge_prompt_section(user_id)
    if not extra:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT.rstrip()}\n\n{extra}\n"
```

将 `run_agent_stream`（及若存在的非流式入口）中：

```python
agent = create_react_agent(model, tools, prompt=SYSTEM_PROMPT)
```

改为：

```python
agent = create_react_agent(model, tools, prompt=build_system_prompt(user_id))
```

用 grep 确认文件内所有 `prompt=SYSTEM_PROMPT` 均已替换。

- [ ] **Step 2: 在 `tools.py` 注册 `load_knowledge`**

在 `build_tools` 内、`return [` 前加入：

```python
    @tool
    def load_knowledge(knowledge_id: str) -> str:
        """按 id 加载用户可选/知识库正文。目录在系统提示「用户可选知识目录」中。
        仅能加载当前用户且已启用的条目。"""
        from ..knowledge import get_item, list_raw

        _bind()
        kid = (knowledge_id or "").strip()
        raw = next((x for x in list_raw(user_id) if x.get("id") == kid), None)
        if not raw:
            return json.dumps(
                {"error": "知识条目不存在", "id": kid}, ensure_ascii=False
            )
        if not raw.get("enabled"):
            return json.dumps(
                {"error": "知识条目已禁用", "id": kid}, ensure_ascii=False
            )
        return json.dumps(
            {
                "id": raw.get("id"),
                "title": raw.get("title"),
                "mode": raw.get("mode"),
                "body": raw.get("body") or "",
            },
            ensure_ascii=False,
        )
```

将 `load_knowledge` 加入 `return [` 列表末尾。

- [ ] **Step 3: 单测工具注册**

追加到 `tests/test_knowledge.py`：

```python
def test_load_knowledge_tool_registered():
    from app.advisor.agent.tools import build_tools

    tools = {t.name: t for t in build_tools("u")}
    assert "load_knowledge" in tools
```

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge.py -v
```

Expected: 全部 PASS；且：

```bash
.venv/bin/python -c "from app.advisor.agent.graph import build_system_prompt; assert '投研助手' in build_system_prompt('nobody'); print('ok')"
```

（无 Mongo 知识时返回基础 prompt；若 `get_db` 连不上则用 patch `build_knowledge_prompt_section` 返回 `""` 测。）

更稳妥的检查：

```bash
.venv/bin/python -c "
from unittest.mock import patch
from app.advisor.agent.graph import build_system_prompt, SYSTEM_PROMPT
with patch('app.advisor.knowledge.build_knowledge_prompt_section', return_value=''):
    assert build_system_prompt('u') == SYSTEM_PROMPT or build_system_prompt('u').startswith(SYSTEM_PROMPT[:20])
print('ok')
"
```

- [ ] **Step 4: Commit（仅当用户要求）**

---

### Task 4: 设置页 UI + agentApi

**Files:**
- Modify: `frontend-advisor/src/agentApi.ts`
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.tsx`
- Modify: `frontend-advisor/src/styles.css`（仅当缺少列表/表单样式时追加 `.knowledge-*`）

**Interfaces:**
- Consumes: `/api/advisor/knowledge` CRUD
- Produces: 设置页「知识库」区块

- [ ] **Step 1: `agentApi.ts` 增加类型与函数**

```typescript
export type KnowledgeMode = 'always' | 'on_demand'

export type KnowledgeItem = {
  id: string
  title: string
  mode: KnowledgeMode
  enabled: boolean
  description: string
  body: string
  created_at?: string | null
  updated_at?: string | null
}

export type KnowledgeInput = {
  title: string
  mode: KnowledgeMode
  enabled: boolean
  description: string
  body: string
}

export function listKnowledge(): Promise<{ items: KnowledgeItem[] }> {
  return authFetch('/api/advisor/knowledge')
}

export function createKnowledge(body: KnowledgeInput): Promise<KnowledgeItem> {
  return authFetch('/api/advisor/knowledge', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateKnowledge(
  id: string,
  body: KnowledgeInput,
): Promise<KnowledgeItem> {
  return authFetch(`/api/advisor/knowledge/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteKnowledge(id: string): Promise<{ ok: boolean }> {
  return authFetch(`/api/advisor/knowledge/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}
```

（`authFetch` 已存在则复用；若现有 save 用不同 helper，与 `saveLlmSettings` 保持同一风格。）

- [ ] **Step 2: 扩展 `AgentSettingsPage.tsx`**

在 DeepSeek 配置区块下方增加「知识库」区块，状态与行为：

- `items` / `kbLoading` / `kbError` / `editing`（null | 新建空表 | 编辑中的 item）
- mount 时 `listKnowledge()`
- 列表行：title、mode 徽章（必选/可选）、enabled checkbox（切换后 `updateKnowledge`）、按钮：查看、编辑、删除
- 查看：只读展示 description + body（可用 `<pre>` 或简单 div）
- 编辑/新建表单字段：title、mode select、enabled、description（mode=on_demand 时 required）、body textarea
- 保存调用 create/update；展示后端 `detail` 错误（字数上限）
- 删除 `confirm` 后 `deleteKnowledge` 并刷新列表
- 页面 hero 可改为「Agent 设置」或保留 DeepSeek 标题并在知识库用 `<h2>知识库</h2>`

保持与现有 `strategy-field` / `btn` / `input` class 一致，避免大改视觉。

- [ ] **Step 3: 手工验收清单**

1. 设置页新建必选 → 保存成功 → 列表可见  
2. 新建可选（无 description）→ 400 提示  
3. 必选超大正文合计超限 → 提示 6000  
4. 禁用后重新开聊，prompt 不应再含该正文（可在后端临时 log `build_system_prompt` 或问 agent「你的必选知识有哪些」）  
5. 可选：问相关问题 → 出现 `load_knowledge` 工具调用  

- [ ] **Step 4: Commit（仅当用户要求）**

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| 数据模型 + 上限校验 | Task 1 |
| REST CRUD | Task 2 |
| 动态 system prompt | Task 3 |
| `load_knowledge` | Task 3 |
| 设置页 UI | Task 4 |
| 用户隔离 | Task 1–2（一律 `user_id` 过滤） |
| 不做向量/独立页/上传 | 全局约束 |

## Self-review

- 无 TBD；函数名 `build_knowledge_prompt_section` / `format_knowledge_prompt_section` / `build_system_prompt` 在任务间一致
- `load_knowledge` 用 `list_raw` + enabled 检查，与 spec「禁用不可 load」一致
