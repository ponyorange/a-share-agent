# 模型配置页 Tab / 搜索 / 槽位表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把模型配置页改成 Tab + 可搜索勾选列表 + 槽位三列表格，保存语义不变。

**Architecture:** 抽出纯函数 `filterProviderModels` 做大小写子串过滤和已选置顶。页面复用 `board-tabs` / `home-tile` / `data-table`，一次只渲染当前提供方。新增 `llm-settings-*` CSS，窄屏把槽位表改成上下叠。

**Tech Stack:** React 19、Vitest + Testing Library、现有 `frontend-advisor/src/styles.css` 变量

## Global Constraints

- Spec：`docs/superpowers/specs/2026-08-17-llm-settings-page-ui-design.md`
- 不改后端、不改 REST、不改勾选保存语义（Key 仍 `saveLlmProvider`，模块/勾选/联网仍 `saveLlmSettings`）
- 不引入 npm 依赖、不做虚拟滚动、不做后端搜索
- 新增类名必须 `llm-settings-` 前缀；不改 `board-tabs` / `data-table` 全局样式
- 去掉页面内联 `maxWidth` 限宽
- 计划中的 commit 步骤默认跳过，除非用户明确要求提交

---

### File map

| 文件 | 职责 |
|------|------|
| `frontend-advisor/src/llmSettingsUi.ts` | `PROVIDER_META`、`SLOT_ROWS`、`filterProviderModels` |
| `frontend-advisor/src/llmSettingsUi.test.ts` | 过滤/置顶纯函数测 |
| `frontend-advisor/src/pages/AgentSettingsPage.tsx` | Tab、搜索列表、槽位表、联网卡片 |
| `frontend-advisor/src/pages/AgentSettingsPage.test.tsx` | Tab / 搜索 / 表头 / 保留原 Tavily 等用例 |
| `frontend-advisor/src/styles.css` | `llm-settings-*` |

---

### Task 1: 模型列表过滤纯函数

**Files:**
- Create: `frontend-advisor/src/llmSettingsUi.ts`
- Create: `frontend-advisor/src/llmSettingsUi.test.ts`

**Interfaces:**
- Consumes: `LlmProviderId` / `LlmSlotId` from `frontend-advisor/src/agentApi.ts`
- Produces:

```ts
export const PROVIDER_META: {
  id: LlmProviderId
  label: string
  docs: string
  docsLabel: string
  defaultModel: string
}[]

export const SLOT_ROWS: { id: LlmSlotId; label: string }[]

export function filterProviderModels(
  available: { id: string }[],
  enabled: string[],
  query: string,
): { id: string }[]
```

`filterProviderModels` 规则：

- `available.length === 0` 时，用 `enabled.map(id => ({ id }))` 当源列表。
- `query.trim()` 为空则不过滤；否则对 `id` 做 **大小写不敏感子串** 匹配。
- 结果先已勾选（保持 `enabled` 相对顺序），再未勾选（保持源列表相对顺序）。

- [ ] **Step 1: 写失败单测**

```ts
// frontend-advisor/src/llmSettingsUi.test.ts
import { describe, expect, it } from 'vitest'
import { PROVIDER_META, SLOT_ROWS, filterProviderModels } from './llmSettingsUi'

describe('filterProviderModels', () => {
  const available = [
    { id: 'deepseek-v4-flash' },
    { id: 'deepseek-chat' },
    { id: 'deepseek-v4-pro' },
    { id: 'other-pro-model' },
  ]
  const enabled = ['deepseek-v4-flash', 'deepseek-v4-pro']

  it('puts enabled models first in enabled order', () => {
    expect(filterProviderModels(available, enabled, '').map((m) => m.id)).toEqual([
      'deepseek-v4-flash',
      'deepseek-v4-pro',
      'deepseek-chat',
      'other-pro-model',
    ])
  })

  it('filters case-insensitively and keeps selected matches first', () => {
    expect(filterProviderModels(available, enabled, 'PRO').map((m) => m.id)).toEqual([
      'deepseek-v4-pro',
      'other-pro-model',
    ])
  })

  it('falls back to enabled ids when available is empty', () => {
    expect(filterProviderModels([], ['kimi-k2.6'], '').map((m) => m.id)).toEqual([
      'kimi-k2.6',
    ])
  })
})

describe('catalog constants', () => {
  it('exports three providers and eight slots', () => {
    expect(PROVIDER_META.map((p) => p.id)).toEqual(['deepseek', 'kimi', 'qwen'])
    expect(SLOT_ROWS).toHaveLength(8)
    expect(SLOT_ROWS[0]).toEqual({ id: 'agent', label: '主 Agent 对话' })
  })
})
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd frontend-advisor && npm test -- src/llmSettingsUi.test.ts`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现常量和纯函数**

