# Agent 知识库沉淀 CRUD 工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为投研助手增加 `list_knowledge` / `save_knowledge` / `delete_knowledge` 工具，支持对话沉淀与确认后 CRUD。

**Architecture:** 在 `knowledge.py` 抽出标题模糊匹配与摘要序列化；在 `tools.py` 按现有 `_need_confirm` 模式实现三工具并注册；`SYSTEM_PROMPT` 增加写知识库确认指引。前端不改。

**Tech Stack:** FastAPI / PyMongo / LangChain `@tool` / pytest

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-25-agent-knowledge-crud-tools-design.md`
- 写操作默认 `confirm=false`；仅 `confirm=true` 落库
- 复用现有上限：body ≤ 8000；title ≤ 80；description ≤ 200；启用 always 合计 ≤ 6000；启用 on_demand ≤ 50
- 标题匹配：`query.strip().lower() in title.lower()`；结果按 `updated_at` 降序
- 工具 JSON 返回；校验错误不抛崩进程
- 前端本版不改
- Commit 仅在用户明确要求时执行；计划中的 commit 步骤默认跳过

---

### File map

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/knowledge.py` | `match_by_title`、`summarize_item` |
| `backend/app/advisor/agent/tools.py` | 三工具 + 注册 |
| `backend/app/advisor/agent/graph.py` | SYSTEM_PROMPT 指引 |
| `backend/tests/test_knowledge.py` | 匹配纯逻辑单测 |
| `backend/tests/test_knowledge_tools.py` | 工具 confirm / CRUD / 匹配 |

---

### Task 1: knowledge 标题匹配与摘要

**Files:**
- Modify: `backend/app/advisor/knowledge.py`
- Modify: `backend/tests/test_knowledge.py`

**Interfaces:**
- Produces:
  - `summarize_item(doc: dict, *, include_body: bool = False) -> dict`
  - `match_by_title(items: list[dict], query: str) -> list[dict]`  # 纯函数，便于单测
  - `find_by_title(user_id: str, query: str) -> list[dict]`  # 读库后 match

- [ ] **Step 1: 写失败单测**

```python
# 追加到 backend/tests/test_knowledge.py

def test_match_by_title_substring_case_insensitive():
    items = [
        {"id": "1", "title": "交易纪律", "updated_at": "2026-01-02"},
        {"id": "2", "title": "茅台笔记", "updated_at": "2026-01-03"},
        {"id": "3", "title": "纪律补充", "updated_at": "2026-01-04"},
    ]
    hits = kn.match_by_title(items, "纪律")
    assert [x["id"] for x in hits] == ["3", "1"]  # updated_at 降序

def test_match_by_title_empty_query_returns_empty():
    assert kn.match_by_title([{"id": "1", "title": "A"}], "  ") == []

def test_summarize_item_omits_body_by_default():
    doc = {
        "id": "1",
        "title": "t",
        "mode": "always",
        "enabled": True,
        "description": "d",
        "body": "SECRET",
    }
    out = kn.summarize_item(doc)
    assert out["id"] == "1"
    assert "body" not in out
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge.py::test_match_by_title_substring_case_insensitive tests/test_knowledge.py::test_summarize_item_omits_body_by_default -v`

Expected: FAIL（缺函数）

- [ ] **Step 3: 实现**

在 `knowledge.py` 追加：

```python
def summarize_item(doc: dict[str, Any], *, include_body: bool = False) -> dict[str, Any]:
    return public_item(doc, include_body=include_body)


def match_by_title(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []
    hits = [
        x for x in items if q in str(x.get("title") or "").lower()
    ]

    def _sort_key(doc: dict[str, Any]):
        v = doc.get("updated_at")
        return v or ""

    return sorted(hits, key=_sort_key, reverse=True)


def find_by_title(user_id: str, query: str) -> list[dict[str, Any]]:
    return match_by_title(list_raw(user_id), query)
```

- [ ] **Step 4: 跑测确认通过**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 2: list / save / delete 工具 + 单测

**Files:**
- Modify: `backend/app/advisor/agent/tools.py`
- Create: `backend/tests/test_knowledge_tools.py`

**Interfaces:**
- Consumes: `list_raw`, `summarize_item`, `find_by_title`, `create_item`, `update_item`, `delete_item`, `validate_payload` / CRUD
- Produces: tools named `list_knowledge`, `save_knowledge`, `delete_knowledge` registered in `build_tools`

- [ ] **Step 1: 写失败单测（mock db / knowledge）**

