# Frontend Advisor 移动端适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为今日关注、模拟盘和 Agent 聊天提供在 `≤768px` 启用的移动端专用布局，同时保持桌面布局、业务回调和后端协议不变。

**Architecture:** 新增可复用的响应式数据视图、折叠区、移动卡片、Agent 会话抽屉和输入器。页面继续拥有数据请求及业务状态，同一份数据按视口和用户偏好渲染为桌面表格或移动卡片；全局 CSS 只负责布局、滚动、安全区和触控尺寸。

**Tech Stack:** React 19、TypeScript 6、React Router 7、React Virtuoso、Vitest 4、Testing Library、全局 CSS。

## Global Constraints

- 适配在视口宽度不超过 `768px` 时启用，重点验证 `375–430px`。
- 桌面布局和现有业务接口保持不变。
- 不新增后端接口，不改变账户、推荐、订单或聊天数据协议。
- 不引入新的 CSS 框架、组件库或第三方依赖。
- 推荐卡片保留现有“诊断 / 查看 K 线”；不新增单标的交易语义。
- 移动端默认卡片视图，可切换横滑表格；偏好按数据集写入 `localStorage`。
- 表格名称、代码和关键数字不得逐字断行。
- 底部固定区域使用 `env(safe-area-inset-bottom)`，主要触控目标至少约 `44px` 高。
- 危险操作继续使用现有确认流程。
- 未经用户明确要求不创建 Git commit；每项任务以测试通过和复核为检查点。

---

## File Map

### 新增

- `frontend-advisor/src/components/ResponsiveDataView.tsx`：视口检测、卡片/表格切换及本地偏好。
- `frontend-advisor/src/components/ResponsiveDataView.test.tsx`：移动默认值、切换、偏好恢复和桌面回退测试。
- `frontend-advisor/src/components/MobileDisclosure.tsx`：可访问的移动端折叠信息容器。
- `frontend-advisor/src/components/RecommendationCard.tsx`：推荐标的移动卡片。
- `frontend-advisor/src/components/RecommendationCard.test.tsx`：推荐字段与现有链接测试。
- `frontend-advisor/src/components/PaperCards.tsx`：持仓、跟踪和成交记录移动卡片。
- `frontend-advisor/src/components/PaperCards.test.tsx`：模拟盘卡片字段和操作测试。
- `frontend-advisor/src/pages/RecommendationsPage.test.tsx`：今日关注移动结构集成测试。
- `frontend-advisor/src/pages/PaperPage.test.tsx`：模拟盘移动结构与危险操作集成测试。
- `frontend-advisor/src/components/AgentConversationDrawer.tsx`：移动会话抽屉及焦点管理。
- `frontend-advisor/src/components/AgentConversationDrawer.test.tsx`：抽屉关闭方式和焦点还原测试。
- `frontend-advisor/src/components/AgentComposer.tsx`：自动增高输入框及发送键盘行为。
- `frontend-advisor/src/components/AgentComposer.test.tsx`：输入、Enter、Shift+Enter 和禁用状态测试。

### 修改

- `frontend-advisor/src/pages/RecommendationsPage.tsx`：首屏摘要、折叠元信息、响应式候选数据和操作分组。
- `frontend-advisor/src/pages/PaperPage.tsx`：账户摘要、收益折叠、操作分层和三组响应式数据。
- `frontend-advisor/src/pages/AgentChatPage.tsx`：移动顶栏、会话抽屉、快捷问题和新 composer。
- `frontend-advisor/src/pages/AgentChatPage.test.tsx`：抽屉、快捷问题和输入区页面集成测试。
- `frontend-advisor/src/App.tsx`：为移动导航和聊天壳层补充可定位的语义类名。
- `frontend-advisor/src/App.test.tsx`：基础导航和聊天壳层回归测试。
- `frontend-advisor/src/styles.css`：`768px` 移动断点、卡片、横滑表格、抽屉、聊天、安全区与触控样式。

---

### Task 1: 响应式数据视图与折叠区

**Files:**
- Create: `frontend-advisor/src/components/ResponsiveDataView.tsx`
- Create: `frontend-advisor/src/components/ResponsiveDataView.test.tsx`
- Create: `frontend-advisor/src/components/MobileDisclosure.tsx`

