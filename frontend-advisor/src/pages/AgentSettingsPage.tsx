import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  clearLlmSettings,
  createKnowledge,
  deleteKnowledge,
  fetchLlmSettings,
  listKnowledge,
  saveLlmSettings,
  updateKnowledge,
  type KnowledgeInput,
  type KnowledgeItem,
  type KnowledgeMode,
  type LlmSettings,
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

export default function AgentSettingsPage() {
  const navigate = useNavigate()
  const [settings, setSettings] = useState<LlmSettings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('deepseek-v4-flash')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

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
    fetchLlmSettings()
      .then((s) => {
        setSettings(s)
        if (s.model) setModel(s.model)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
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

  async function handleSave() {
    setSaving(true)
    setError(null)
    setMsg(null)
    try {
      const s = await saveLlmSettings({ api_key: apiKey, model })
      setSettings(s)
      setApiKey('')
      setMsg('已保存并通过 DeepSeek 校验，可以开始使用 Agent。')
      setTimeout(() => navigate('/agent'), 600)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function handleClear() {
    if (!window.confirm('清除 DeepSeek API Key？清除后需重新配置才能使用 Agent。')) return
    setSaving(true)
    setError(null)
    try {
      const s = await clearLlmSettings()
      setSettings(s)
      setMsg('已清除。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <h1>Agent 设置</h1>
        <p>
          配置 DeepSeek API Key 与个人知识库。Key 存于服务端加密，不回显完整密钥。文档见{' '}
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
      </div>

      {loading ? <p className="status">加载中…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {msg ? <p className="status ok">{msg}</p> : null}

      <h2 className="section-title">DeepSeek 配置</h2>

      {settings ? (
        <p className="meta-line">
          状态：{settings.configured ? `已配置（${settings.key_hint}）` : '未配置'}
          {settings.model ? ` · 模型 ${settings.model}` : ''}
          {settings.last_validated_at ? ` · 校验于 ${settings.last_validated_at}` : ''}
        </p>
      ) : null}

      <div className="strategy-grid" style={{ maxWidth: '28rem' }}>
        <label className="strategy-field">
          <span>API Key</span>
          <input
            className="input mono"
            type="password"
            autoComplete="off"
            placeholder={settings?.configured ? '输入新 Key 以覆盖' : 'sk-…'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </label>
        <label className="strategy-field">
          <span>模型</span>
          <select
            className="input"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            <option value="deepseek-v4-flash">deepseek-v4-flash</option>
            <option value="deepseek-v4-pro">deepseek-v4-pro</option>
          </select>
        </label>
      </div>

      <div className="form-actions">
        <button
          type="button"
          className="btn"
          disabled={saving || !apiKey.trim()}
          onClick={handleSave}
        >
          {saving ? '校验中…' : '保存并校验'}
        </button>
        {settings?.configured ? (
          <button type="button" className="btn ghost" disabled={saving} onClick={handleClear}>
            清除 Key
          </button>
        ) : null}
        <Link className="text-link" to="/agent">
          返回助手
        </Link>
      </div>

      <hr className="knowledge-divider" />

      <h2 className="section-title">知识库</h2>
      <p className="meta-line knowledge-hint">
        必选知识会注入 Agent 系统提示；可选知识需 Agent 按需加载。单条正文 ≤ 8000 字，启用必选合计 ≤
        6000 字，启用可选 ≤ 50 条。
      </p>

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