```ts
// frontend-advisor/src/llmSettingsUi.ts
import type { LlmProviderId, LlmSlotId } from './agentApi'

export const PROVIDER_META: {
  id: LlmProviderId
  label: string
  docs: string
  docsLabel: string
  defaultModel: string
}[] = [
  {
    id: 'deepseek',
    label: 'DeepSeek',
    docs: 'https://api-docs.deepseek.com/zh-cn/',
    docsLabel: 'DeepSeek API',
    defaultModel: 'deepseek-v4-flash',
  },
  {
    id: 'kimi',
    label: 'Kimi',
    docs: 'https://platform.kimi.com/docs/api/overview',
    docsLabel: 'Kimi API',
    defaultModel: 'kimi-k2.6',
  },
  {
    id: 'qwen',
    label: '千问',
    docs: 'https://platform.qianwenai.com/docs/developer-guides/getting-started/text-generation-models',
    docsLabel: '千问 API',
    defaultModel: 'qwen3.7-plus',
  },
]

export const SLOT_ROWS: { id: LlmSlotId; label: string }[] = [
  { id: 'agent', label: '主 Agent 对话' },
  { id: 'paper', label: '模拟盘' },
  { id: 'home', label: '首页解读' },
  { id: 'monitor', label: '定时任务' },
  { id: 'policy', label: '政策雷达' },
  { id: 'limitup', label: '打板晋级' },
  { id: 'committee_quick', label: '委员会·快速' },
  { id: 'committee_deep', label: '委员会·深度' },
]

export function filterProviderModels(
  available: { id: string }[],
  enabled: string[],
  query: string,
): { id: string }[] {
  const source = available.length ? available : enabled.map((id) => ({ id }))
  const q = query.trim().toLowerCase()
  const filtered = q
    ? source.filter((m) => m.id.toLowerCase().includes(q))
    : [...source]
  const enabledSet = new Set(enabled)
  const selected = enabled
    .filter((id) => filtered.some((m) => m.id === id))
    .map((id) => ({ id }))
  const rest = filtered.filter((m) => !enabledSet.has(m.id))
  return [...selected, ...rest]
}
```

文档 URL 必须与 spec 和现页一致，不要改。

- [ ] **Step 4: 跑测确认通过**

