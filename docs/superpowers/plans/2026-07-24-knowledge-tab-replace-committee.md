# 知识库 Tab 替代投委会入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端 Agent 导航隐藏「投委会」，用独立「知识库」Tab（`/agent/knowledge`）替代；投委会直链保留。

**Architecture:** 从 `AgentSettingsPage` 迁出知识库 CRUD UI 到新建 `KnowledgePage`；`App.tsx` 换导航链接并注册路由；`/agent/committee` 路由保留但不挂 NavLink。不改后端与投委会业务代码。

**Tech Stack:** React + React Router + Vitest + Testing Library（`frontend-advisor`）

## Global Constraints

- 不改动投委会业务逻辑、API 或 `committee/` 目录。
- 不新增或修改知识库后端 API；继续用现有 `agentApi` 知识库函数。
- 不删除 `CommitteePage`；仅隐藏导航入口。
- 知识库页复用现有 `knowledge-*` CSS class，不引入新设计体系。
- `isAgentChat` 仍仅覆盖 `/agent` 与 `/agent/committee`；知识库页用普通 Agent shell。

---

## File Structure

| 文件 | 职责 |
|------|------|
| `frontend-advisor/src/pages/KnowledgePage.tsx` | 新建：知识库列表/CRUD UI |
| `frontend-advisor/src/pages/AgentSettingsPage.tsx` | 收窄：仅 DeepSeek 配置 |
| `frontend-advisor/src/App.tsx` | 导航「知识库」+ 路由 `/agent/knowledge`；去掉「投委会」NavLink |
| `frontend-advisor/src/App.test.tsx` | 断言导航与直链行为 |

---

### Task 1: 新建 KnowledgePage 并从设置页迁出知识库

**Files:**
- Create: `frontend-advisor/src/pages/KnowledgePage.tsx`
- Modify: `frontend-advisor/src/pages/AgentSettingsPage.tsx`

**Interfaces:**
- Consumes: `listKnowledge`, `createKnowledge`, `updateKnowledge`, `deleteKnowledge`, `KnowledgeInput`, `KnowledgeItem`, `KnowledgeMode` from `../agentApi`
- Produces: default export `KnowledgePage`（无 props）

- [ ] **Step 1: 创建 `KnowledgePage.tsx`**

将原 `AgentSettingsPage` 中知识库相关逻辑原样迁入（state、handlers、UI），hero 改为独立页标题。完整文件：