```python
# backend/tests/test_knowledge_tools.py
import json
from unittest.mock import patch

from app.advisor.agent.tools import build_tools


def _tool_map(user_id: str = "u1"):
    return {t.name: t for t in build_tools(user_id)}


def test_list_knowledge_registered():
    assert "list_knowledge" in _tool_map()
    assert "save_knowledge" in _tool_map()
    assert "delete_knowledge" in _tool_map()


def test_save_knowledge_preview_does_not_persist():
    tools = _tool_map()
    with (
        patch("app.advisor.knowledge.list_raw", return_value=[]),
        patch("app.advisor.knowledge.create_item") as create,
    ):
        raw = tools["save_knowledge"].invoke(
            {
                "title": "纪律",
                "mode": "always",
                "body": "不加杠杆",
                "description": "",
                "confirm": False,
            }
        )
    data = json.loads(raw)
    assert data["needs_confirm"] is True
    assert data.get("ok") is False or data.get("applied") is False
    create.assert_not_called()


def test_save_knowledge_confirm_creates():
    tools = _tool_map()
    fake = {
        "id": "k1",
        "title": "纪律",
        "mode": "always",
        "enabled": True,
        "description": "",
        "body": "不加杠杆",
    }
    with (
        patch("app.advisor.knowledge.list_raw", return_value=[]),
        patch("app.advisor.knowledge.create_item", return_value=fake) as create,
    ):
        raw = tools["save_knowledge"].invoke(
            {
                "title": "纪律",
                "mode": "always",
                "body": "不加杠杆",
                "description": "",
                "confirm": True,
            }
        )
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["item"]["id"] == "k1"
    create.assert_called_once()


def test_delete_knowledge_title_ambiguous():
    tools = _tool_map()
    items = [
        {"id": "a", "title": "纪律A", "mode": "always", "enabled": True, "description": "", "updated_at": "2"},
        {"id": "b", "title": "纪律B", "mode": "on_demand", "enabled": True, "description": "d", "updated_at": "1"},
    ]
    with patch("app.advisor.knowledge.list_raw", return_value=items):
        raw = tools["delete_knowledge"].invoke(
            {"title": "纪律", "confirm": False}
        )
    data = json.loads(raw)
    assert data["ok"] is False
    assert len(data["candidates"]) == 2
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge_tools.py -v`

Expected: FAIL（工具未注册）

- [ ] **Step 3: 实现工具**

在 `tools.py` 的 `load_knowledge` 旁新增（保持与 `_need_confirm` 风格一致；知识库可用 `ok`/`needs_confirm` 字段，与 spec 对齐）：