Run: `cd frontend-advisor && npm test -- src/llmSettingsUi.test.ts`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add frontend-advisor/src/llmSettingsUi.ts frontend-advisor/src/llmSettingsUi.test.ts
git commit -m "feat: add searchable model-list helper for LLM settings"
```

---

### Task 2: 设置页 Tab、搜索列表、槽位表

**Files:**
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.tsx`
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`（文件末尾追加）

**Interfaces:**
- Consumes: `PROVIDER_META`、`SLOT_ROWS`、`filterProviderModels` from Task 1
- Produces: 设置页 UI。处理函数（`handleSaveProvider` / `handleClearProvider` / `handleRefresh` / `toggleEnabled` / `changeSlotProvider` / `handleSaveModules` / `handleClearTavily`）行为保持现文件逻辑，只改 JSX 与新增 `activeProvider` / `modelQuery` state。

页面必须满足：

1. `role="tablist"` `aria-label="模型提供方"`，三个 `role="tab"`。未配置 Tab 可见名称为 `{label} 未配置`（如 `Kimi 未配置`），`aria-label` 用同一字符串。默认 `activeProvider === 'deepseek'`。一次只渲染当前 `tabpanel`。
2. 当前提供方包在 `home-tile` + `role="tabpanel"`。已配置才渲染搜索框（`placeholder="搜索模型"`，`aria-label={`搜索${label}模型`}`）和限高列表。计数文案精确为 `已选 ${n} / 共 ${m}`，`m` 为 available（或回退 enabled）总数，不被搜索缩小。无匹配时列表内文本 `无匹配模型`。
3. 槽位：`table-wrap llm-settings-slots` + `data-table`，`<th>` 为 `模块` `提供方` `模型`。`aria-label` 仍是 `{模块} 提供方` / `{模块} 模型`。
4. 联网搜索包在 `home-tile`。综述禁用逻辑不变。
5. 删除所有 `style={{ maxWidth: ... }}`。从本文件删除本地 `PROVIDER_META` / `SLOT_ROWS`，改为 import。

- [ ] **Step 1: 改测试为新交互并确认失败**

把 `shows provider cards and slot rows` 换成 Tab/表头断言，并追加搜索与切 Tab 用例。`configuredFixture` 保持；搜索用例在 beforeEach 之后单独 mock 一份带十余个模型的 DeepSeek。

在 `AgentSettingsPage.test.tsx` 中：

1. 将 `it('shows provider cards and slot rows', ...)` 整段替换为：

```tsx
  it('shows provider tabs and slot table', async () => {
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('tab', { name: 'DeepSeek' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Kimi 未配置' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '千问 未配置' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '模块' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '提供方' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '模型' })).toBeInTheDocument()
    expect(screen.getByLabelText('主 Agent 对话 提供方')).toBeInTheDocument()
  })
```

2. 在该 describe 内追加：

```tsx
  it('shows only the active provider panel', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    expect(await screen.findByLabelText('搜索 DeepSeek 模型')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '千问 未配置' }))
    expect(screen.queryByLabelText('搜索 DeepSeek 模型')).not.toBeInTheDocument()
    expect(screen.getByRole('tabpanel')).toHaveTextContent('千问')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('未配置')
  })

  it('filters models and keeps selected matches first', async () => {
    const user = userEvent.setup()
    const available = [
      { id: 'deepseek-v4-flash' },
      { id: 'deepseek-chat' },
      { id: 'deepseek-reasoner' },
      { id: 'deepseek-v4-pro' },
      { id: 'alpha-pro' },
      { id: 'beta-lite' },
      { id: 'gamma-max' },
      { id: 'delta-coder' },
      { id: 'echo-mini' },
      { id: 'foxtrot-pro' },
      { id: 'golf-base' },
      { id: 'hotel-plus' },
    ]
    const base = configuredFixture()
    vi.mocked(api.fetchLlmSettings).mockResolvedValue(
      configuredFixture({
        providers: {
          ...base.providers,
          deepseek: {
            ...base.providers.deepseek,
            available_models: available,
            enabled_models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
          },
        },
      }),
    )
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    const search = await screen.findByLabelText('搜索 DeepSeek 模型')
    expect(screen.getByText('已选 2 / 共 12')).toBeInTheDocument()
    await user.type(search, 'pro')
    const list = screen.getByTestId('llm-settings-model-list')
    expect(list).toHaveTextContent('deepseek-v4-pro')
    expect(list).toHaveTextContent('alpha-pro')
    expect(list).toHaveTextContent('foxtrot-pro')
    expect(list).not.toHaveTextContent('deepseek-v4-flash')
    const labels = within(list)
      .getAllByRole('checkbox')
      .map((el) => el.closest('label')?.textContent?.trim())
    expect(labels[0]).toBe('deepseek-v4-pro')
  })
```

保留 `hides unconfigured provider from slot dropdown`、`switching provider sets default model`、`disables web research when agent is not deepseek` 以及全部 Tavily 用例，不要删。

Run: `cd frontend-advisor && npm test -- src/pages/AgentSettingsPage.test.tsx`  
Expected: FAIL（仍是三家 heading、无 Tab/搜索/表头）

- [ ] **Step 2: 追加 CSS**

在 `frontend-advisor/src/styles.css` **文件末尾**追加，不要改已有 `.board-tabs` / `.data-table`：

```css
.llm-settings-tabs {
  margin: 0.35rem 0 0.85rem;
}

.llm-settings-panel {
  min-height: 0;
  margin-bottom: 0.5rem;
}

.llm-settings-panel .form-actions {
  margin-top: 0.35rem;
}

.llm-settings-model-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.35rem 0.75rem;
}

.llm-settings-model-list {
  max-height: 16rem;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--bg1);
}

.llm-settings-model-list .meta-line {
  padding: 0.5rem 0.75rem;
}

.llm-settings-model-item {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  margin: 0;
  padding: 0.4rem 0.75rem;
  font-size: 0.85rem;
  font-family: var(--mono);
  color: var(--ink);
}

