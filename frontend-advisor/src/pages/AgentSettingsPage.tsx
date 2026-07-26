import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  clearLlmSettings,
  clearTavilySettings,
  fetchLlmSettings,
  saveLlmSettings,
  type LlmSettings,
} from '../agentApi'

export default function AgentSettingsPage() {
  const navigate = useNavigate()
  const [settings, setSettings] = useState<LlmSettings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('deepseek-v4-flash')
  const [webResearchEnabled, setWebResearchEnabled] = useState(true)
  const [tavilyEnabled, setTavilyEnabled] = useState(false)
  const [tavilyKey, setTavilyKey] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    fetchLlmSettings()
      .then((s) => {
        setSettings(s)
        if (s.model) setModel(s.model)
        setWebResearchEnabled(s.web_research_enabled !== false)
        setTavilyEnabled(Boolean(s.tavily_enabled))
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave() {
    setSaving(true)
    setError(null)
    setMsg(null)
    if (
      tavilyEnabled &&
      !settings?.tavily_configured &&
      !tavilyKey.trim()
    ) {
      setError('开启 Tavily 前请先填写有效的 API Key')
      setSaving(false)
      return
    }
    if (!settings?.configured && !apiKey.trim()) {
      setError('请先填写 DeepSeek API Key')
      setSaving(false)
      return
    }
    try {
      const body: Parameters<typeof saveLlmSettings>[0] = {
        model,
        web_research_enabled: webResearchEnabled,
        tavily_enabled: tavilyEnabled,
      }
      if (apiKey.trim()) body.api_key = apiKey.trim()
      if (tavilyKey.trim()) body.tavily_api_key = tavilyKey.trim()
      const s = await saveLlmSettings(body)
      setSettings(s)
      setApiKey('')
      setTavilyKey('')
      setWebResearchEnabled(s.web_research_enabled !== false)
      setTavilyEnabled(Boolean(s.tavily_enabled))
      setMsg('已保存。')
      if (s.configured) {
        setTimeout(() => navigate('/agent'), 600)
      }
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
      setMsg('已清除 DeepSeek Key。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function handleClearTavily() {
    if (!window.confirm('清除 Tavily API Key，并关闭 Tavily 搜索？')) return
    setSaving(true)
    setError(null)
    try {
      const s = await clearTavilySettings()
      setSettings(s)
      setTavilyEnabled(false)
      setTavilyKey('')
      setMsg('已清除 Tavily Key。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const canSave =
    Boolean(apiKey.trim()) ||
    Boolean(settings?.configured) ||
    Boolean(tavilyKey.trim())

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

      <h2 className="section-title">联网搜索</h2>
      <p className="meta-line">
        可同时开启或同时关闭。DeepSeek 联网综述复用上方 Key；Tavily 需自备 API Key。
      </p>
      <div className="strategy-grid" style={{ maxWidth: '28rem' }}>
        <label className="strategy-field">
          <span>
            <input
              type="checkbox"
              checked={webResearchEnabled}
              onChange={(e) => setWebResearchEnabled(e.target.checked)}
            />{' '}
            DeepSeek 联网综述（web_research）
          </span>
          <span className="meta-line">默认开启；使用固定轻量模型做服务端搜索。</span>
        </label>
        <label className="strategy-field">
          <span>
            <input
              type="checkbox"
              checked={tavilyEnabled}
              onChange={(e) => setTavilyEnabled(e.target.checked)}
            />{' '}
            Tavily 搜索 + 网页抓取
          </span>
          <span className="meta-line">
            文档见{' '}
            <a
              className="text-link"
              href="https://docs.tavily.com/"
              target="_blank"
              rel="noreferrer"
            >
              Tavily
            </a>
            。
          </span>
        </label>
        {tavilyEnabled ? (
          <label className="strategy-field">
            <span>Tavily API Key</span>
            <input
              className="input mono"
              type="password"
              autoComplete="off"
              placeholder={
                settings?.tavily_configured
                  ? `已配置（${settings.tavily_key_hint}），输入新 Key 以覆盖`
                  : 'tvly-…'
              }
              value={tavilyKey}
              onChange={(e) => setTavilyKey(e.target.value)}
            />
          </label>
        ) : null}
      </div>

      <div className="form-actions">
        <button
          type="button"
          className="btn"
          disabled={saving || !canSave}
          onClick={handleSave}
        >
          {saving ? '保存中…' : '保存'}
        </button>
        {settings?.configured ? (
          <button type="button" className="btn ghost" disabled={saving} onClick={handleClear}>
            清除 DeepSeek Key
          </button>
        ) : null}
        {settings?.tavily_configured ? (
          <button
            type="button"
            className="btn ghost"
            disabled={saving}
            onClick={handleClearTavily}
          >
            清除 Tavily Key
          </button>
        ) : null}
        <Link className="text-link" to="/agent">
          返回助手
        </Link>
      </div>
    </section>
  )
}