**Interfaces:**
- Produces: `type DataViewMode = 'card' | 'table'`
- Produces: `useMediaQuery(query: string): boolean`
- Produces: `ResponsiveDataView({ storageKey, label, cards, table })`
- Produces: `MobileDisclosure({ summary, children, className? })`
- Consumes: `window.matchMedia`、`localStorage`

- [ ] **Step 1: 写响应式视图失败测试**

测试中提供可切换的 `matchMedia` stub；验证移动端默认只显示卡片，点击“表格视图”后只显示表格并写入偏好，重新挂载恢复表格；桌面端始终显示表格。

```tsx
// frontend-advisor/src/components/ResponsiveDataView.test.tsx
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { ResponsiveDataView } from './ResponsiveDataView'

function setViewport(mobile: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: mobile,
      media: '(max-width: 768px)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

beforeEach(() => localStorage.clear())

it('移动端默认卡片并保存表格偏好', async () => {
  setViewport(true)
  const user = userEvent.setup()
  const view = render(
    <ResponsiveDataView
      storageKey="test-view"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )
  expect(screen.getByText('卡片内容')).toBeInTheDocument()
  expect(screen.queryByText('表格内容')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '表格视图' }))
  expect(screen.getByText('表格内容')).toBeInTheDocument()
  expect(localStorage.getItem('test-view')).toBe('table')
  view.unmount()
  render(
    <ResponsiveDataView
      storageKey="test-view"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )
  expect(screen.getByText('表格内容')).toBeInTheDocument()
})

it('桌面端忽略移动偏好并保持表格', () => {
  localStorage.setItem('test-view', 'card')
  setViewport(false)
  render(
    <ResponsiveDataView
      storageKey="test-view"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )
  expect(screen.getByText('表格内容')).toBeInTheDocument()
  expect(screen.queryByRole('group', { name: '候选视图' })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/components/ResponsiveDataView.test.tsx`

Expected: FAIL，提示无法解析 `./ResponsiveDataView`。

- [ ] **Step 3: 实现最小响应式视图**

实现 `useMediaQuery` 的订阅与清理；仅接受 `card/table` 两种存储值；读写存储异常时静默回退；桌面端只渲染表格；移动端渲染带 `aria-pressed` 的视图切换按钮。

```tsx
// frontend-advisor/src/components/ResponsiveDataView.tsx
import { useEffect, useState, type ReactNode } from 'react'

export type DataViewMode = 'card' | 'table'

export function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false,
  )
  useEffect(() => {
    const media = window.matchMedia(query)
    const update = () => setMatches(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [query])
  return matches
}

export function ResponsiveDataView(props: {
  storageKey: string
  label: string
  cards: ReactNode
  table: ReactNode
}) {
  const isMobile = useMediaQuery('(max-width: 768px)')
  const [mode, setMode] = useState<DataViewMode>(() => {
    try {
      return localStorage.getItem(props.storageKey) === 'table' ? 'table' : 'card'
    } catch {
      return 'card'
    }
  })
  const select = (next: DataViewMode) => {
    setMode(next)
    try {
      localStorage.setItem(props.storageKey, next)
    } catch {
      // 展示不依赖持久化成功
    }
  }
  if (!isMobile) return <>{props.table}</>
  return (
    <section className={`responsive-data-view responsive-data-view--${mode}`}>
      <div className="responsive-view-toggle" role="group" aria-label={`${props.label}视图`}>
        <button type="button" aria-pressed={mode === 'card'} onClick={() => select('card')}>
          卡片视图
        </button>
        <button type="button" aria-pressed={mode === 'table'} onClick={() => select('table')}>
          表格视图
        </button>
      </div>
      {mode === 'card' ? props.cards : props.table}
    </section>
  )
}
```

`MobileDisclosure` 使用原生 `<details>` 和 `<summary>`，附加 `.mobile-disclosure` 类，不管理业务状态。

- [ ] **Step 4: 运行组件测试**

Run: `cd frontend-advisor && npx vitest run src/components/ResponsiveDataView.test.tsx`

Expected: PASS。

- [ ] **Step 5: 运行类型检查**

Run: `cd frontend-advisor && npm run build`

Expected: TypeScript 与 Vite 构建通过。

---

### Task 2: 今日关注移动布局