```python
    @tool
    def list_knowledge(query: str = "") -> str:
        """列出或按标题模糊查找当前用户知识库条目摘要（不含正文）。
        query 为空返回全部；非空则 title 子串匹配（不区分大小写）。"""
        from ..knowledge import find_by_title, list_raw, summarize_item

        _bind()
        q = (query or "").strip()
        if not q:
            items = [summarize_item(x) for x in list_raw(user_id)]
            return json.dumps({"ok": True, "items": items}, ensure_ascii=False, default=str)
        hits = find_by_title(user_id, q)
        if not hits:
            return json.dumps(
                {"ok": False, "error": "无匹配", "query": q},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "ok": True,
                "query": q,
                "items": [summarize_item(x) for x in hits],
            },
            ensure_ascii=False,
            default=str,
        )

    @tool
    def save_knowledge(
        title: str,
        mode: str,
        body: str,
        description: str = "",
        knowledge_id: str = "",
        enabled: bool = True,
        confirm: bool = False,
    ) -> str:
        """新建或更新知识库条目。必须 confirm=true 才落库。
        无 knowledge_id 为新建；有则为更新。更新也可只给 title（唯一匹配）。
        mode=always|on_demand；on_demand 必须有 description。"""
        from ..knowledge import (
            create_item,
            find_by_title,
            get_item,
            list_raw,
            summarize_item,
            update_item,
            validate_payload,
        )

        _bind()
        kid = (knowledge_id or "").strip()
        payload = {
            "title": title,
            "mode": mode,
            "body": body,
            "description": description,
            "enabled": enabled,
        }
        action = "update" if kid else "create"
        target_id = kid

        if not kid:
            # 若调用方意图更新但只传了已有标题：不在 create 路径猜；create 总是新建
            pass
        else:
            if not get_item(user_id, kid):
                return json.dumps(
                    {"ok": False, "error": "知识条目不存在", "id": kid},
                    ensure_ascii=False,
                )

        try:
            existing = list_raw(user_id)
            validate_payload(
                payload,
                existing_enabled=existing,
                exclude_id=target_id or None,
            )
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

        preview = {
            "action": action,
            "id": target_id or None,
            "title": title,
            "mode": mode,
            "enabled": enabled,
            "description": description,
            "body": body,
            "body_chars": len(body or ""),
        }
        if not confirm:
            return json.dumps(
                {
                    "ok": False,
                    "needs_confirm": True,
                    "preview": preview,
                    "message": "未确认。请向用户展示预览，同意后再以 confirm=true 调用。",
                },
                ensure_ascii=False,
            )
        try:
            if action == "create":
                item = create_item(user_id, payload)
            else:
                item = update_item(user_id, target_id, payload)
        except (ValueError, KeyError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "item": item}, ensure_ascii=False, default=str)

    @tool
    def delete_knowledge(
        knowledge_id: str = "",
        title: str = "",
        confirm: bool = False,
    ) -> str:
        """删除知识库条目。必须 confirm=true 才删除。
        优先 knowledge_id；否则按 title 模糊匹配，多条返回 candidates。"""
        from ..knowledge import delete_item, find_by_title, get_item, summarize_item

        _bind()
        kid = (knowledge_id or "").strip()
        t = (title or "").strip()
        target = None
        if kid:
            target = get_item(user_id, kid)
            if not target:
                return json.dumps(
                    {"ok": False, "error": "知识条目不存在", "id": kid},
                    ensure_ascii=False,
                )
        elif t:
            # get_item 返回 public；find_by_title 返回 raw — 统一 summarize
            from ..knowledge import list_raw, match_by_title

            hits = match_by_title(list_raw(user_id), t)
            if not hits:
                return json.dumps(
                    {"ok": False, "error": "无匹配", "query": t},
                    ensure_ascii=False,
                )
            if len(hits) > 1:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "匹配多条，请指定 knowledge_id",
                        "candidates": [summarize_item(x) for x in hits],
                    },
                    ensure_ascii=False,
                    default=str,
                )
            target = summarize_item(hits[0], include_body=True)
        else:
            return json.dumps(
                {"ok": False, "error": "需要 knowledge_id 或 title"},
                ensure_ascii=False,
            )

        preview = {
            "action": "delete",
            "id": target.get("id"),
            "title": target.get("title"),
            "mode": target.get("mode"),
        }
        if not confirm:
            return json.dumps(
                {
                    "ok": False,
                    "needs_confirm": True,
                    "preview": preview,
                    "message": "未确认。请向用户展示将删除的条目，同意后再以 confirm=true 调用。",
                },
                ensure_ascii=False,
            )
        ok = delete_item(user_id, str(target.get("id")))
        if not ok:
            return json.dumps(
                {"ok": False, "error": "删除失败", "id": target.get("id")},
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": True, "deleted_id": target.get("id")},
            ensure_ascii=False,
        )
```

**重要：`save_knowledge` 按标题更新**

Spec：更新时若只给 title 未给 knowledge_id，先匹配。在 `save_knowledge` 中，增加可选约定：

- 若 `knowledge_id` 为空且参数中需要更新语义：用额外参数不增加复杂度——改为：当 `knowledge_id` 为空时始终 **create**；更新必须传 `knowledge_id`，或传 `title` **并** 让 Agent 先 `list_knowledge` 拿到 id。  

为符合 spec「更新时若只给 title」：在 `save_knowledge` 增加逻辑——若调用方传入 `knowledge_id=""` 且希望更新，可用 `mode`/`body` 正常 create。更干净的做法：

增加参数 `update_if_title_matches: bool = False` **不要**（YAGNI）。

按 spec 实现：当 `knowledge_id` 为空时 **create**；当需要按标题更新时，Agent 先 list 再带 id。同时在 `save_knowledge` docstring 写清。

若要坚持 spec 字面「只给 title 更新」：当 `knowledge_id` 空且 `find_by_title(title)` 唯一命中且 body/mode 与「覆盖写」——易与新建同名冲突。

**本计划定稿：**  
- `knowledge_id` 空 → 始终新建  
- 更新必须 `knowledge_id`  
- 按标题定位用 `list_knowledge` / `delete_knowledge(title=…)`  
- 单测覆盖 create 预览、create 确认、delete 多候选  

把工具加入 `return [..., load_knowledge, list_knowledge, save_knowledge, delete_knowledge, build_delegate_data_tool(...)]`（delegate 仍最后）。

