import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  deleteMonitorJob,
  fetchMonitorJobs,
  pauseMonitorJob,
  resumeMonitorJob,
  type MonitorJob,
  type MonitorRule,
} from '../api'

const SCOPE_LABEL: Record<string, string> = {
  watchlist: '收藏',
  portfolio: '持仓',
  symbols: '指定代码',
}

const RULE_LABEL: Record<string, string> = {
  price_below: '现价≤',
  price_above: '现价≥',
  day_chg_below: '涨跌幅≤',
  day_chg_above: '涨跌幅≥',
}

function formatRule(rule: MonitorRule): string {
  const label = RULE_LABEL[rule.type] || rule.type
  if (rule.type.startsWith('day_chg')) {
    const pct = Number(rule.value) * 100
    const body = `${label}${Number.isFinite(pct) ? pct.toFixed(2) + '%' : rule.value}`
    return rule.hint ? `${body}（${rule.hint}）` : body
  }
  const body = `${label}${rule.value}`
  return rule.hint ? `${body}（${rule.hint}）` : body
}

function formatTs(raw?: string | null): string {
  if (!raw) return '—'
  try {
    const d = new Date(raw)
    if (Number.isNaN(d.getTime())) return raw
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return raw
  }
}

export default function MonitorJobsPage() {
  const [jobs, setJobs] = useState<MonitorJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyMap, setBusyMap] = useState<Record<string, boolean>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetchMonitorJobs()
      setJobs(res.jobs || [])
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function withBusy(id: string, fn: () => Promise<void>) {
    setBusyMap((prev) => ({ ...prev, [id]: true }))
    try {
      await fn()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyMap((cur) => {
        const copy = { ...cur }
        delete copy[id]
        return copy
      })
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <p>
          盯盘任务在交易时段由后台轮询规则，触发后向已验证邮箱发告警（不下单）。可在投研助手对话里创建。
        </p>
      </div>

      <div className="diag-block">
        <div className="form-actions" style={{ marginTop: 0 }}>
          <h2 className="section-title">定时任务</h2>
          <button
            type="button"
            className="btn ghost"
            disabled={loading}
            onClick={() => void load()}
          >
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>
        <p className="meta-line">
          {jobs.length ? `共 ${jobs.length} 条` : '暂无任务'}
          {' · '}
          <Link className="text-link" to="/agent">
            打开投研助手
          </Link>
        </p>
        {error ? <p className="status error">{error}</p> : null}
        {loading && jobs.length === 0 ? (
          <p className="status">正在加载…</p>
        ) : null}
        {!loading && jobs.length === 0 ? (
          <p className="muted">
            暂无定时任务。可在投研助手对话里说「帮我盯收藏里跌超 3% 的标的发邮件」。
          </p>
        ) : null}
        {jobs.length > 0 ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>标题</th>
                  <th>范围</th>
                  <th>状态</th>
                  <th>规则</th>
                  <th>最近运行 / 告警</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const busy = Boolean(busyMap[job.id])
                  const scopeLabel = SCOPE_LABEL[job.scope] || job.scope
                  const scopeExtra =
                    job.scope === 'symbols' && job.symbols?.length
                      ? `（${job.symbols.slice(0, 3).join('、')}${
                          job.symbols.length > 3 ? '…' : ''
                        }）`
                      : ''
                  return (
                    <tr key={job.id}>
                      <td>
                        <div className="cell-main">{job.title}</div>
                        <div className="cell-sub mono">{job.id}</div>
                      </td>
                      <td>
                        {scopeLabel}
                        {scopeExtra}
                      </td>
                      <td>{job.status === 'running' ? '运行中' : '已暂停'}</td>
                      <td>
                        {(job.rules || []).map(formatRule).join('；') || '—'}
                      </td>
                      <td>
                        <div className="cell-main">{formatTs(job.last_run_at)}</div>
                        <div className="cell-sub">{formatTs(job.last_alert_at)}</div>
                        {job.last_error ? (
                          <div className="cell-sub status error">{job.last_error}</div>
                        ) : null}
                      </td>
                      <td className="row-actions">
                        {job.status === 'running' ? (
                          <button
                            type="button"
                            className="btn ghost"
                            disabled={busy}
                            onClick={() =>
                              void withBusy(job.id, async () => {
                                const out = await pauseMonitorJob(job.id)
                                setJobs((prev) =>
                                  prev.map((j) => (j.id === job.id ? out : j)),
                                )
                              })
                            }
                          >
                            暂停
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn ghost"
                            disabled={busy}
                            onClick={() =>
                              void withBusy(job.id, async () => {
                                const out = await resumeMonitorJob(job.id)
                                setJobs((prev) =>
                                  prev.map((j) => (j.id === job.id ? out : j)),
                                )
                              })
                            }
                          >
                            继续
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn ghost"
                          disabled={busy}
                          onClick={() =>
                            void withBusy(job.id, async () => {
                              if (!window.confirm(`确认删除任务「${job.title}」？`)) {
                                return
                              }
                              await deleteMonitorJob(job.id)
                              setJobs((prev) => prev.filter((j) => j.id !== job.id))
                            })
                          }
                        >
                          删除
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </section>
  )
}
