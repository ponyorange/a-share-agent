import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchPolicyWatchItems,
  fetchPolicyWatchPresets,
  fetchPolicyWatchSettings,
  savePolicyWatchSettings,
  type PolicyWatchItem,
  type PolicyWatchPreset,
  type PolicyWatchScanMode,
  type PolicyWatchSensitivity,
  type PolicyWatchSettings,
} from '../api'

const EMPTY_HINT =
  '勾选来源并开启后，新文章会出现在这里；只有可能影响股价的才发邮件。刚开启不会把旧闻刷进来。'

const DIR_LABEL: Record<string, string> = {
  up: '利好',
  down: '利空',
  mixed: '分化',
  unclear: '影响不明',
}

const NOTIFY_LABEL: Record<string, string> = {
  sent: '已发信',
  skipped: '仅收录',
  failed: '发信失败',
}

function defaultSettings(): PolicyWatchSettings {
  return {
    enabled: false,
    sensitivity: 'medium',
    scan_mode: 'always',
    interval_trading_min: 15,
    interval_offhours_min: 60,
    preset_ids: ['gov_zhengce', 'scio_news'],
    custom_sources: [],
  }
}

function formatTs(raw?: string | null): string {
  if (!raw) return '—'
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return raw
  return d.toLocaleString('zh-CN', { hour12: false, month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function dirClass(direction?: string): string {
  if (direction === 'up') return 'up'
  if (direction === 'down') return 'down'
  return 'muted'
}

export default function PolicyWatchPage() {
  const [settings, setSettings] = useState<PolicyWatchSettings | null>(null)
  const [presets, setPresets] = useState<PolicyWatchPreset[]>([])
  const [items, setItems] = useState<PolicyWatchItem[]>([])
  const [filter, setFilter] = useState<'all' | 'emailed' | 'inbox'>('all')
  const [customUrl, setCustomUrl] = useState('')
  const [tradingDraft, setTradingDraft] = useState('15')
  const [offhoursDraft, setOffhoursDraft] = useState('60')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const applySettings = useCallback((s: PolicyWatchSettings) => {
    setSettings(s)
    setTradingDraft(String(s.interval_trading_min))
    setOffhoursDraft(String(s.interval_offhours_min))
  }, [])

  const load = useCallback(async () => {
    const [s, p, inbox] = await Promise.all([
      fetchPolicyWatchSettings(),
      fetchPolicyWatchPresets(),
      fetchPolicyWatchItems({ filter, limit: 30 }),
    ])
    applySettings(s)
    setPresets(p.presets || [])
    setItems(inbox.items || [])
  }, [applySettings, filter])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    load()
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    const timer = window.setInterval(() => {
      void load().catch(() => undefined)
    }, 10000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [load])

  async function patch(body: Partial<PolicyWatchSettings>) {
    setSaving(true)
    setError(null)
    try {
      const next = await savePolicyWatchSettings(body)
      applySettings(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const current = settings || defaultSettings()

  return (
    <section className="page policy-watch">
      <div className="page-hero">
        <h1>政策雷达</h1>
        <p>
          监控预置官方栏目和你粘贴的列表页。新文章进收件箱；只有可能影响股价的才发邮件。
        </p>
      </div>

      {error ? <p className="status error">{error}</p> : null}
      {loading && !settings ? <p className="status">正在加载…</p> : null}

      <div className="policy-watch-status">
        <span className={current.enabled ? 'policy-watch-pill is-on' : 'policy-watch-pill'}>
          {current.enabled ? '雷达开启' : '雷达关闭'}
        </span>
        {current.email_verified ? (
          <span className="policy-watch-pill">邮箱 {current.notify_email}</span>
        ) : (
          <Link className="policy-watch-pill is-warn" to="/account">
            邮箱未绑定
          </Link>
        )}
        {current.llm_configured ? (
          <span className="policy-watch-pill">DeepSeek 已配置</span>
        ) : (
          <Link className="policy-watch-pill is-warn" to="/agent/settings">
            未配置 DeepSeek
          </Link>
        )}
      </div>

      <div className="policy-watch-setup">
        <section className="home-tile policy-watch-panel">
          <h2 className="home-tile-title">扫描设置</h2>
          <label className="policy-watch-switch">
            <input
              type="checkbox"
              checked={current.enabled}
              disabled={saving}
              onChange={(e) => void patch({ enabled: e.target.checked })}
            />
            <span>开启雷达</span>
          </label>
          <div className="policy-watch-fields">
            <label className="strategy-field">
              <span>灵敏度</span>
              <select
                className="input"
                value={current.sensitivity}
                disabled={saving}
                onChange={(e) =>
                  void patch({ sensitivity: e.target.value as PolicyWatchSensitivity })
                }
              >
                <option value="low">低 · 重大政策</option>
                <option value="medium">中 · 默认</option>
                <option value="high">高 · 更多提醒</option>
              </select>
            </label>
            <label className="strategy-field">
              <span>扫描时段</span>
              <select
                className="input"
                value={current.scan_mode}
                disabled={saving}
                onChange={(e) =>
                  void patch({ scan_mode: e.target.value as PolicyWatchScanMode })
                }
              >
                <option value="always">全天</option>
                <option value="trading_only">仅交易时间</option>
                <option value="offhours_only">仅非交易时间</option>
              </select>
            </label>
            <label className="strategy-field">
              <span>交易间隔（分钟）</span>
              <input
                className="input"
                type="number"
                min={5}
                max={180}
                disabled={saving || current.scan_mode === 'offhours_only'}
                value={tradingDraft}
                onChange={(e) => setTradingDraft(e.target.value)}
                onBlur={() => {
                  const n = Number(tradingDraft)
                  if (Number.isFinite(n) && n !== current.interval_trading_min) {
                    void patch({ interval_trading_min: n })
                  }
                }}
              />
            </label>
            <label className="strategy-field">
              <span>非交易间隔（分钟）</span>
              <input
                className="input"
                type="number"
                min={15}
                max={360}
                disabled={saving || current.scan_mode === 'trading_only'}
                value={offhoursDraft}
                onChange={(e) => setOffhoursDraft(e.target.value)}
                onBlur={() => {
                  const n = Number(offhoursDraft)
                  if (Number.isFinite(n) && n !== current.interval_offhours_min) {
                    void patch({ interval_offhours_min: n })
                  }
                }}
              />
            </label>
          </div>
        </section>

        <section className="home-tile policy-watch-panel">
          <h2 className="home-tile-title">监控来源</h2>
          <div className="policy-watch-presets">
            {presets.map((preset) => {
              const checked = current.preset_ids.includes(preset.id)
              const st = current.source_status?.[preset.id]
              return (
                <label
                  key={preset.id}
                  className={checked ? 'policy-watch-source is-on' : 'policy-watch-source'}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={saving}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...current.preset_ids, preset.id]
                        : current.preset_ids.filter((id) => id !== preset.id)
                      void patch({ preset_ids: next })
                    }}
                  />
                  <span className="policy-watch-source-name">{preset.name}</span>
                  {preset.description ? (
                    <span className="cell-sub">{preset.description}</span>
                  ) : null}
                  {st?.state === 'seeding' ? (
                    <span className="cell-sub">首次扫描中，不会回放旧闻</span>
                  ) : null}
                  {st?.last_error ? (
                    <span className="status error">{st.last_error}</span>
                  ) : null}
                </label>
              )
            })}
          </div>
          <div className="policy-watch-custom">
            <input
              className="input"
              placeholder="粘贴栏目列表页 URL，最多 8 条"
              value={customUrl}
              onChange={(e) => setCustomUrl(e.target.value)}
            />
            <button
              type="button"
              className="btn"
              disabled={saving || current.custom_sources.length >= 8}
              onClick={() => {
                const url = customUrl.trim()
                if (!url) return
                if (current.custom_sources.length >= 8) return
                void patch({
                  custom_sources: [...current.custom_sources, { id: '', url }],
                }).then(() => setCustomUrl(''))
              }}
            >
              添加
            </button>
          </div>
          {current.custom_sources.length ? (
            <ul className="policy-watch-custom-list">
              {current.custom_sources.map((src) => (
                <li key={src.id || src.url}>
                  <span className="mono">{src.title || src.url}</span>
                  <button
                    type="button"
                    className="btn ghost"
                    onClick={() =>
                      void patch({
                        custom_sources: current.custom_sources.filter((x) => x.url !== src.url),
                      })
                    }
                  >
                    删除
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">也可以自己贴政府网/政策网的栏目页。</p>
          )}
        </section>
      </div>

      <section className="home-tile policy-watch-inbox-wrap">
        <div className="home-news-pane-head">
          <h2 className="home-tile-title">收件箱</h2>
          <div className="policy-watch-filters" role="tablist" aria-label="收件箱筛选">
            {(
              [
                ['all', '全部'],
                ['emailed', '已发信'],
                ['inbox', '仅收录'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={filter === key}
                className={filter === key ? 'btn' : 'btn ghost'}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {!current.enabled ? <p className="muted">{EMPTY_HINT}</p> : null}
        {current.enabled && items.length === 0 && !loading ? (
          <p className="muted">暂无新文章。开启后只收录之后出现的内容。</p>
        ) : null}

        <ul className="policy-watch-inbox">
          {items.map((item) => (
            <li key={item.id} className="policy-watch-card">
              <div className="policy-watch-card-top">
                <div className="cell-main">{item.title}</div>
                <div className="policy-watch-card-tags">
                  <span className={`action-badge ${dirClass(item.direction)}`}>
                    {DIR_LABEL[item.direction || ''] || '影响不明'}
                  </span>
                  <span
                    className={
                      item.notify_status === 'sent'
                        ? 'action-badge action-buy'
                        : item.notify_status === 'failed'
                          ? 'action-badge action-sell'
                          : 'action-badge action-watch'
                    }
                  >
                    {NOTIFY_LABEL[item.notify_status] || item.notify_status}
                  </span>
                </div>
              </div>
              <div className="cell-sub">
                {item.source_label}
                {' · '}
                {formatTs(item.created_at)}
                {item.impact_score != null
                  ? ` · 分数 ${Number(item.impact_score).toFixed(2)}`
                  : ''}
              </div>
              {item.summary ? <p className="policy-watch-summary">{item.summary}</p> : null}
              {item.sectors?.length ? (
                <div className="policy-watch-chips">
                  {item.sectors.map((s) => (
                    <span key={s.name} className="policy-watch-chip">
                      {s.name}
                    </span>
                  ))}
                </div>
              ) : null}
              {item.symbols?.length ? (
                <p className="cell-sub">
                  {item.symbols
                    .map((s) =>
                      [s.symbol, s.name, s.verified === false ? '待核实' : '']
                        .filter(Boolean)
                        .join(' '),
                    )
                    .join(' · ')}
                </p>
              ) : null}
              {item.url ? (
                <a className="text-link" href={item.url} target="_blank" rel="noreferrer">
                  打开原文
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </section>
  )
}