- [ ] **Step 4: 跑测**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge_tools.py tests/test_knowledge.py -v`

Expected: PASS

补充单测（同文件）：

```python
def test_list_knowledge_filters_by_query():
    tools = _tool_map()
    items = [
        {"id": "1", "title": "交易纪律", "mode": "always", "enabled": True, "description": "", "body": "x", "updated_at": "2"},
        {"id": "2", "title": "茅台", "mode": "on_demand", "enabled": True, "description": "d", "body": "y", "updated_at": "1"},
    ]
    with patch("app.advisor.knowledge.list_raw", return_value=items):
        raw = tools["list_knowledge"].invoke({"query": "纪律"})
    data = json.loads(raw)
    assert data["ok"] is True
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "1"
    assert "body" not in data["items"][0]
```

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 3: SYSTEM_PROMPT 指引

**Files:**
- Modify: `backend/app/advisor/agent/graph.py`
- Modify: `backend/tests/test_knowledge_tools.py` 或 `test_data_agent_delegate.py` 增加断言

**Interfaces:**
- Produces: SYSTEM_PROMPT 含沉淀/确认/`list_knowledge`/`save_knowledge`/`delete_knowledge` 指引

- [ ] **Step 1: 写失败断言**

```python
def test_system_prompt_mentions_knowledge_write_confirm():
    from app.advisor.agent.graph import SYSTEM_PROMPT

    assert "save_knowledge" in SYSTEM_PROMPT
    assert "confirm=true" in SYSTEM_PROMPT
    assert "list_knowledge" in SYSTEM_PROMPT
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge_tools.py::test_system_prompt_mentions_knowledge_write_confirm -v`

Expected: FAIL

- [ ] **Step 3: 更新 SYSTEM_PROMPT**

在规则 12 之后插入新规则（后续编号顺延），例如：

```text
13. 知识库写入/更新/删除：先整理内容或用 list_knowledge 定位 → 调用 save_knowledge / delete_knowledge 且 confirm=false 展示预览 → 用户明确同意后再 confirm=true。未指定可选/必选时先询问。匹配多条时列出候选，勿猜测。未确认不得声称已保存。
```

原 13–15 改为 14–16。并确保仍包含 `delegate_data_task` 等既有关键词（`test_data_agent_delegate` 依赖）。

- [ ] **Step 4: 跑相关测试**

Run:

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge_tools.py tests/test_knowledge.py tests/test_data_agent_delegate.py::test_main_agent_registers_delegate_last_and_preserves_specialized_rules -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

---

### Task 4: 冒烟

- [ ] **Step 1: 工具注册冒烟**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python - <<'PY'
from app.advisor.agent.tools import build_tools
from app.advisor.agent.graph import SYSTEM_PROMPT
names = {t.name for t in build_tools("u")}
assert {"list_knowledge", "save_knowledge", "delete_knowledge", "load_knowledge"} <= names
assert "save_knowledge" in SYSTEM_PROMPT
print("ok", sorted(names & {"list_knowledge", "save_knowledge", "delete_knowledge"}))
PY
```

Expected: 打印 `ok ...`

- [ ] **Step 2: 全量子集**

```bash
cd /Users/orange/Desktop/code/share-data/backend && .venv/bin/python -m pytest tests/test_knowledge.py tests/test_knowledge_tools.py tests/test_data_agent_delegate.py -v
```

Expected: PASS

- [ ] **Step 3: 手工验收清单**

1. 对话：「把刚才总结成可选知识」→ 见预览 → 确认 → Agent 配置页可见  
2. 必选超预算 → 报错不落库  
3. 删标题模糊多条 → 列出候选  
4. 未确认时库中无新条目  

---

## Spec coverage

| Spec 要求 | Task |
|-----------|------|
| list_knowledge + 模糊匹配 | 1, 2 |
| save 预览/确认 | 2 |
| delete 预览/确认 + 多候选 | 2 |
| SYSTEM_PROMPT 指引 | 3 |
| 上限复用 | 2（validate_payload） |
| 验收 | 4 |

## 与 spec 的一处明确收窄

- **按标题更新**：实现上更新必须传 `knowledge_id`（Agent 先 `list_knowledge`）。避免「同名新建 vs 更新」歧义。删除仍支持 `title` 唯一匹配。  
- 若产品坚持「save 仅 title 更新」，在实现 Task 2 前改回：`knowledge_id` 空时若 `find_by_title(title)` 唯一则走 update，0 条 create，多条返回 candidates。