.llm-settings-slots {
  margin: 0.75rem 0 1.5rem;
}

.llm-settings-slots .input {
  width: 100%;
  min-width: 0;
}

.llm-settings-web {
  margin: 0.75rem 0 1.5rem;
}

@media (max-width: 639px) {
  .llm-settings-slots thead {
    display: none;
  }

  .llm-settings-slots tr {
    display: grid;
    gap: 0.4rem;
    padding: 0.75rem 0.85rem;
    border-bottom: 1px solid var(--line);
  }

  .llm-settings-slots td {
    display: block;
    padding: 0;
    border: 0;
  }

  .llm-settings-slots td:first-child {
    font-weight: 600;
    color: var(--ink);
  }
}
```

- [ ] **Step 3: 改页面**

`AgentSettingsPage.tsx`：

- import 改为从 `../llmSettingsUi` 引入 `PROVIDER_META`、`SLOT_ROWS`、`filterProviderModels`。
- 删除文件内的 `PROVIDER_META` / `SLOT_ROWS` 常量。
- `emptyProvider` 用 `PROVIDER_META.find(...)?.defaultModel`。
- 新增 state：

```ts
const [activeProvider, setActiveProvider] = useState<LlmProviderId>('deepseek')
const [modelQuery, setModelQuery] = useState('')
```

切 Tab 时 `setActiveProvider(id); setModelQuery('')`。

- 当前提供方：

```ts
const activeMeta = PROVIDER_META.find((p) => p.id === activeProvider) || PROVIDER_META[0]
const activePub =
  settings?.providers[activeMeta.id] || emptyProvider(activeMeta.defaultModel)
const activeAvailable = activePub.available_models.length
  ? activePub.available_models
  : enabled[activeMeta.id].map((id) => ({ id }))
const visibleModels = filterProviderModels(
  activePub.available_models,
  enabled[activeMeta.id] || [],
  modelQuery,
)
```

- **return** 中「模型提供方」整段换成（保留 hero / status / 处理函数）：

```tsx
      <h2 className="section-title">模型提供方</h2>
      <div className="board-tabs llm-settings-tabs" role="tablist" aria-label="模型提供方">
        {PROVIDER_META.map((meta) => {
          const configured = Boolean(settings?.providers[meta.id]?.configured)
          const name = configured ? meta.label : `${meta.label} 未配置`
          return (
            <button
              key={meta.id}
              type="button"
              role="tab"
              className={`board-tab${activeProvider === meta.id ? ' active' : ''}`}
              aria-selected={activeProvider === meta.id}
              aria-label={name}
              onClick={() => {
                setActiveProvider(meta.id)
                setModelQuery('')
              }}
            >
              {name}
            </button>
          )
        })}
      </div>
      <div className="home-tile llm-settings-panel" role="tabpanel">
        <h3 className="home-tile-title">{activeMeta.label}</h3>
        <p className="meta-line">
          文档见{' '}
          <a className="text-link" href={activeMeta.docs} target="_blank" rel="noreferrer">
            {activeMeta.docsLabel}
          </a>
          {activePub.configured ? ` · 已配置（${activePub.key_hint}）` : ' · 未配置'}
        </p>
        <label className="strategy-field">
          <span>API Key</span>
          <input
            className="input mono"
            type="password"
            autoComplete="off"
            placeholder={activePub.configured ? '输入新 Key 以覆盖' : 'sk-…'}
            value={keys[activeMeta.id]}
            onChange={(e) =>
              setKeys((prev) => ({ ...prev, [activeMeta.id]: e.target.value }))
            }
          />
        </label>
        <div className="form-actions">
          <button
            type="button"
            className="btn"
            disabled={saving || !keys[activeMeta.id].trim()}
            onClick={() => handleSaveProvider(activeMeta.id)}
          >
            {busy === `save-${activeMeta.id}` ? '保存中…' : '保存并校验'}
          </button>
          {activePub.configured ? (
            <button
              type="button"
              className="btn ghost"
              disabled={saving}
              onClick={() => handleClearProvider(activeMeta.id)}
            >
              清除 {activeMeta.label} Key
            </button>
          ) : null}
          {activePub.configured ? (
            <button
              type="button"
              className="btn ghost"
              disabled={saving}
              onClick={() => handleRefresh(activeMeta.id)}
            >
              {busy === `refresh-${activeMeta.id}` ? '刷新中…' : '刷新模型'}
            </button>
          ) : null}
        </div>
        {activePub.configured ? (
          <div className="strategy-field">
            <div className="llm-settings-model-head">
              <span>可用模型</span>
              <span className="meta-line">
                已选 {enabled[activeMeta.id].length} / 共 {activeAvailable.length}
              </span>
            </div>
            <input
              className="input"
              type="search"
              placeholder="搜索模型"
              aria-label={`搜索${activeMeta.label}模型`}
              value={modelQuery}
              onChange={(e) => setModelQuery(e.target.value)}
            />
            <div className="llm-settings-model-list" data-testid="llm-settings-model-list">
              {visibleModels.length === 0 ? (
                <p className="meta-line">
                  {activeAvailable.length === 0
                    ? '模型列表为空，请点击刷新模型。'
                    : '无匹配模型'}
                </p>
              ) : (
                visibleModels.map((m) => (
                  <label key={m.id} className="llm-settings-model-item">
                    <input
                      type="checkbox"
                      checked={enabled[activeMeta.id].includes(m.id)}
                      onChange={(e) =>
                        toggleEnabled(activeMeta.id, m.id, e.target.checked)
                      }
                    />
                    {m.id}
                  </label>
                ))
              )}
            </div>
          </div>
        ) : null}
      </div>