**Files:**
- Create: `frontend-advisor/src/components/RecommendationCard.tsx`
- Create: `frontend-advisor/src/components/RecommendationCard.test.tsx`
- Create: `frontend-advisor/src/pages/RecommendationsPage.test.tsx`
- Modify: `frontend-advisor/src/pages/RecommendationsPage.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Consumes: `AdviceItem`、`formatPct()`、`formatScore()`、`explorerKlineUrl()`
- Produces: `RecommendationCard({ item })`
- Consumes from Task 1: `ResponsiveDataView`、`MobileDisclosure`

- [ ] **Step 1: 写推荐卡片失败测试**

使用包含代码、名称、收盘、涨跌幅、评分、建议和命中率的 fixture，断言卡片展示关键字段，并且“诊断”和“查看 K 线”分别指向现有 URL；不得出现单标的“买入”按钮。

```tsx
render(
  <MemoryRouter>
    <RecommendationCard item={item} />
  </MemoryRouter>,
)
expect(screen.getByText('标普油气ETF嘉实')).toBeInTheDocument()
expect(screen.getByRole('link', { name: '诊断' })).toHaveAttribute(
  'href',
  '/advice?symbol=159518',
)
expect(screen.queryByRole('button', { name: '买入' })).not.toBeInTheDocument()
```

- [ ] **Step 2: 运行卡片测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/components/RecommendationCard.test.tsx`

Expected: FAIL，组件尚不存在。

- [ ] **Step 3: 实现推荐卡片**

卡片头部显示名称、代码、评分和建议徽章；中部显示收盘、日涨幅和命中率；底部复用现有诊断与 K 线链接。正负样式继续调用页面已有的 `chgClass` 逻辑，将该函数导出或移动到组件内作为私有 helper。

- [ ] **Step 4: 写页面集成失败测试**

Mock `fetchRecommendations()` 返回 ETF 板块数据；移动 `matchMedia` 下断言：

- “大池 / 精算 / 推荐”三个摘要指标存在；
- 长归档信息位于“筛选与数据源”折叠区；
- 默认显示推荐卡片；
- 切换表格后出现列标题；
- 页面仍有“一键买入 ETF/沪深”，且没有单标的买入按钮。

- [ ] **Step 5: 重构 RecommendationsPage**

给根节点增加 `recommendations-page`：

```tsx
<section className="page recommendations-page">
```

将现有长 `meta-line` 拆成：

```tsx
<div className="recommendation-summary" aria-label="推荐摘要">
  <div className="summary-stat"><span>大池</span><strong>{board?.pool_size ?? '—'}</strong></div>
  <div className="summary-stat"><span>精算</span><strong>{board?.precise_size ?? board?.scanned ?? '—'}</strong></div>
  <div className="summary-stat"><span>推荐</span><strong>{board?.count ?? 0}</strong></div>
</div>
<MobileDisclosure summary="筛选与数据源">
  <p className="meta-line">{/* 保留现有归档、日期、错误和来源文案 */}</p>
</MobileDisclosure>
```

将 `BoardTable` 放入 `ResponsiveDataView` 的 `table`，将 `items.map(item => <RecommendationCard ... />)` 放入 `cards`。`storageKey` 固定为 `advisor_recommendations_view`。不改刷新、行情、一键买入和限购状态。

- [ ] **Step 6: 添加推荐页移动样式**

在 `@media (max-width: 768px)` 中：

- `.recommendations-page .page-hero p` 缩短首屏占用；
- `.recommendation-summary` 三列；
- `.recommendations-page .form-actions` 分成筛选、次要操作和主操作；
- `.board-tabs` 单行横滚；
- `.buy-cap` 两行布局，stepper 不再 `margin-left:auto` 挤压；
- `.recommendation-card-list` 单列；
- `.recommendations-page .data-table` 设置 `min-width: 50rem`；
- `.data-table .name-cell` 使用 `white-space: nowrap`；
- 主按钮和视图切换按钮最小高度 `44px`。

- [ ] **Step 7: 运行推荐页测试**

Run: `cd frontend-advisor && npx vitest run src/components/RecommendationCard.test.tsx src/pages/RecommendationsPage.test.tsx`

Expected: PASS。

---

### Task 3: 模拟盘移动布局

