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
import {
  PROVIDER_META,
  SLOT_ROWS,
  clampSlotModel,
  filterProviderModels,
} from '../llmSettingsUi'

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
  const [activeProvider, setActiveProvider] = useState<LlmProviderId>('deepseek')
  const [modelQuery, setModelQuery] = useState('')

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

  const activeMeta =
    PROVIDER_META.find((p) => p.id === activeProvider) || PROVIDER_META[0]
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

  function slotModelForProvider(provider: LlmProviderId): string {
    const def = settings?.providers[provider].default_model || ''
    const allowed = enabled[provider] || []
    return allowed.includes(def) ? def : allowed[0] || def
  }

  function changeSlotProvider(slotId: LlmSlotId, provider: LlmProviderId) {
    const model = slotModelForProvider(provider)
    setSlots((old) =>
      old ? { ...old, [slotId]: { provider, model } } : old,
    )
  }

  function applyAllSlots(provider: LlmProviderId) {
    const model = slotModelForProvider(provider)
    const label =
      PROVIDER_META.find((p) => p.id === provider)?.label || provider
    setSlots((old) => {
      const next = { ...(old || {}) } as LlmSettings['slots']
      for (const row of SLOT_ROWS) {
        next[row.id] = { provider, model }
      }
      return next
    })
    setError(null)
    setMsg(`已将全部功能模块改为 ${label}，请点底部保存。`)
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
      const packedEnabled: NonNullable<
        Parameters<typeof saveLlmSettings>[0]['enabled_models']
      > = {}
      for (const id of configuredProviders) {
        packedEnabled[id] = enabled[id] || []
      }
      const body: Parameters<typeof saveLlmSettings>[0] = {
        enabled_models: packedEnabled,
        web_research_enabled: webResearchEnabled,
        tavily_enabled: tavilyEnabled,
      }
      if (slots) {
        const packed: NonNullable<Parameters<typeof saveLlmSettings>[0]['slots']> =
          {}
        for (const row of SLOT_ROWS) {
          const val = slots[row.id]
          if (!val) continue
          const allow = packedEnabled[val.provider] || enabled[val.provider] || []
          packed[row.id] = {
            provider: val.provider,
            model: clampSlotModel(val.model, allow),
          }
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
                {activePub.available_models.length
                  ? `已选 ${enabled[activeMeta.id].length} / 共 ${activeAvailable.length}`
                  : `已选 ${enabled[activeMeta.id].length}（目录未同步，请刷新模型）`}
              </span>
            </div>
            <input
              className="input"
              type="search"
              placeholder="搜索模型"
              aria-label={`搜索 ${activeMeta.label} 模型`}
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

      <h2 className="section-title">功能模块</h2>
      {!settings?.configured ? (
        <p className="meta-line">请先配置至少一个模型提供方。</p>
      ) : (
        <div className="table-wrap llm-settings-slots">
          {configuredProviders.length >= 2 ? (
            <div className="llm-settings-slot-toolbar">
              <span className="llm-settings-slot-toolbar-label">
                全部使用
              </span>
              {configuredProviders.map((id) => {
                const label =
                  PROVIDER_META.find((p) => p.id === id)?.label || id
                return (
                  <button
                    key={id}
                    type="button"
                    className="btn ghost"
                    disabled={saving}
                    aria-label={`全部使用${label}`}
                    onClick={() => applyAllSlots(id)}
                  >
                    {label}
                  </button>
                )
              })}
            </div>
          ) : null}
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
                        value={clampSlotModel(val?.model, models)}
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

      <h2 className="section-title">联网搜索</h2>
      <p className="meta-line">
        可同时开启或同时关闭。DeepSeek 联网综述复用 DeepSeek Key；Tavily 需自备 API Key。
      </p>
      <p className="meta-line">
        精读网页在任一联网能力开启时可用；困难页面会自动增强抓取。
      </p>
      <div className="home-tile llm-settings-web">
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
        {error ? <p className="status error">{error}</p> : null}
        {msg ? <p className="status ok">{msg}</p> : null}
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
