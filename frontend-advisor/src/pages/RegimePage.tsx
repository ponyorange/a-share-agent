import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchRegimeCurrent, fetchRegimeHistory, type RegimeCurrent } from '../api'

const GATE_LABELS: Record<string, string> = {
  risk_off: '风险关闭',
  defensive: '防御模式',
  normal: '正常模式',
  aggressive: '积极模式',
}

const TREND_LABELS: Record<string, string> = {
  uptrend: '上行趋势',
  range: '震荡区间',
  downtrend: '下行趋势',
}

const SENTIMENT_LABELS: Record<string, string> = {
  ice: '情绪冰点',
  repair: '情绪修复',
  strengthen: '情绪增强',
  climax: '情绪高潮',
  ebb: '情绪退潮',
  neutral: '情绪中性',
}

function labelOf(map: Record<string, string>, value: string | null | undefined) {
  if (!value) return '—'
  return map[value] || value
}

function formatPct(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

function formatMaybeNumber(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
  if (typeof value === 'string' && value) return value
  if (value == null) return '—'
  return String(value)
}

export default function RegimePage() {
  const navigate = useNavigate()
  const [data, setData] = useState<RegimeCurrent | null>(null)
  const [history, setHistory] = useState<RegimeCurrent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    fetchRegimeCurrent()
      .then((res) => {
        if (!alive) return
        setData(res)
        setError(null)
      })
      .catch((err) => {
        if (!alive) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    let alive = true
    fetchRegimeHistory(20)
      .then((rows) => {
        if (alive) setHistory(rows)
      })
      .catch(() => {
        if (alive) setHistory([])
      })
    return () => {
      alive = false
    }
  }, [])

  const gateLevel = data?.gate_level
  const gateLabel = labelOf(GATE_LABELS, gateLevel)
  const showQualityBanner = data && data.data_quality !== 'ok'
  const metrics = data?.metrics || {}

  return (
    <section className="page regime-page">
      <div className="page-hero">
        <h1>市场状态</h1>
        <p>
          汇总趋势、情绪周期与数据质量，给出今日推荐闸门和仓位上限。风险关闭时默认不主动推买入名单。
        </p>
      </div>

      <div className="diag-block">
        {loading && !data ? <p className="status">正在加载市场状态…</p> : null}
        {error ? <p className="status error">{error}</p> : null}

        {data ? (
          <>
            {showQualityBanner ? (
              <div className="regime-banner" role="status">
                数据质量：{data.data_quality}。请降低仓位，并优先查看 evidence。
              </div>
            ) : null}

            <div className={`regime-hero-card regime-hero-card--${gateLevel || 'unknown'}`}>
              <div>
                <span className="metric-label">今日闸门</span>
                <div className="regime-gate-label">{gateLabel}</div>
                <p className="meta-line">
                  raw gate_level: <span className="mono">{gateLevel || '—'}</span>
                </p>
              </div>
              <div>
                <span className="metric-label">仓位上限</span>
                <div className="regime-cap">{formatPct(data.position_cap)}</div>
                <p className="meta-line">
                  {data.trade_date ? `交易日 ${data.trade_date}` : '交易日 —'}
                  {data.as_of
                    ? ` · 更新 ${new Date(data.as_of).toLocaleString('zh-CN', { hour12: false })}`
                    : ''}
                </p>
              </div>
            </div>

            <div className="stat-row">
              <div className="stat">
                <span className="metric-label">趋势状态</span>
                <div className="metric-value">{labelOf(TREND_LABELS, data.trend_regime)}</div>
                <span className="meta-line mono">{data.trend_regime}</span>
              </div>
              <div className="stat">
                <span className="metric-label">情绪周期</span>
                <div className="metric-value">
                  {labelOf(SENTIMENT_LABELS, data.sentiment_cycle)}
                </div>
                <span className="meta-line mono">{data.sentiment_cycle}</span>
              </div>
              <div className="stat">
                <span className="metric-label">数据质量</span>
                <div className="metric-value">{data.data_quality}</div>
                <span className="meta-line">{data.pool_policy || '—'}</span>
              </div>
            </div>

            {gateLevel === 'risk_off' && data.override_allowed ? (
              <button
                type="button"
                className="btn"
                onClick={() => navigate('/?regime_override=1')}
              >
                仍要看今日关注
              </button>
            ) : null}

            <h2 className="section-title">关键证据</h2>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>指标</th>
                    <th>数值</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.evidence || []).map((item) => (
                    <tr key={`${item.key}-${item.value}`}>
                      <td className="mono">{item.key}</td>
                      <td>{formatMaybeNumber(item.value)}</td>
                      <td>{item.note || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.evidence?.length ? null : <p className="muted">暂无 evidence。</p>}
            </div>

            <h2 className="section-title">情绪指标</h2>
            <div className="stat-row">
              {Object.entries(metrics)
                .filter(([key]) => key !== 'evidence')
                .slice(0, 6)
                .map(([key, value]) => (
                  <div className="stat" key={key}>
                    <span className="metric-label mono">{key}</span>
                    <div className="metric-value">{formatMaybeNumber(value)}</div>
                  </div>
                ))}
            </div>

            <h2 className="section-title">近 N 日周期</h2>
            {history.length ? (
              <div className="stat-row">
                {history.slice(0, 8).map((item) => (
                  <div className="stat" key={item.trade_date || `${item.gate_level}-${item.as_of}`}>
                    <span className="metric-label">{item.trade_date || '—'}</span>
                    <div className="metric-value mono">{item.gate_level}</div>
                    <span className="meta-line">
                      {labelOf(SENTIMENT_LABELS, item.sentiment_cycle)} · 仓位{formatPct(item.position_cap)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">暂无历史周期。</p>
            )}
          </>
        ) : null}
      </div>
    </section>
  )
}
