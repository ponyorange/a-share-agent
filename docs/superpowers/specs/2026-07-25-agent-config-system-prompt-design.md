# Agent 配置：系统提示词 + 知识库注入调整

日期：2026-07-25  
状态：已确认设计

## 问题

当前「必选知识」拼进动态 system prompt，与产品工具/安全规则混在一起。用户无法单独配置角色称呼、性格与硬性纪律；必选知识也不适合承担「系统指令」职责。

## 目标

1. 原「知识库」升级为 **Agent 配置**（路由 `/agent/config`）。
2. Agent 配置下分两块：**系统提示词** + **知识库**。
3. 用户可自由配置系统提示词（硬性纪律/偏好、助手角色性格、名称等）；用户文案**追加**在产品 `SYSTEM_PROMPT` 之后，可覆盖默认称呼等表述。
4. 知识库仍分可选 / 必选：可选逻辑不变；必选改为每轮自动拼入**消息上下文**（不写聊天历史、不再进 system）。
5. 用户系统提示词长度上限 **6000 字**（Unicode 字符数）。

## 非目标

- 向量检索 / embedding RAG
- 文件上传、PDF/网页抓取
- 跨用户共享、系统预置知识包
- 允许用户整段替换或关闭产品工具/安全规则
- 把 DeepSeek API Key 配置并入本页（仍独立「DeepSeek 配置」）

## 方案概要

- **System** = 产品固定规则 +（可选）用户系统提示词  
- **Messages** = 运行时必选知识 context + 滑动窗口历史 + 当前用户消息  
- 可选知识：目录仍进 system；`load_knowledge` 不变  
- 前端：导航「Agent 配置」→ `/agent/config`；页内两区块

## Prompt 组装

每次对话：

```
[System]
{产品 SYSTEM_PROMPT}
{用户系统提示词}          # 可空；追加在后

## 用户可选知识目录      # 若有启用的 on_demand
...

[Messages]
{必选知识 context}        # 仅运行时注入，不持久化到 chat store
+ 历史滑动窗口
+ 当前用户消息
```

必选知识注入形式：带明确标题的独立消息（优先 `SystemMessage`，或等价带前缀的 context），文案结构沿用现有「## 用户必选知识」+ 各条 `### title` + body。勿伪装成用户原话。

产品 `SYSTEM_PROMPT` 文案调整：

- 删除「必选知识已在系统提示中」类表述  
- 改为：必选在消息上下文；可选目录存在时按需 `load_knowledge`

## 数据模型

### 用户系统提示词

Per-user 存储（独立集合或挂在现有用户 agent 配置文档均可，实现时选一处并保持单一真相源）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | string | 归属用户 |
| `system_prompt` | string | 可空；空则只用产品默认 |
| `updated_at` | datetime | |

校验：`len(system_prompt) ≤ 6000`（Unicode 字符）；超限拒绝保存。

### 知识库

集合与字段不变（`always` / `on_demand`、enabled、title/body/description 等）。上限不变：

- 单条 body ≤ 8000  
- 启用必选合计 ≤ 6000  
- 启用可选 ≤ 50 条  

**行为变化**：`format_knowledge_prompt_section`（或拆分后的等价函数）中，always 正文不再进入 system 片段；改为 `build_always_knowledge_messages(user_id)`（或同类）供 graph 在组装 `lc_messages` 时前置注入。可选目录仍进 system。

## API

均需登录，仅当前用户。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/advisor/agent-config/system-prompt` | 返回 `{ system_prompt, updated_at? }` |
| PUT | `/api/advisor/agent-config/system-prompt` | body: `{ system_prompt }`；校验长度 |
| 现有 | `/api/advisor/knowledge*` | CRUD 不变 |

错误：400（超 6000 字等校验失败）。

也可合并为单一 `GET/PUT /api/advisor/agent-config`，但本版最小面用上述专用端点即可。

## 前端

| 项 | 说明 |
|----|------|
| 路由 | `/agent/config`（替换 `/agent/knowledge`；旧路径可 redirect 到新路由） |
| 导航 | 「知识库」→「Agent 配置」；移动端 more menu 同步 |
| 页面 | 原 `KnowledgePage` 改造或重命名为 Agent 配置页 |
| 区块 1 | 系统提示词：textarea、字数 `当前/6000`、说明文案、保存 |
| 区块 2 | 知识库 CRUD；hint 改为必选注入消息 / 可选按需加载 |
| DeepSeek | `/agent/settings` 不动 |

说明文案要点：系统提示追加在产品规则之后，可覆盖称呼/性格/纪律；工具调用与写操作确认等产品规则始终保留。

## 模块落点（预期）

| 文件 | 职责 |
|------|------|
| `backend/app/advisor/` 新模块或扩展 | 用户 system_prompt 存取与校验 |
| `backend/app/advisor/knowledge.py` | 拆分 always vs on_demand 的 prompt/message 构建 |
| `backend/app/advisor/agent/graph.py` | `build_system_prompt` 追加用户文案；消息列表前置必选 context |
| `backend/app/advisor/routes.py` | system-prompt API |
| `frontend-advisor/src/pages/*` | Agent 配置页 |
| `frontend-advisor/src/App.tsx` 等 | 路由与导航 |
| `frontend-advisor/src/agentApi.ts` | 客户端 |

## 迁移

- 已有知识条目：无数据迁移；仅运行时注入位置变化  
- 用户系统提示词：默认空  
- `/agent/knowledge` → `/agent/config`（建议 前端 route redirect）

## 验收

1. 空系统提示 + 无必选：与产品默认 system 行为一致（除必选注入路径变更外）  
2. 配置称呼/性格后，助手按用户设定自称/语气；工具规则仍生效  
3. 系统提示 > 6000 字保存失败并提示  
4. 启用必选 → 新对话消息侧含正文；system 中无必选正文块  
5. 可选目录出现在 system；`load_knowledge` 可用  
6. 禁用/删除必选或清空系统提示后，下一轮不再出现对应内容  
7. 导航「Agent 配置」指向 `/agent/config`；直链可打开配置页  

## 明确不在本版

向量检索、DeepSeek 并入本页、用户关闭产品安全规则、结构化多字段「角色表单」（本版为自由文本）。