**Files:**
- Create: `frontend-advisor/src/components/PaperCards.tsx`
- Create: `frontend-advisor/src/components/PaperCards.test.tsx`
- Create: `frontend-advisor/src/pages/PaperPage.test.tsx`
- Modify: `frontend-advisor/src/pages/PaperPage.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Produces: `PaperPositionCard({ position, onSell, onDelete })`
- Produces: `PaperPerformanceCard({ row })`
- Produces: `PaperTradeCard({ trade })`
- Consumes from Task 1: `ResponsiveDataView`、`MobileDisclosure`
- Consumes: `PaperAccount['positions'][number]` 和现有 `Record<string, unknown>` 数据

- [ ] **Step 1: 写模拟盘卡片失败测试**

验证持仓卡片显示名称、代码、数量、成本、现价、市值和浮盈亏；点击“卖出”和“删除”分别调用传入回调。验证跟踪和成交卡片使用与当前表格一致的字段键，不自行计算交易结果。

- [ ] **Step 2: 运行卡片测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/components/PaperCards.test.tsx`

Expected: FAIL，组件尚不存在。

- [ ] **Step 3: 实现 PaperCards**

`PaperPositionCard` 直接接收强类型 position；`PaperPerformanceCard` 读取 `symbol/name/rec_date/buy_price/last/unrealized_pnl_pct`；`PaperTradeCard` 读取现有成交表格使用的字段。所有未知值使用 `—`，数值格式继续复用 `formatPct`，危险删除按钮使用 `.danger` 类。

- [ ] **Step 4: 写 PaperPage 集成失败测试**

Mock `fetchPaper()`、`fetchPaperPnl()`、`fetchOneClickPerf()`、`fetchPaperTrades()`；移动视口下验证：

- 总权益和总收益处于账户摘要；
- 历史收益与持仓收益位于折叠区；
- “更多操作”折叠区包含“一键卖出”和“重置资金”；
- 下单字段纵向可访问；
- 持仓、跟踪、成交记录默认卡片并各自可切换表格；
- 点击删除仍触发 `window.confirm`。

- [ ] **Step 5: 重构 PaperPage**

给根节点增加 `paper-page`。账户摘要使用 `.paper-account-summary`，总权益与 `pnl?.total` 为一级信息，现金和市值为二级信息。用两个 `MobileDisclosure` 包裹历史收益和持仓收益；桌面通过 CSS 保持展开式布局。

将当前重置、刷新、一键卖出重组为：

```tsx
<div className="paper-primary-actions">
  <button className="btn" type="button" onClick={refreshMarkToMarket}>刷新市值</button>
  <MobileDisclosure summary="更多操作" className="paper-danger-actions">
    {/* 原 cashInput、重置按钮、一键卖出按钮 */}
  </MobileDisclosure>
</div>
```

保持所有 handler 原样。下单表单只增加 `paper-order-form` 类。三个表格分别用 `ResponsiveDataView` 包裹，storage key 为：

- `advisor_paper_positions_view`
- `advisor_paper_performance_view`
- `advisor_paper_trades_view`

- [ ] **Step 6: 添加模拟盘移动样式**

在 `@media (max-width: 768px)` 中：

- `.paper-account-summary` 使用总权益大卡和两列二级指标；
- 收益折叠区、更多操作、下单表单使用单列；
- 行内 `maxWidth` 被 scoped CSS 覆盖为 `width:100%;max-width:none`；
- 卡片操作按钮最小高度 `44px`；
- 三张表分别设置足够的 `min-width`，保证横向滚动；
- 分页为三列，按钮不小于 `44px`。

- [ ] **Step 7: 运行模拟盘测试**

Run: `cd frontend-advisor && npx vitest run src/components/PaperCards.test.tsx src/pages/PaperPage.test.tsx`

Expected: PASS。

---

### Task 4: Agent 会话抽屉与输入器

**Files:**
- Create: `frontend-advisor/src/components/AgentConversationDrawer.tsx`
- Create: `frontend-advisor/src/components/AgentConversationDrawer.test.tsx`
- Create: `frontend-advisor/src/components/AgentComposer.tsx`
- Create: `frontend-advisor/src/components/AgentComposer.test.tsx`

**Interfaces:**
- Produces: `AgentConversationDrawer({ open, sessions, activeSessionId, disabled, triggerRef, onClose, onNew, onOpen, onDelete })`
- Produces: `AgentComposer({ value, disabled, sending, error, onChange, onSend })`
- Consumes: `AgentSession`

- [ ] **Step 1: 写抽屉可访问性失败测试**

渲染打开的抽屉，断言关闭按钮自动获得焦点；按 Escape 或点击带 `data-testid="agent-drawer-backdrop"` 的遮罩调用 `onClose`；关闭后焦点回到 `triggerRef`；会话按钮调用 `onOpen(sessionId)`。

- [ ] **Step 2: 写输入器失败测试**

断言：