```tsx
import { useCallback, useEffect, useState } from 'react'
import {
  createKnowledge,
  deleteKnowledge,
  listKnowledge,
  updateKnowledge,
  type KnowledgeInput,
  type KnowledgeItem,
  type KnowledgeMode,
} from '../agentApi'

const EMPTY_FORM: KnowledgeInput = {
  title: '',
  mode: 'always',
  enabled: true,
  description: '',
  body: '',
}

function itemToInput(item: KnowledgeItem): KnowledgeInput {
  return {
    title: item.title,
    mode: item.mode,
    enabled: item.enabled,
    description: item.description,
    body: item.body,
  }
}

export default function KnowledgePage() {
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [kbLoading, setKbLoading] = useState(true)
  const [kbError, setKbError] = useState<string | null>(null)
  const [kbSaving, setKbSaving] = useState(false)
  const [editing, setEditing] = useState<null | 'create' | KnowledgeItem>(null)
  const [viewing, setViewing] = useState<KnowledgeItem | null>(null)
  const [form, setForm] = useState<KnowledgeInput>(EMPTY_FORM)

  const loadKnowledge = useCallback(async () => {
    setKbLoading(true)
    setKbError(null)
    try {
      const res = await listKnowledge()
      setItems(res.items)
    } catch (err) {
      setKbError(err instanceof Error ? err.message : String(err))
    } finally {
      setKbLoading(false)
    }
  }, [])

  useEffect(() => {
    loadKnowledge()
  }, [loadKnowledge])

  function startCreate() {
    setViewing(null)
    setEditing('create')
    setForm(EMPTY_FORM)
    setKbError(null)
  }

  function startEdit(item: KnowledgeItem) {
    setViewing(null)
    setEditing(item)
    setForm(itemToInput(item))
    setKbError(null)
  }

  function cancelKnowledgeForm() {
    setEditing(null)
    setKbError(null)
  }

  async function handleSaveKnowledge() {
    setKbSaving(true)
    setKbError(null)
    try {
      if (editing === 'create') {
        await createKnowledge(form)
      } else if (editing) {
        await updateKnowledge(editing.id, form)
      }
      setEditing(null)
      await loadKnowledge()
    } catch (err) {
      setKbError(err instanceof Error ? err.message : String(err))
    } finally {
      setKbSaving(false)
    }
  }

  async function handleToggleEnabled(item: KnowledgeItem) {
    setKbError(null)
    try {
      await updateKnowledge(item.id, { ...itemToInput(item), enabled: !item.enabled })
      await loadKnowledge()
    } catch (err) {
      setKbError(err instanceof Error ? err.message : String(err))
    }
  }

  async function handleDeleteKnowledge(item: KnowledgeItem) {
    if (!window.confirm(`删除知识条目「${item.title}」？此操作不可撤销。`)) return
    setKbError(null)
    try {
      await deleteKnowledge(item.id)
      if (viewing?.id === item.id) setViewing(null)
      if (editing && editing !== 'create' && editing.id === item.id) setEditing(null)
      await loadKnowledge()
    } catch (err) {
      setKbError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <h1>知识库</h1>
        <p className="knowledge-hint">
          必选知识会注入 Agent 系统提示；可选知识需 Agent 按需加载。单条正文 ≤ 8000 字，启用必选合计 ≤
          6000 字，启用可选 ≤ 50 条。
        </p>
      </div>

      {kbLoading ? <p className="status">知识库加载中…</p> : null}
      {kbError ? <p className="status error">{kbError}</p> : null}

      <div className="form-actions">
        <button type="button" className="btn" disabled={kbSaving || Boolean(editing)} onClick={startCreate}>
          新建条目
        </button>
      </div>

      {!kbLoading && items.length === 0 && !editing ? (
        <p className="meta-line">暂无知识条目，点击「新建条目」添加。</p>
      ) : null}

      {items.length > 0 ? (
        <ul className="knowledge-list">
          {items.map((item) => (
            <li key={item.id} className="knowledge-row">
              <div className="knowledge-row-main">
                <span className="knowledge-title">{item.title}</span>
                <span
                  className={`knowledge-badge knowledge-badge--${item.mode}`}
                  title={item.mode === 'always' ? '必选：注入系统提示' : '可选：按需加载'}
                >
                  {item.mode === 'always' ? '必选' : '可选'}
                </span>
                <label className="knowledge-enabled">
                  <input
                    type="checkbox"
                    checked={item.enabled}
                    onChange={() => handleToggleEnabled(item)}
                  />
                  启用
                </label>
              </div>
              <div className="knowledge-row-actions">
                <button type="button" className="btn ghost" onClick={() => setViewing(item)}>
                  查看
                </button>
                <button type="button" className="btn ghost" onClick={() => startEdit(item)}>
                  编辑
                </button>
                <button type="button" className="btn ghost" onClick={() => handleDeleteKnowledge(item)}>
                  删除
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {viewing && !editing ? (
        <div className="knowledge-panel">
          <div className="knowledge-panel-head">
            <h3>{viewing.title}</h3>
            <button type="button" className="btn ghost" onClick={() => setViewing(null)}>
              关闭
            </button>
          </div>
          {viewing.description ? (
            <p className="meta-line">{viewing.description}</p>
          ) : (
            <p className="meta-line">（无描述）</p>
          )}
          <pre className="knowledge-body">{viewing.body || '（空正文）'}</pre>
        </div>
      ) : null}

      {editing ? (
        <div className="knowledge-panel">
          <h3>{editing === 'create' ? '新建知识条目' : '编辑知识条目'}</h3>
          <div className="strategy-grid knowledge-form">
            <label className="strategy-field">
              <span>标题（≤ 80 字）</span>
              <input
                className="input"
                maxLength={80}
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              />
            </label>
            <label className="strategy-field">
              <span>模式</span>
              <select
                className="input"
                value={form.mode}
                onChange={(e) =>
                  setForm((f) => ({ ...f, mode: e.target.value as KnowledgeMode }))
                }
              >
                <option value="always">必选（注入系统提示）</option>
                <option value="on_demand">可选（按需加载）</option>
              </select>
            </label>
            <label className="strategy-field knowledge-field-check">
              <span>启用</span>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
              />
            </label>
            <label className="strategy-field" style={{ gridColumn: '1 / -1' }}>
              <span>
                描述{form.mode === 'on_demand' ? '（必填，≤ 200 字）' : '（可选，≤ 200 字）'}
              </span>
              <input
                className="input"
                maxLength={200}
                required={form.mode === 'on_demand'}
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              />
            </label>
            <label className="strategy-field" style={{ gridColumn: '1 / -1' }}>
              <span>正文（≤ 8000 字）</span>
              <textarea
                className="input knowledge-textarea"
                maxLength={8000}
                rows={10}
                value={form.body}
                onChange={(e) => setForm((f) => ({ ...f, body: e.target.value }))}
              />
            </label>
          </div>
          <div className="form-actions">
            <button
              type="button"
              className="btn"
              disabled={
                kbSaving ||
                !form.title.trim() ||
                (form.mode === 'on_demand' && !form.description.trim())
              }
              onClick={handleSaveKnowledge}
            >
              {kbSaving ? '保存中…' : '保存'}
            </button>
            <button type="button" className="btn ghost" disabled={kbSaving} onClick={cancelKnowledgeForm}>
              取消
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
```