```

槽位表替换现 `strategy-grid` 槽位块：

```tsx
      <h2 className="section-title">功能模块</h2>
      {!settings?.configured ? (
        <p className="meta-line">请先配置至少一个模型提供方。</p>
      ) : (
        <div className="table-wrap llm-settings-slots">
          <table className="data-table">
            <thead>
              <tr>
                <th>模块</th>
                <th>提供方</th>
                <th>模型</th>
              </tr>
            </thead>
            <tbody>
              {SLOT_ROWS.map((row) => {
                const val = slots?.[row.id]
                const pid = (val?.provider || configuredProviders[0]) as LlmProviderId
                const models = enabled[pid] || []
                return (
                  <tr key={row.id}>
                    <td>{row.label}</td>
                    <td>
                      <select
                        className="input"
                        aria-label={`${row.label} 提供方`}
                        value={pid}
                        disabled={!configuredProviders.length}
                        onChange={(e) =>
                          changeSlotProvider(row.id, e.target.value as LlmProviderId)
                        }
                      >
                        {configuredProviders.map((id) => (
                          <option key={id} value={id}>
                            {PROVIDER_META.find((p) => p.id === id)?.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <select
                        className="input"
                        aria-label={`${row.label} 模型`}
                        value={val?.model || models[0] || ''}
                        disabled={!models.length}
                        onChange={(e) =>
                          setSlots((old) =>
                            old
                              ? {
                                  ...old,
                                  [row.id]: { provider: pid, model: e.target.value },
                                }
                              : old,
                          )
                        }
                      >
                        {models.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
```

联网搜索：外层改成 `<div className="home-tile llm-settings-web">`，去掉 `strategy-grid` 的 `maxWidth` 内联样式。标题「联网搜索」仍用 `h2.section-title` 放在卡片外（与提供方、功能模块一致）。

- [ ] **Step 4: 跑测确认通过**

Run: `cd frontend-advisor && npm test -- src/pages/AgentSettingsPage.test.tsx src/llmSettingsUi.test.ts`  
Expected: PASS

再跑：`cd frontend-advisor && npm test`  
Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add frontend-advisor/src/pages/AgentSettingsPage.tsx frontend-advisor/src/pages/AgentSettingsPage.test.tsx frontend-advisor/src/styles.css frontend-advisor/src/llmSettingsUi.ts
git commit -m "feat: restyle LLM settings with tabs and model search"
```

---

## Spec coverage（自检）

| Spec 项 | 任务 |
|---------|------|
| Tab 一次一家、未配置文案、默认 DeepSeek | 2 |
| 搜索子串、已选置顶、已选 n / 共 m、无匹配、限高 16rem | 1, 2 |
| 槽位三列表格 + aria-label + 窄屏叠 | 2 |
| 联网卡片 + 综述禁用 | 2 |
| 不改后端 / 保存语义 | 全程 |
| 复用 board-tabs / home-tile / data-table | 2 |
| `llm-settings-` 前缀、去掉内联限宽 | 2 |
| 测试：Tab、搜索、表头、保留 Tavily | 2 |
