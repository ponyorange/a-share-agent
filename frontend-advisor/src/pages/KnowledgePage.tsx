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
        <p className="knowledge-hint">
          必选知识会注入 Agent 系统提示；可选知识需 Agent 按需加载。单条正文 ≤ 8000 字，启用必选合计 ≤
          6000 字，启用可选 ≤ 50 条。
        </p>
      </div>

      {kbLoading ? <p className="status">知识库加载中…</p> : null}
      {kbError ? <p className="status error">{kbError}</p> : null}

      <div className="form-actions">
        <button
          type="button"
          className="btn"
          disabled={kbSaving || Boolean(editing)}
          onClick={startCreate}
        >
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
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => handleDeleteKnowledge(item)}
                >
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
                placeholder="例：用户询问仓位管理或止损规则时使用"
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              />
              <span className="meta-line knowledge-hint">
                {form.mode === 'on_demand'
                  ? '写清触发场景与适用问题，Agent 靠描述判断何时加载正文。避免空泛词（如「笔记」），写具体条件。'
                  : '建议写清主题与适用场景；可选知识时描述会进入目录供 Agent 检索。'}
              </span>
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
            <button
              type="button"
              className="btn ghost"
              disabled={kbSaving}
              onClick={cancelKnowledgeForm}
            >
              取消
            </button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
