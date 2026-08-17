# 模型配置页布局与模型搜索设计

## 目标

把 Agent「模型配置」页改成可读的设置页：三家提供方用 Tab 一次只看一家；上百个模型用搜索 + 限高勾选列表；八个功能槽位用三列表格。保存语义与后端 API 不变。

## 已确认决策

| 项 | 决策 |
|----|------|
| 提供方结构 | Tab：DeepSeek / Kimi / 千问，一次只渲染当前家 |
| 模型清单 | 搜索 + 可滚动勾选；已勾选置顶；显示「已选 n / 共 m」 |
| 功能模块 | 表格三列：模块 \| 提供方 \| 模型；窄屏上下叠 |
| 实现路径 | 复用现有 `board-tabs`、`home-tile`、`data-table`，不引入组件库 |
| 后端 | 不改 `llm_settings` / REST / 勾选保存语义 |
| 非目标 | 自定义皮肤、左右双栏、标签式添加、虚拟滚动、后端搜索 |

## 页面结构（自上而下）

1. `page-hero`：一句说明（Key 服务端加密；模块可独立选提供方和模型）。
2. 全局 `status`：加载 / 错误 / 成功，仍挂在页顶（不按 Tab 拆）。
3. **模型提供方**
   - `role="tablist"`，三个 `role="tab"`，文案 `DeepSeek` / `Kimi` / `千问`。
   - 未配置的 Tab 在名称后加「未配置」（例如 `Kimi 未配置`），`aria-label` 用全称。
   - 当前 Tab 对应一张 `home-tile` 卡片（`role="tabpanel"`）。
   - 卡片内：文档链接、已配置 hint 或未配置、API Key 输入、保存并校验、已配置则显示清除 Key 与刷新模型。
   - 仅当前已配置提供方展示模型区。
4. **功能模块**：`table-wrap` + `data-table`。未配置任何提供方时不渲染表格，只显示「请先配置至少一个模型提供方」。
5. **联网搜索**：一张 `home-tile`，逻辑与现网相同（综述在主 Agent 非 DeepSeek 时 `disabled` + 旁注；Tavily 开关与 Key）。
6. 底部 `form-actions`：保存模块/联网、清除 Tavily（若已配）、返回助手。

默认选中第一个提供方 Tab（DeepSeek）。切换 Tab 只改本地 UI 状态，不发请求。

## 模型搜索与勾选

数据源仍是该提供方 `available_models`（空则回退已勾选 id）。过滤与排序只在前端。

- 搜索框 `placeholder`：`搜索模型`；`aria-label`：`搜索{提供方名}模型`（如 `搜索 DeepSeek 模型`）。
- 过滤：模型 id **大小写不敏感子串**匹配；空查询显示全部。
- 排序：先已勾选（保持 `enabled_models` 相对顺序），再未勾选（保持 `available_models` 相对顺序）。
- 列表容器限高约 `16rem`，内部滚动；不虚拟滚动。
- 计数：`已选 {n} / 共 {m}`，`m` 为 available（或回退）总数，不受搜索过滤影响；无匹配时列表区显示「无匹配模型」。
- 取消最后一个勾选：忽略操作并提示「至少勾选一个模型」（现有行为）。
- 勾选变化仍只进本地 state，与模块/联网一起点「保存」才 `saveLlmSettings({ enabled_models, slots, ... })`。提供方 Key 仍走独立 `saveLlmProvider`。

## 功能模块表格

列头：`模块`、`提供方`、`模型`。八行顺序不变：

主 Agent 对话、模拟盘、首页解读、定时任务、政策雷达、打板晋级、委员会·快速、委员会·深度。

- 提供方 `<select>` `aria-label` 仍为 `{模块} 提供方`，选项仅为已配置提供方。
- 模型 `<select>` `aria-label` 仍为 `{模块} 模型`，选项为该提供方当前勾选列表。
- 切换提供方：该行模型改为该提供方 `default_model`（若已勾选），否则勾选列表第一项（现有 `changeSlotProvider`）。
- 视口 `max-width: 639px`：表格行改为「模块名 + 两个全宽下拉」上下叠，不横向滚动。

## 样式约束

- 新增类名加 `llm-settings-` 前缀，挂在 `styles.css`；不改 `board-tabs` / `data-table` 全局观感。
- 去掉页面上的 `style={{ maxWidth: '36rem' }}` 一类内联限宽。
- 颜色/圆角/边框只用现有 CSS 变量（`--line`、`--bg1`、`--muted`、`--ink`、`--color-brand-soft`）。

## 测试

文件：`frontend-advisor/src/pages/AgentSettingsPage.test.tsx`。保留现有 Tavily / 切换提供方默认模型 / 综述禁用用例。新增：

- Tab：能看到三个 Tab；点「千问」后 Key 区是千问而不是三家同时铺开。
- 搜索：fixture 给 DeepSeek 十余个模型；输入 `pro` 后只剩 id 含 `pro` 的项；已勾选且匹配的排在未勾选之前。
- 槽位：存在列头「模块」「提供方」「模型」；`主 Agent 对话 提供方` 仍可按 label 取到。

不测真实外网、不测虚拟滚动。

## 范围外

不改后端、不改槽位/提供方常量、不改缺 Key 文案、不引入 npm 依赖。
