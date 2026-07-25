# Agent 知识库沉淀（CRUD 工具）

日期：2026-07-25  
状态：已确认设计

## 问题

知识库目前仅支持前端 CRUD 与 Agent 只读 `load_knowledge`。用户无法在对话中让 Agent 总结聊天内容并沉淀为可选/必选知识，也无法通过对话更新或删除条目。

## 目标

1. Agent 可按用户意图将对话要点沉淀到知识库（可选 / 必选）。
2. 支持完整 CRUD：列表/查找、新建、更新、删除。
3. 写操作必须先预览、用户明确确认后再落库（与持仓/策略一致）。
4. 查找支持 `list_knowledge` + 按标题模糊匹配；多条时列出候选供用户选择。

## 非目标

- 自动改写用户系统提示词
- 向量检索 / embedding
- 跨用户共享
- 文件上传导入
- 前端 UI 新增「一键沉淀」按钮（本版仅 Agent 工具）

## 方案概要

在 `tools.py` 新增三个工具，复用 `knowledge.py` 校验与 CRUD：

| 工具 | 职责 |
|------|------|
| `list_knowledge` | 列表或按标题模糊匹配 |
| `save_knowledge` | 新建或更新；`confirm` 门闩 |
| `delete_knowledge` | 删除；`confirm` 门闩 |

保留现有 `load_knowledge`。在 `SYSTEM_PROMPT` 增加写知识库的确认流程指引。

## 工具接口

### `list_knowledge(query: str = "") -> str`（JSON）

- `query` 为空：返回当前用户全部条目摘要（含禁用）  
  字段：`id, title, mode, enabled, description`（不含 body）
- `query` 非空：对 `title` 做不区分大小写的子串匹配  
  - 0 条：`{ ok:false, error:"无匹配", query }`  
  - 1+ 条：`{ ok:true, items:[…], query }`（多条即候选列表）

### `save_knowledge(...) -> str`（JSON）

参数：

| 参数 | 说明 |
|------|------|
| `title` | 必填 |
| `mode` | `always` \| `on_demand` |
| `body` | 必填 |
| `description` | `on_demand` 必填；`always` 可空 |
| `knowledge_id` | 可选；有则更新，无则新建 |
| `enabled` | 默认 `true` |
| `confirm` | 默认 `false` |

行为：

1. 组装 payload，走现有 `validate_payload`（字数/预算/条数）。  
2. `confirm=false`：不落库，返回  
   `{ ok:false, needs_confirm:true, preview:{ action:"create"|"update", … } }`  
3. `confirm=true`：`create_item` / `update_item`，返回 `{ ok:true, item: public_item }`  
4. 校验或 KeyError：`{ ok:false, error:"…" }`

更新时若只给 `title` 未给 `knowledge_id`：先内部按标题匹配；0/多条返回 error + candidates，不写入。

### `delete_knowledge(knowledge_id: str = "", title: str = "", confirm: bool = false) -> str`

1. 解析目标：优先 `knowledge_id`；否则用 `title` 模糊匹配。  
2. 0/多条：error + candidates。  
3. `confirm=false`：返回预览（id/title/mode）。  
4. `confirm=true`：`delete_item`，返回 `{ ok:true, deleted_id }`。

### `load_knowledge`

不变；可加载已启用条目（含 always）。

## 标题匹配规则

- 规范化：`strip` + 小写比较  
- 匹配：`query.lower() in title.lower()`  
- 多条：按 `updated_at` 降序返回候选，Agent 必须展示并请用户指定 id 或更精确标题

## Agent 系统提示指引

在产品 `SYSTEM_PROMPT` 规则中增加（编号顺延）：

- 用户要求把内容写入/更新/删除知识库时：先整理或定位条目 → 调用对应工具且 `confirm=false` 展示预览 → 用户明确同意后再 `confirm=true`。  
- 未指定可选/必选时先询问。  
- 改删前用 `list_knowledge`（或标题匹配）定位；匹配多条时列出候选，勿猜测。  
- 工具与安全规则不因沉淀而放宽。

## 模块落点

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/knowledge.py` | 可选：抽出 `match_by_title(user_id, query)` 供工具复用 |
| `backend/app/advisor/agent/tools.py` | 注册三工具；确认门闩 |
| `backend/app/advisor/agent/graph.py` | SYSTEM_PROMPT 指引 |
| `backend/tests/test_knowledge_tools.py`（或扩展 `test_knowledge.py`） | 预览不落库、确认落库、模糊匹配、预算错误 |

前端本版不改。

## 验收

1. 「总结进可选知识库」→ 预览 → 确认后列表可见且 mode=on_demand  
2. 指定必选且合计将超 6000 → 失败提示，不落库  
3. 按标题更新/删除：唯一匹配可预览确认；多条先列候选  
4. `confirm=false` 永不落库  
5. 用户 A 无法操作用户 B 的条目  
6. 未确认时 Agent 不得声称已保存  

## 明确不在本版

系统提示词写入、向量检索、前端沉淀按钮、批量导入。
