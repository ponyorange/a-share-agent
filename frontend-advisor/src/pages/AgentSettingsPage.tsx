import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  clearLlmSettings,
  fetchLlmSettings,
  saveLlmSettings,
  type LlmSettings,
} from '../agentApi'

export default function AgentSettingsPage() {
  const navigate = useNavigate()
  const [settings, setSettings] = useState<LlmSettings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('deepseek-v4-flash')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    fetchLlmSettings()
      .then((s) => {
        setSettings(s)
        if (s.model) setModel(s.model)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [])

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
    </section>
  )
}