- [ ] **Step 2: 收窄 `AgentSettingsPage.tsx`**

删除全部知识库相关 import、`EMPTY_FORM`、`itemToInput`、kb state、`loadKnowledge`、知识库 handlers、以及 JSX 中从 `<hr className="knowledge-divider" />` 到文件末尾知识库区块。

保留 DeepSeek 相关逻辑。将 hero 文案改为：

```tsx
<p>
  配置 DeepSeek API Key。Key 存于服务端加密，不回显完整密钥。文档见{' '}
  <a
    className="text-link"
    href="https://api-docs.deepseek.com/zh-cn/"
    target="_blank"
    rel="noreferrer"
  >
    DeepSeek API
  </a>
  。
</p>
```

最终文件顶部 import 仅为：

```tsx
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  clearLlmSettings,
  fetchLlmSettings,
  saveLlmSettings,
  type LlmSettings,
} from '../agentApi'
```

页面在 DeepSeek「返回助手」链接后结束 `</section>`，不再有知识库 UI。

- [ ] **Step 3: Commit**

```bash
git add frontend-advisor/src/pages/KnowledgePage.tsx frontend-advisor/src/pages/AgentSettingsPage.tsx
git commit -m "$(cat <<'EOF'
feat: extract knowledge base into dedicated page

EOF
)"
```

---

### Task 2: 更新导航、路由与 App 测试

**Files:**
- Modify: `frontend-advisor/src/App.tsx`
- Modify: `frontend-advisor/src/App.test.tsx`
- Test: `frontend-advisor/src/App.test.tsx`

**Interfaces:**
- Consumes: `KnowledgePage` default export from `./pages/KnowledgePage`
- Produces: NavLink「知识库」→ `/agent/knowledge`；Route `/agent/knowledge`

- [ ] **Step 1: 写失败测试（更新 `App.test.tsx`）**

替换现有「Agent 导航包含投委会…」用例，并增加知识库路由用例。完整测试文件目标内容：

