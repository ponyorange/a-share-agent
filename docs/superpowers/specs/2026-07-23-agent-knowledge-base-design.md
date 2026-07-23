# 投研助手用户知识库

日期：2026-07-23  
状态：已确认设计

## 问题

投研助手的 `SYSTEM_PROMPT` 全局固定，用户无法注入个人交易纪律、研究笔记等长期知识。聊天仅有滑动窗口，不是可管理的知识库。

## 目标

每位用户可配置自己的知识条目；支持启用/禁用/删除/修改/查看。知识分两类：

1. **必选（always）**：启用后，正文完整拼入该用户当次对话的系统提示词。
2. **可选（on_demand）**：启用后，仅将标题+描述放入系统提示词中的「知识目录」；agent 按需调用工具加载正文（类似 skill）。

## 非目标

- 向量检索 / embedding RAG（远期可扩展，本版不做）
- 文件上传、PDF/网页抓取
- 跨用户共享、系统预置知识包
- 独立路由页（本版挂在设置页）

## 方案概要

复用现有 per-user Mongo + 登录 API 模式；在 `graph.py` 构建**动态 system prompt**；新增 `load_knowledge` 工具按需取正文；UI 放在 `AgentSettingsPage`。

## 数据模型

集合：`user_knowledge_items`（每条一文档）

| 字段 | 类型 | 说明 |
|------|------|------|
| `_id` / `id` | string (uuid) | 对外 id |
| `user_id` | string | 归属用户 |
| `title` | string | 标题，非空 |
| `mode` | `"always"` \| `"on_demand"` | 必选 / 可选 |
| `enabled` | bool | 禁用则不进提示词、不进目录、不可 load（或 load 返回 disabled） |
| `description` | string | `on_demand` 必填；`always` 可空 |
| `body` | string | 正文，非空 |
| `created_at` | datetime | |
| `updated_at` | datetime | |

索引：`(user_id, updated_at)`；查询一律带 `user_id`。

### 校验与上限

- 单条 `body` ≤ 8000 字（Unicode 字符数）
- 单条 `title` ≤ 80 字；`description` ≤ 200 字
- 所有 **enabled + always** 的 `body` 合计 ≤ 6000 字；超限拒绝保存并返回明确错误
- 所有 **enabled + on_demand** 条数 ≤ 50；超限拒绝将新条目标为启用可选，或拒绝保存

## API

均需 `Depends(get_current_user)`，只操作当前用户数据。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/advisor/knowledge` | 列表；默认返回含 body（设置页编辑需要）；可加 `?summary=1` 仅 id/title/mode/enabled/description |
| POST | `/api/advisor/knowledge` | 新建 |
| GET | `/api/advisor/knowledge/{id}` | 单条详情 |
| PUT | `/api/advisor/knowledge/{id}` | 全量更新（title/mode/enabled/description/body） |
| DELETE | `/api/advisor/knowledge/{id}` | 删除 |

启用/禁用通过 PUT 的 `enabled` 字段完成，不单独拆 PATCH（保持简单）。

错误：404（非本人或不存在）、400（校验失败，含字数上限说明）。

## Agent 运行时

### 动态 System Prompt

在 `graph.py` 每次聊天：

1. 加载该用户全部 `enabled` 条目
2. 拼接：

```
{SYSTEM_PROMPT}

## 用户必选知识
（按 updated_at 或 title 稳定排序，逐条输出 title + body）

## 用户可选知识目录
（on_demand：`- id: … | title: … | desc: …`）
需要细则时调用 load_knowledge(id)；勿编造目录外知识。
```

若某类为空则省略对应小节。

### 工具

- `load_knowledge(knowledge_id: str) -> str`  
  - 仅当前用户、且 `enabled` 的条目可加载  
  - 返回 JSON：`{id, title, mode, body}` 或 error  
  - `always` 条目也可 load（冗余但无害）；prompt 可提示「必选已在系统提示中」

- （可选，本版可不做）`list_knowledge`：目录已在 system prompt 时不必再暴露，减少工具噪声。

注册进 `build_tools`；SYSTEM_PROMPT 全局段增加一句指引：有用户知识目录时按需 `load_knowledge`。

## 前端

- 页面：`frontend-advisor/src/pages/AgentSettingsPage.tsx` 增加「知识库」区块  
- API：`agentApi.ts` 增 CRUD  
- 交互：列表（标题、类型徽章、启用开关、查看/编辑/删除）；弹层或内联表单新建/编辑；查看只读展示正文  
- 保存失败时展示后端字数/条数错误信息  

## 模块落点

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/knowledge.py` | CRUD + 校验 + 构建 prompt 片段 |
| `backend/app/advisor/routes.py` | HTTP 路由 |
| `backend/app/db.py` | 索引 |
| `backend/app/advisor/agent/graph.py` | 动态 prompt |
| `backend/app/advisor/agent/tools.py` | `load_knowledge` |
| `frontend-advisor/.../AgentSettingsPage.tsx` | UI |
| `frontend-advisor/.../agentApi.ts` | 客户端 |

## 验收

1. 用户 A 的知识对用户 B 不可见  
2. 启用必选后，新对话 system 侧含该正文；禁用后不再出现  
3. 启用可选后，目录出现 description；agent 可 `load_knowledge` 拿到正文  
4. 必选合计超 6000 字时保存失败并提示  
5. 删除后不可再 load  

## 明确不在本版

向量检索、独立 `/agent/knowledge` 路由、批量导入导出。
