import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  clearLlmProvider,
  clearTavilySettings,
  fetchLlmSettings,
  refreshLlmProviderModels,
  saveLlmProvider,
  saveLlmSettings,
  type LlmProviderId,
  type LlmSettings,
  type LlmSlotId,
} from '../agentApi'

const PROVIDER_META: {
  id: LlmProviderId
  label: string
  docs: string
  docsLabel: string
}[] = [
  {
    id: 'deepseek',
    label: 'DeepSeek',
    docs: 'https://api-docs.deepseek.com/zh-cn/',
    docsLabel: 'DeepSeek API',
  },
  {
    id: 'kimi',
    label: 'Kimi',
    docs: 'https://platform.kimi.com/docs/api/overview',
    docsLabel: 'Kimi API',
  },
  {
    id: 'qwen',
    label: '千问',
    docs: 'https://platform.qianwenai.com/docs/developer-guides/getting-started/text-generation-models',
    docsLabel: '千问 API',
  },
]

const SLOT_ROWS: { id: LlmSlotId; label: string }[] = [
  { id: 'agent', label: '主 Agent 对话' },
  { id: 'paper', label: '模拟盘' },
  { id: 'home', label: '首页解读' },
  { id: 'monitor', label: '定时任务' },
  { id: 'policy', label: '政策雷达' },
  { id: 'limitup', label: '打板晋级' },
  { id: 'committee_quick', label: '委员会·快速' },
  { id: 'committee_deep', label: '委员会·深度' },
]

function emptyProvider(defaultModel: string) {
  return {
    configured: false,
    key_hint: null,
    last_validated_at: null,
    available_models: [] as { id: string }[],
    enabled_models: [] as string[],
    default_model: defaultModel,
    models_synced_at: null,
  }
}

function applySettings(
  s: LlmSettings,
  setEnabled: (v: Record<LlmProviderId, string[]>) => void,
  setSlots: (v: LlmSettings['slots']) => void,
  setWebResearchEnabled: (v: boolean) => void,
  setTavilyEnabled: (v: boolean) => void,
) {
  setEnabled({
    deepseek: [...(s.providers.deepseek.enabled_models || [])],
    kimi: [...(s.providers.kimi.enabled_models || [])],
    qwen: [...(s.providers.qwen.enabled_models || [])],
  })
  setSlots({ ...s.slots })
  setWebResearchEnabled(s.web_research_enabled !== false)
  setTavilyEnabled(Boolean(s.tavily_enabled))
}