```tsx
import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import App from './App'

const authState = vi.hoisted(() => ({ token: null as string | null }))

vi.mock('./auth', () => ({
  AUTH_CHANGED_EVENT: 'advisor-auth-changed',
  getToken: () => authState.token,
  getUser: () => ({ id: 'u1', username: 'tester' }),
  fetchMe: vi.fn(),
  clearSession: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  setSession: vi.fn(),
}))

vi.mock('./committee/CommitteePage', () => ({
  default: () => <h1>投委会实时工作台</h1>,
}))

vi.mock('./pages/KnowledgePage', () => ({
  default: () => <h1>知识库</h1>,
}))

beforeEach(() => {
  localStorage.clear()
  authState.token = null
})

it('收到统一认证变更事件后立即返回登录页', async () => {
  render(
    <MemoryRouter initialEntries={['/agent/committee']}>
      <App />
    </MemoryRouter>,
  )
  window.dispatchEvent(new Event('advisor-auth-changed'))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument(),
  )
  expect(screen.queryByRole('link', { name: '投委会' })).not.toBeInTheDocument()
})

it('Agent 导航包含知识库且不含投委会；直链投委会仍可用', () => {
  const { container } = render(
    <MemoryRouter initialEntries={['/agent/committee']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.queryByRole('link', { name: '投委会' })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: '知识库' })).toHaveAttribute(
    'href',
    '/agent/knowledge',
  )
  expect(screen.getByRole('heading', { name: '投委会实时工作台' })).toBeInTheDocument()
  expect(container.querySelector('.app-shell')).toHaveClass('app-shell--agent-chat')
})

it('知识库路由渲染知识库页且非 chat shell', () => {
  const { container } = render(
    <MemoryRouter initialEntries={['/agent/knowledge']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: '知识库' })).toBeInTheDocument()
  expect(container.querySelector('.app-shell')).toHaveClass('app-shell--agent')
  expect(container.querySelector('.app-shell')).not.toHaveClass('app-shell--agent-chat')
})
```

注意：认证用例在 `token === null` 时会因 `getUser` mock 仍返回用户而进入已登录壳——与现有测试一致，不要改 auth mock 行为。

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd frontend-advisor && npx vitest run src/App.test.tsx
```

Expected: FAIL（找不到「知识库」链接 / 路由未注册）

- [ ] **Step 3: 更新 `App.tsx`**

1. 增加 import：

```tsx
import KnowledgePage from './pages/KnowledgePage'
```

2. Agent `nav` 中把：

```tsx
<NavLink to="/agent/committee">投委会</NavLink>
```

改为：

```tsx
<NavLink to="/agent/knowledge">知识库</NavLink>
```

3. 在 Routes 中保留 committee 路由，并在其旁增加：

```tsx
<Route path="/agent/knowledge" element={<KnowledgePage />} />
```

建议放在 `/agent/committee` 路由之后、`/agent/strategy` 之前。不要删除：

```tsx
<Route path="/agent/committee" element={<CommitteePage />} />
```

4. 不要修改 `isAgentChat` 判断（继续只含 `/agent` 与 `/agent/committee`）。

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
cd frontend-advisor && npx vitest run src/App.test.tsx
```

Expected: PASS（全部用例）

- [ ] **Step 5: Commit**

```bash
git add frontend-advisor/src/App.tsx frontend-advisor/src/App.test.tsx
git commit -m "$(cat <<'EOF'
feat: swap agent nav from committee to knowledge tab

EOF
)"
```

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|-----------|------|
| 导航隐藏投委会、显示知识库 | Task 2 |
| `/agent/knowledge` 独立页 | Task 1 + Task 2 |
| 设置页移除知识库 | Task 1 |
| `/agent/committee` 直链保留 | Task 2 |
| `isAgentChat` 不含 knowledge | Task 2（明确不改） |
| App 测试更新 | Task 2 |
| 不改后端 / committee 业务 | 全任务约束 |