- 输入变化调用 `onChange`；
- Enter 阻止默认并调用 `onSend`；
- Shift+Enter 不发送；
- 空内容或 disabled 时发送按钮不可用；
- `sending` 时按钮文案为“生成中…”；
- error 使用 `role="alert"` 显示。

- [ ] **Step 3: 运行测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/components/AgentConversationDrawer.test.tsx src/components/AgentComposer.test.tsx`

Expected: FAIL，两个组件尚不存在。

- [ ] **Step 4: 实现会话抽屉**

抽屉只在 `open` 时挂载；`useEffect` 注册 Escape 监听并聚焦关闭按钮；清理时还原触发器焦点。遮罩和面板分开，面板使用 `role="dialog"`、`aria-modal="true"`、`aria-label="对话记录"`。复用现有会话标题、消息数和删除动作，不请求数据。

- [ ] **Step 5: 实现 AgentComposer**

输入框 ref 在 value 变化时先设 `height:auto`，再将高度设为 `Math.min(scrollHeight, 120px)`；CSS 负责超过高度后内部滚动。表单提交与 Enter 都调用同一个 `submit()`，该函数仅在非 disabled 且 value.trim() 非空时调用 `onSend`。

- [ ] **Step 6: 运行组件测试**

Run: `cd frontend-advisor && npx vitest run src/components/AgentConversationDrawer.test.tsx src/components/AgentComposer.test.tsx`

Expected: PASS。

---

### Task 5: AgentChatPage 移动集成

**Files:**
- Modify: `frontend-advisor/src/pages/AgentChatPage.tsx`
- Modify: `frontend-advisor/src/pages/AgentChatPage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Consumes from Task 4: `AgentConversationDrawer`、`AgentComposer`
- Preserves: `send()`、`handleNewChat()`、`openSession()`、`handleDelete()`、Virtuoso 滚动和流失效保护

- [ ] **Step 1: 写页面移动交互失败测试**

在现有 API mock 基础上新增：

- 移动顶栏存在“打开对话记录”按钮；
- 点击后显示抽屉，选择会话调用既有加载路径并关闭抽屉；
- 新对话后抽屉关闭；
- 快捷问题区域可访问；
- composer 发送后仍调用现有 `streamAgentChat`；
- 工具与子 Agent 进度默认折叠行为保持。

- [ ] **Step 2: 运行 AgentChatPage 测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/pages/AgentChatPage.test.tsx`

Expected: 新增断言 FAIL，现有测试仍通过。

- [ ] **Step 3: 集成移动顶栏和抽屉**

新增：

```tsx
const [drawerOpen, setDrawerOpen] = useState(false)
const drawerTriggerRef = useRef<HTMLButtonElement>(null)
const currentSession = sessions.find((session) => session.session_id === sessionId)
```

在 `.agent-main` 顶部渲染 `.agent-mobile-header`，包含菜单按钮、单行省略的当前标题和新对话按钮。桌面 `.agent-sidebar` 保留。抽屉接收相同 sessions 与 handler；打开会话、新建或删除成功后关闭抽屉。

- [ ] **Step 4: 替换 composer 并整理快捷问题**

用 `AgentComposer` 替换现有 form；传入 `input/setInput`、`composingDisabled`、`sending`、`error` 和 `send`。错误从消息区底部移动到 composer 上方的 alert，但保留安全文案。快捷问题容器增加单行横滚结构和“更多”视觉提示；开始输入时添加 `is-composing` 类以弱化。

- [ ] **Step 5: 添加 Agent 移动样式**

在 `@media (max-width: 768px)` 中：

- `.agent-layout` 单列且高度使用可用 `100dvh`；
- 隐藏桌面 sidebar，显示移动 header；
- drawer 固定覆盖视口，面板宽 `min(85vw, 22rem)`；
- `.agent-chat` 保持 `min-height:0` 和单一滚动；
- 助手气泡最大宽度约 `94%`；
- Markdown `pre/table/a` 不撑宽页面；
- 快捷问题单行横滚；
- composer 固定为单行“textarea + 发送”，textarea 自动增高，底部加入安全区；
- 进度面板折叠时只占一行；
- 不让 `panel-switch--float` 覆盖输入区。

- [ ] **Step 6: 运行 Agent 测试**

Run: `cd frontend-advisor && npx vitest run src/pages/AgentChatPage.test.tsx src/components/AgentConversationDrawer.test.tsx src/components/AgentComposer.test.tsx`

Expected: PASS。

---

### Task 6: 全局移动导航与桌面回归

**Files:**
- Modify: `frontend-advisor/src/App.tsx`
- Modify: `frontend-advisor/src/App.test.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Preserves: 路由、登录状态、`switchPanel()` 和 `PANEL_KEY`
- Produces: `.topbar-nav-wrap`、`.app-shell--agent-chat` 的移动端可定位结构