export default function AgentSettingsPage() {
  const [settings, setSettings] = useState<LlmSettings | null>(null)
  const [keys, setKeys] = useState<Record<LlmProviderId, string>>({
    deepseek: '',
    kimi: '',
    qwen: '',
  })
  const [enabled, setEnabled] = useState<Record<LlmProviderId, string[]>>({
    deepseek: [],
    kimi: [],
    qwen: [],
  })
  const [slots, setSlots] = useState<LlmSettings['slots'] | null>(null)
  const [webResearchEnabled, setWebResearchEnabled] = useState(true)
  const [tavilyEnabled, setTavilyEnabled] = useState(false)
  const [tavilyKey, setTavilyKey] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    fetchLlmSettings()
      .then((s) => {
        setSettings(s)
        applySettings(
          s,
          setEnabled,
          setSlots,
          setWebResearchEnabled,
          setTavilyEnabled,
        )
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [])

  const configuredProviders = useMemo(() => {
    if (!settings) return [] as LlmProviderId[]
    return PROVIDER_META.map((p) => p.id).filter(
      (id) => settings.providers[id]?.configured,
    )
  }, [settings])

  function onSettings(s: LlmSettings) {
    setSettings(s)
    applySettings(s, setEnabled, setSlots, setWebResearchEnabled, setTavilyEnabled)
  }

  async function handleSaveProvider(id: LlmProviderId) {
    const key = keys[id].trim()
    if (!key) {
      setError(`请先填写 ${PROVIDER_META.find((p) => p.id === id)?.label} API Key`)
      return
    }
    setBusy(`save-${id}`)
    setError(null)
    setMsg(null)
    try {
      const s = await saveLlmProvider(id, key)
      onSettings(s)
      setKeys((prev) => ({ ...prev, [id]: '' }))
      setMsg(`${PROVIDER_META.find((p) => p.id === id)?.label} 已保存。`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  async function handleClearProvider(id: LlmProviderId) {
    const label = PROVIDER_META.find((p) => p.id === id)?.label
    if (!window.confirm(`清除 ${label} API Key？占用该提供方的模块会改到其它已配置提供方。`)) {
      return
    }
    setBusy(`clear-${id}`)
    setError(null)
    try {
      const s = await clearLlmProvider(id)
      onSettings(s)
      setMsg(`已清除 ${label} Key。`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  async function handleRefresh(id: LlmProviderId) {
    setBusy(`refresh-${id}`)
    setError(null)
    try {
      const s = await refreshLlmProviderModels(id)
      onSettings(s)
      setMsg('模型列表已刷新。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  function toggleEnabled(id: LlmProviderId, modelId: string, checked: boolean) {
    setEnabled((prev) => {
      const cur = prev[id] || []
      if (checked) {
        if (cur.includes(modelId)) return prev
        return { ...prev, [id]: [...cur, modelId] }
      }
      if (cur.length <= 1) {
        setError('至少勾选一个模型')
        return prev
      }
      const next = cur.filter((m) => m !== modelId)
      setSlots((old) => {
        if (!old || !settings) return old
        const fallback =
          settings.providers[id].default_model === modelId
            ? next[0]
            : settings.providers[id].default_model
        const use = next.includes(fallback) ? fallback : next[0]
        const copy = { ...old }
        for (const row of SLOT_ROWS) {
          const val = copy[row.id]
          if (val && val.provider === id && val.model === modelId) {
            copy[row.id] = { provider: id, model: use }
          }
        }
        return copy
      })
      return { ...prev, [id]: next }
    })
  }

  function changeSlotProvider(slotId: LlmSlotId, provider: LlmProviderId) {
    if (!settings) return
    const def = settings.providers[provider].default_model
    const allowed = enabled[provider]
    const model = allowed.includes(def) ? def : allowed[0] || def
    setSlots((old) =>
      old ? { ...old, [slotId]: { provider, model } } : old,
    )
  }

  async function handleSaveModules() {
    if (
      tavilyEnabled &&
      !settings?.tavily_configured &&
      !tavilyKey.trim()
    ) {
      setError('开启 Tavily 前请先填写有效的 API Key')
      return
    }
    if (!settings?.configured) {
      setError('请先配置至少一个模型提供方')
      return
    }
    setBusy('modules')
    setError(null)
    setMsg(null)
    try {
      const body: Parameters<typeof saveLlmSettings>[0] = {
        enabled_models: enabled,
        web_research_enabled: webResearchEnabled,
        tavily_enabled: tavilyEnabled,
      }
      if (slots) {
        const packed: NonNullable<Parameters<typeof saveLlmSettings>[0]['slots']> =
          {}
        for (const row of SLOT_ROWS) {
          const val = slots[row.id]
          if (val) packed[row.id] = val
        }
        body.slots = packed
      }
      if (tavilyKey.trim()) body.tavily_api_key = tavilyKey.trim()
      const s = await saveLlmSettings(body)
      onSettings(s)
      setTavilyKey('')
      setMsg('已保存。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  async function handleClearTavily() {
    if (!window.confirm('清除 Tavily API Key，并关闭 Tavily 搜索？')) return
    setBusy('tavily')
    setError(null)
    try {
      const s = await clearTavilySettings()
      onSettings(s)
      setTavilyEnabled(false)
      setTavilyKey('')
      setMsg('已清除 Tavily Key。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const agentProvider = slots?.agent?.provider
  const researchDisabled = agentProvider !== 'deepseek'
  const saving = Boolean(busy)

  return (
    <section className="page">
      <div className="page-hero">
        <p>
          配置 DeepSeek、Kimi、千问的 API Key。Key 存于服务端加密，不回显完整密钥。各功能模块可独立选择提供方和模型。
        </p>
      </div>

      {loading ? <p className="status">加载中…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {msg ? <p className="status ok">{msg}</p> : null}

      <h2 className="section-title">模型提供方</h2>
      {PROVIDER_META.map((meta) => {
        const pub = settings?.providers[meta.id] || emptyProvider(meta.id === 'kimi' ? 'kimi-k2.6' : meta.id === 'qwen' ? 'qwen3.7-plus' : 'deepseek-v4-flash')
        const available = pub.available_models.length
          ? pub.available_models
          : enabled[meta.id].map((id) => ({ id }))
        return (
          <div key={meta.id} className="strategy-grid" style={{ maxWidth: '36rem', marginBottom: '1.25rem' }}>
            <h3 className="section-title">{meta.label}</h3>
            <p className="meta-line">
              文档见{' '}
              <a className="text-link" href={meta.docs} target="_blank" rel="noreferrer">
                {meta.docsLabel}
              </a>
              {pub.configured ? ` · 已配置（${pub.key_hint}）` : ' · 未配置'}
            </p>
            <label className="strategy-field">
              <span>API Key</span>
              <input
                className="input mono"
                type="password"
                autoComplete="off"
                placeholder={pub.configured ? '输入新 Key 以覆盖' : 'sk-…'}
                value={keys[meta.id]}
                onChange={(e) =>
                  setKeys((prev) => ({ ...prev, [meta.id]: e.target.value }))
                }
              />
            </label>
            <div className="form-actions">
              <button
                type="button"
                className="btn"
                disabled={saving || !keys[meta.id].trim()}
                onClick={() => handleSaveProvider(meta.id)}
              >
                {busy === `save-${meta.id}` ? '保存中…' : '保存并校验'}
              </button>
              {pub.configured ? (
                <button
                  type="button"
                  className="btn ghost"
                  disabled={saving}
                  onClick={() => handleClearProvider(meta.id)}
                >
                  清除 {meta.label} Key
                </button>
              ) : null}
              {pub.configured ? (
                <button
                  type="button"
                  className="btn ghost"
                  disabled={saving}
                  onClick={() => handleRefresh(meta.id)}
                >
                  {busy === `refresh-${meta.id}` ? '刷新中…' : '刷新模型'}
                </button>
              ) : null}
            </div>
            {pub.configured ? (
              <div className="strategy-field">
                <span>可用模型</span>
                {available.length === 0 ? (
                  <p className="meta-line">模型列表为空，请点击刷新模型。</p>
                ) : (
                  available.map((m) => (
                    <label key={m.id} className="meta-line" style={{ display: 'block' }}>
                      <input
                        type="checkbox"
                        checked={enabled[meta.id].includes(m.id)}
                        onChange={(e) =>
                          toggleEnabled(meta.id, m.id, e.target.checked)
                        }
                      />{' '}
                      {m.id}
                    </label>
                  ))
                )}
              </div>
            ) : null}
          </div>
        )
      })}

      <h2 className="section-title">功能模块</h2>
      {!settings?.configured ? (
        <p className="meta-line">请先配置至少一个模型提供方。</p>
      ) : (
        <div className="strategy-grid" style={{ maxWidth: '36rem' }}>
          {SLOT_ROWS.map((row) => {
            const val = slots?.[row.id]
            const pid = (val?.provider || configuredProviders[0]) as LlmProviderId
            const models = enabled[pid] || []
            return (
              <div key={row.id} className="strategy-field">
                <span>{row.label}</span>
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
              </div>
            )
          })}
        </div>
      )}

      <h2 className="section-title">联网搜索</h2>
      <p className="meta-line">
        可同时开启或同时关闭。DeepSeek 联网综述复用 DeepSeek Key；Tavily 需自备 API Key。
      </p>
      <p className="meta-line">
        精读网页在任一联网能力开启时可用；困难页面会自动增强抓取。
      </p>
      <div className="strategy-grid" style={{ maxWidth: '28rem' }}>
        <label className="strategy-field">
          <span>
            <input
              type="checkbox"
              checked={webResearchEnabled}
              disabled={researchDisabled}
              onChange={(e) => setWebResearchEnabled(e.target.checked)}
            />{' '}
            DeepSeek 联网综述（web_research）
          </span>
          <span className="meta-line">
            {researchDisabled
              ? '仅主 Agent 使用 DeepSeek 时可用'
              : '默认开启；使用固定轻量模型做服务端搜索。'}
          </span>
        </label>
        <label className="strategy-field">
          <span>
            <input
              type="checkbox"
              checked={tavilyEnabled}
              onChange={(e) => setTavilyEnabled(e.target.checked)}
            />{' '}
            Tavily 搜索（web_search）
          </span>
          <span className="meta-line">
            在{' '}
            <a
              className="text-link"
              href="https://app.tavily.com/home"
              target="_blank"
              rel="noreferrer"
            >
              Tavily 控制台
            </a>
            {' '}获取 API Key；用法见{' '}
            <a
              className="text-link"
              href="https://docs.tavily.com/"
              target="_blank"
              rel="noreferrer"
            >
              文档
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
          disabled={saving || !settings?.configured}
          onClick={handleSaveModules}
        >
          {busy === 'modules' ? '保存中…' : '保存'}
        </button>
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
