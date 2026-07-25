# 页面标题精简 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 缩小桌面端“次日顾问”品牌标题，并移除已登录页面中与导航重复的一级标题。

**Architecture:** 保持现有页面结构，只删除业务页面 `.page-hero` 或同等页面头部中的 `<h1>`，说明文字和内部二级标题不变。通过桌面媒体查询覆盖 `.brand` 字号，移动端现有 `1.35rem` 规则不变。

**Tech Stack:** React 19、TypeScript、CSS、Vitest。

## Global Constraints

- 仅桌面端缩小顶部左侧“次日顾问”；移动端保持现状。
- 已登录业务页面不再显示与导航重复的一级标题。
- 登录页 `<h1>次日顾问</h1>` 必须保留。
- 页面内部二级标题和说明文字必须保留。
- 不新增依赖，不提交 git commit。

## File Structure

- `frontend-advisor/src/styles.css`：桌面端品牌字号覆盖。
- `frontend-advisor/src/pages/*.tsx`：删除基础面板和 Agent 页面一级标题。
- `frontend-advisor/src/committee/CommitteePage.tsx`：删除投委会页面一级标题。
- `frontend-advisor/tests/pageTitlePolicy.test.ts`：静态回归页面标题策略与桌面字号；置于 `src` 外避免进入浏览器端生产编译。
- `frontend-advisor/src/pages/KnowledgePage.test.tsx`：更新知识库页面行为断言。

---

### Task 1: 精简品牌和业务页面标题

**Files:**
- Create: `frontend-advisor/tests/pageTitlePolicy.test.ts`
- Modify: `frontend-advisor/src/styles.css`
- Modify: `frontend-advisor/src/pages/RecommendationsPage.tsx`
- Modify: `frontend-advisor/src/pages/AdvicePage.tsx`
- Modify: `frontend-advisor/src/pages/PortfolioPage.tsx`
- Modify: `frontend-advisor/src/pages/HistoryPage.tsx`
- Modify: `frontend-advisor/src/pages/PaperPage.tsx`
- Modify: `frontend-advisor/src/pages/LeaderboardPage.tsx`
- Modify: `frontend-advisor/src/pages/PerformancePage.tsx`
- Modify: `frontend-advisor/src/pages/StrategyPage.tsx`
- Modify: `frontend-advisor/src/pages/SettingsPage.tsx`
- Modify: `frontend-advisor/src/pages/KnowledgePage.tsx`
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.tsx`
- Modify: `frontend-advisor/src/pages/AgentStrategyPage.tsx`
- Modify: `frontend-advisor/src/committee/CommitteePage.tsx`
- Modify: `frontend-advisor/src/pages/KnowledgePage.test.tsx`

**Interfaces:**
- Consumes: existing `.brand`, `.page-hero`, desktop breakpoint `769px`, mobile breakpoint `768px`.
- Produces: business pages without `<h1>` and desktop `.brand { font-size: 1.5rem; }`.

- [ ] **Step 1: 写失败的标题策略测试**

在 `frontend-advisor/tests/` 创建 `pageTitlePolicy.test.ts`，读取上述业务页面源码并断言不含 `<h1>`；单独断言 `LoginPage.tsx` 仍含 `<h1>次日顾问</h1>`。读取 `styles.css` 并断言存在以下桌面规则：

```css
@media (min-width: 769px) {
  .brand {
    font-size: 1.5rem;
  }
}
```

同时将 `KnowledgePage.test.tsx` 的标题断言改为：

```tsx
expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
expect(screen.getByText(/必选知识会注入 Agent 系统提示/)).toBeInTheDocument()
```

- [ ] **Step 2: 运行并确认测试失败**

Run:

```bash
npm --prefix frontend-advisor exec -- vitest run --root frontend-advisor \
  tests/pageTitlePolicy.test.ts src/pages/KnowledgePage.test.tsx
```

Expected: FAIL；业务页面仍含 `<h1>`，且桌面 `.brand` 规则不存在。

- [ ] **Step 3: 实现最小改动**

从列出的已登录业务页面和 `CommitteePage.tsx` 删除页面级 `<h1>`，但不删除 `.page-hero`、说明文字、摘要、按钮或任何 `<h2>`。

在 `styles.css` 现有 `.brand` 与移动端规则附近加入：

```css
@media (min-width: 769px) {
  .brand {
    font-size: 1.5rem;
  }
}
```

不要修改 `LoginPage.tsx`。

- [ ] **Step 4: 运行聚焦测试**

Run:

```bash
npm --prefix frontend-advisor exec -- vitest run --root frontend-advisor \
  tests/pageTitlePolicy.test.ts src/pages/KnowledgePage.test.tsx
```

Expected: PASS。

- [ ] **Step 5: 运行完整前端验证**

Run:

```bash
npm --prefix frontend-advisor test -- --run
npm --prefix frontend-advisor run lint
npm --prefix frontend-advisor run build
git diff --check
```

Expected: 测试、lint、构建和差异检查均无错误；允许仅保留已知的非本任务 lint/chunk-size warning。

- [ ] **Step 6: 检查范围**

确认：

- `LoginPage.tsx` 仍显示“次日顾问”一级标题。
- 所有业务页面说明文字和内部 `<h2>` 未删除。
- `@media (max-width: 768px)` 下 `.brand { font-size: 1.35rem; }` 未修改。
- 不执行 git commit。