- [ ] **Step 1: 写 App 壳层回归失败测试**

新增认证状态下的测试，断言基础页导航仍包含模拟盘；Agent chat 壳层仍存在，面板切换按钮仍在 DOM 中供桌面使用；导航容器拥有用于移动横滚的类。测试不通过 jsdom 断言 CSS 可见性。

- [ ] **Step 2: 运行 App 测试并确认失败**

Run: `cd frontend-advisor && npx vitest run src/App.test.tsx`

Expected: 新的语义类断言 FAIL。

- [ ] **Step 3: 添加语义容器**

保持链接和事件不变，只用容器包住现有 nav：

```tsx
<div className="topbar-nav-wrap">
  <nav className="nav" aria-label={isAgent ? 'Agent 导航' : '基础导航'}>
    {/* 现有 NavLink */}
  </nav>
</div>
```

- [ ] **Step 4: 添加全局 `768px` 规则**

- 普通页面 `.app-shell` 减少水平 padding 并预留底部安全区；
- `.topbar` 单列组织品牌/用户与导航；
- `.topbar-nav-wrap` 横向滚动，`.nav` 不换行；
- Agent chat 手机端隐藏全局 topbar 和浮动 panel switch，改由页面移动顶栏承担入口；
- 普通页 panel switch 上移到安全区之上；
- 通用 `.table-wrap` 启用 `overscroll-behavior-inline: contain`；
- `prefers-reduced-motion` 下关闭新增抽屉和滚动提示动画。

- [ ] **Step 5: 运行 App 与相关页面测试**

Run: `cd frontend-advisor && npx vitest run src/App.test.tsx src/pages/RecommendationsPage.test.tsx src/pages/PaperPage.test.tsx src/pages/AgentChatPage.test.tsx`

Expected: PASS。

---

### Task 7: 全量验证与视口验收

**Files:**
- Verify only: `frontend-advisor/src/**`
- Compare against: `docs/superpowers/specs/2026-07-25-frontend-advisor-mobile-design.md`

**Interfaces:**
- Consumes: Tasks 1–6 的全部成果
- Produces: 可交付的测试、构建和人工视口验收证据

- [ ] **Step 1: 运行全量单元测试**

Run: `cd frontend-advisor && npm test`

Expected: 所有 Vitest 测试通过，无未处理 Promise 或 act 警告。

- [ ] **Step 2: 运行 lint**

Run: `cd frontend-advisor && npm run lint`

Expected: oxlint 退出码 0。

- [ ] **Step 3: 运行生产构建**

Run: `cd frontend-advisor && npm run build`

Expected: `tsc -b && vite build` 退出码 0。

- [ ] **Step 4: 启动应用做视口检查**

Run: `cd frontend-advisor && npm run dev -- --host 127.0.0.1`

在 `375×667`、`390×844`、`430×932`、`768×1024` 检查 `/`、`/paper`、`/agent`：

- 页面没有非预期整体横向滚动；
- 今日关注摘要清晰，默认卡片，表格可横滑且名称不竖排；
- 模拟盘总权益优先，收益和危险操作可折叠，三组数据可切换；
- Agent 会话抽屉、消息滚动、快捷问题和输入器互不遮挡；
- 软键盘弹出时输入区仍可见，最后一条消息可滚到输入区上方；
- 主要按钮触控高度约 `44px`，底部控件避开安全区。

- [ ] **Step 5: 检查桌面回归**

在宽度 `1280px` 检查同一路由：推荐和模拟盘仍默认表格；Agent 仍为双栏；全局导航、面板切换、刷新、买入、下单、卖出和聊天发送行为不变。

- [ ] **Step 6: 复核改动范围**

Run: `git diff -- frontend-advisor docs/superpowers/specs/2026-07-25-frontend-advisor-mobile-design.md docs/superpowers/plans/2026-07-25-frontend-advisor-mobile.md`

Expected: 仅包含移动端布局、对应测试与设计/计划文档；不包含后端协议、规则或无关重构。
