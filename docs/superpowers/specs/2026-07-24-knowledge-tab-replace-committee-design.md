# 知识库 Tab 替代投委会入口设计

## 目标

前端 Agent 导航暂时隐藏「投委会」入口，用独立「知识库」Tab 替代其导航位。知识库从 DeepSeek 配置页拆出为独立页面；投委会功能与直链路由保留，便于内部调试。

## 非目标

- 不改动投委会业务逻辑、API 或页面组件行为。
- 不新增或修改知识库后端 API。
- 不删除 `CommitteePage` 及相关代码。
- 不改变 DeepSeek 配置的保存/校验语义。

## 决策摘要

| 项 | 选择 |
|----|------|
| 知识库呈现 | 独立页面 `/agent/knowledge` |
| 投委会 | 仅隐藏导航；`/agent/committee` 直链仍可打开 |
| 设置页 | 仅保留 DeepSeek 配置，移除知识库区块 |

## 导航与路由

Agent 模式下 `nav` 变为：

1. 投研助手 → `/agent`
2. **知识库** → `/agent/knowledge`（替代原「投委会」）
3. 策略副驾 → `/agent/strategy`
4. DeepSeek 配置 → `/agent/settings`

路由变更：

- 新增：`/agent/knowledge` → `KnowledgePage`（新页面）。
- 保留：`/agent/committee` → `CommitteePage`（无 NavLink）。
- 不变：`/agent/settings` → `AgentSettingsPage`（内容收窄）。

`isAgentChat` 仍仅对 `/agent` 与 `/agent/committee` 启用 chat shell；知识库页使用普通 Agent shell（含 footer）。

## 页面拆分

### KnowledgePage（新建）

从 `AgentSettingsPage` 迁出知识库相关状态与 UI：

- 列表、新建/编辑表单、查看面板、启用开关、删除。
- 继续调用现有 `agentApi`：`listKnowledge` / `createKnowledge` / `updateKnowledge` / `deleteKnowledge`。
- 页面 hero：标题「知识库」，说明必选/可选注入规则与字数限制（文案可沿用设置页现有 hint）。
- 样式复用现有 `knowledge-*` class，不引入新设计体系。

### AgentSettingsPage（收窄）

- 删除知识库 state、handlers、区块与相关 import。
- Hero 文案改为仅描述 DeepSeek API Key 配置。
- 保留 DeepSeek 保存/清除/返回助手链接。

## 测试

更新 `App.test.tsx`：

- Agent 导航包含「知识库」链接指向 `/agent/knowledge`，不包含「投委会」链接。
- 直链 `/agent/committee` 仍渲染投委会工作台，且仍带 `app-shell--agent-chat`。
- 可选：直链 `/agent/knowledge` 渲染知识库页标题。

若有设置页单测覆盖知识库 UI，改为指向 `KnowledgePage` 或删除过时断言。

## 实现范围（文件）

- `frontend-advisor/src/App.tsx` — 导航与路由
- `frontend-advisor/src/App.test.tsx` — 导航/路由断言
- `frontend-advisor/src/pages/KnowledgePage.tsx` — 新建
- `frontend-advisor/src/pages/AgentSettingsPage.tsx` — 移除知识库区块

后端与 `committee/` 目录本变更不触碰。

## 验收标准

1. Agent 顶栏可见「知识库」，不可见「投委会」。
2. 点击「知识库」可完成既有 CRUD（与原设置页行为一致）。
3. DeepSeek 配置页不再展示知识库区块。
4. 手动访问 `/agent/committee` 仍可打开投委会工作台。
