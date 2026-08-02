import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchRegimeCurrent, fetchRegimeHistory, type RegimeCurrent } from '../api'
import {
  buildWhyBullets,
  dataQualityLabel,
  formatCapPct,
  gateConclusion,
  gateOneLiner,
  gateShortLabel,
  metricLabel,
  sentimentLabel,
  trendLabel,
} from '../regimeCopy'

function formatMaybeNumber(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2)
  if (typeof value === 'string' && value) return value
  if (value == null) return '—'
  return String(value)
}

function formatMetricValue(key: string, value: unknown): string {
  const num =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && value.trim() !== ''
        ? Number(value)
        : NaN
  if (Number.isFinite(num) && /(?:rate|pct|ratio)$/.test(key)) {
    return formatCapPct(num)
  }
  return formatMaybeNumber(value)
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
  const showQualityBanner = data && data.data_quality !== 'ok'
  const metrics = data?.metrics || {}
  const whyBullets = data
    ? buildWhyBullets({
        gate_level: data.gate_level,
        trend_regime: data.trend_regime,
        data_quality: data.data_quality,
        evidence: data.evidence,
        metrics,
      })
    : []

  return (
    <section className="page regime-page">
      <div className="page-hero">
        <h1>今日闸门</h1>
        <p>
          先看今天能不能干、最多干多大，再看为什么这样判。
        </p>
      </div>

      <div className="diag-block">
        {loading && !data ? <p className="status">正在加载今日闸门…</p> : null}
        {error ? <p className="status error">{error}</p> : null}

        {data ? (
          <>
            {showQualityBanner ? (
              <div className="regime-banner" role="status">
                数据质量：{dataQualityLabel(data.data_quality)}。请降低仓位，并优先查看指标明细。
              </div>
            ) : null}

            <div className={`regime-hero-card regime-hero-card--${gateLevel || 'unknown'}`}>
              <div>
                <span className="metric-label">今日结论</span>
                <div className="regime-gate-label">{gateConclusion(data.gate_level)}</div>
                <p className="regime-one-liner">{gateOneLiner(data.gate_level)}</p>
              </div>
              <div>
                <span className="metric-label">仓位建议</span>
                <div className="regime-cap">
                  建议总仓位不超过 {formatCapPct(data.position_cap)}
                </div>
                <p className="meta-line">
                  {data.trade_date ? `交易日 ${data.trade_date}` : '交易日 —'}
                  {data.as_of
                    ? ` · 更新 ${new Date(data.as_of).toLocaleString('zh-CN', { hour12: false })}`
                    : ''}
                </p>
              </div>
            </div>

            <div className="regime-tags" aria-label="今日闸门标签">
              <span>趋势：{trendLabel(data.trend_regime)}</span>
              <span>情绪：{sentimentLabel(data.sentiment_cycle)}</span>
              <span>数据：{dataQualityLabel(data.data_quality)}</span>
            </div>

            <h2 className="section-title">为什么这样判</h2>
            <ol className="regime-why">
              {whyBullets.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ol>

            <div className="btn-row">
              {gateLevel === 'risk_off' && data.override_allowed ? (
                <button
                  type="button"
                  className="btn"
                  onClick={() => navigate('/recommendations?regime_override=1')}
                >
                  仍要看今日关注
                </button>
              ) : gateLevel !== 'risk_off' ? (
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => navigate('/recommendations')}
                >
                  查看今日关注
                </button>
              ) : null}
            </div>

            <details className="regime-details">
              <summary>查看指标明细</summary>
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
                      <tr key={`evidence-${item.key}-${item.value}`}>
                        <td>{metricLabel(item.key)}</td>
                        <td>{formatMetricValue(item.key, item.value)}</td>
                        <td>{item.note || '—'}</td>
                      </tr>
                    ))}
                    {Object.entries(metrics)
                      .filter(([key]) => key !== 'evidence')
                      .slice(0, 6)
                      .map(([key, value]) => (
                        <tr key={`metric-${key}`}>
                          <td>{metricLabel(key)}</td>
                          <td>{formatMetricValue(key, value)}</td>
                          <td>—</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {data.evidence?.length || Object.keys(metrics).length ? null : (
                  <p className="muted">暂无指标明细。</p>
                )}
              </div>
            </details>

            <h2 className="section-title">近 N 日周期</h2>
            {history.length ? (
              <div className="stat-row">
                {history.slice(0, 8).map((item) => (
                  <div className="stat" key={item.trade_date || `${item.gate_level}-${item.as_of}`}>
                    <span className="metric-label">{item.trade_date || '—'}</span>
                    <div className="metric-value">{gateShortLabel(item.gate_level)}</div>
                    <span className="meta-line">
                      情绪{sentimentLabel(item.sentiment_cycle)} · 仓位
                      {formatCapPct(item.position_cap)}
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
