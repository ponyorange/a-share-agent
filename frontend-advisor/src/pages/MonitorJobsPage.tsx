import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  deleteMonitorJob,
  fetchRegimeBriefTemplate,
  fetchMonitorJobLogs,
  fetchMonitorJobs,
  pauseMonitorJob,
  resumeMonitorJob,
  type MonitorJob,
  type MonitorJobLog,
  type MonitorRule,
} from '../api'
import { copyText } from '../copyText'

const SCOPE_LABEL: Record<string, string> = {
  watchlist: '收藏',
  portfolio: '持仓',
  symbols: '指定代码',
}

const STATUS_LABEL: Record<string, string> = {
  scheduled: '已调度',
  running: '运行中',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
}

const RULE_LABEL: Record<string, string> = {
  price_below: '现价≤',
  price_above: '现价≥',
  day_chg_below: '涨跌幅≤',
  day_chg_above: '涨跌幅≥',
  flow_spike_in: '主力流入异动',
  flow_spike_out: '主力流出异动',
}

function formatRule(rule: MonitorRule): string {
  const label = RULE_LABEL[rule.type] || rule.type
  if (rule.type.startsWith('day_chg')) {
    const pct = Number(rule.value) * 100
    const body = `${label}${Number.isFinite(pct) ? pct.toFixed(2) + '%' : rule.value}`
    return rule.hint ? `${body}（${rule.hint}）` : body
  }
  if (rule.type.startsWith('flow_spike')) {
    const pct = Number(rule.value) * 100
    const mult = rule.mult != null ? `×${rule.mult}` : '×3'
    const body = `${label}（占比≥${Number.isFinite(pct) ? pct.toFixed(1) : rule.value}% / ${mult}）`
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

/** Hide countdown while watch job is actively running. */
export function shouldShowCountdown(job: MonitorJob): boolean {
  const kind = job.kind || 'watch'
  if (kind === 'watch' && job.status === 'running') return false
  return Boolean(job.next_run_at) && job.status !== 'completed' && job.status !== 'failed'
}

export function formatCountdown(nextRunAt: string | null | undefined, nowMs: number): string {
  if (!nextRunAt) return '—'
  const target = new Date(nextRunAt).getTime()
  if (Number.isNaN(target)) return '—'
  const diff = target - nowMs
  if (diff <= 0) return '即将触发'
  const totalSec = Math.floor(diff / 1000)
  const days = Math.floor(totalSec / 86400)
  const hours = Math.floor((totalSec % 86400) / 3600)
  const mins = Math.floor((totalSec % 3600) / 60)
  const secs = totalSec % 60
  if (days > 0) return `${days}天 ${hours}时 ${mins}分`
  if (hours > 0) return `${hours}时 ${mins}分 ${secs}秒`
  if (mins > 0) return `${mins}分 ${secs}秒`
  return `${secs}秒`
}

function scheduleLabel(job: MonitorJob): string {
  const kind = job.kind === 'run_at' ? '定点' : '盯盘'
  const repeat = job.repeat === 'once' ? '一次' : '重复'
  const cal = job.calendar === 'everyday' ? '每天' : '交易日'
  return `${kind} · ${repeat} · ${cal}`
}

export default function MonitorJobsPage() {
  const [jobs, setJobs] = useState<MonitorJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyMap, setBusyMap] = useState<Record<string, boolean>>({})
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [logJob, setLogJob] = useState<MonitorJob | null>(null)
  const [logs, setLogs] = useState<MonitorJobLog[]>([])
  const [logsError, setLogsError] = useState<string | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const [briefCopy, setBriefCopy] = useState<'idle' | 'copied' | 'failed'>('idle')

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

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  useEffect(() => {
    if (!logJob) {
      setLogs([])
      setLogsError(null)
      return
    }
    let cancelled = false
    const pull = async () => {
      if (document.visibilityState === 'hidden') return
      try {
        const res = await fetchMonitorJobLogs(logJob.id, { limit: 100 })
        if (cancelled) return
        setLogs(res.logs || [])
        setLogsError(null)
      } catch (err) {
        if (cancelled) return
        setLogsError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLogsLoading(false)
      }
    }
    setLogsLoading(true)
    void pull()
    const id = window.setInterval(() => void pull(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [logJob])

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

  async function copyBriefTemplate() {
    try {
      const res = await fetchRegimeBriefTemplate()
      await copyText(res.prompt)
      setBriefCopy('copied')
      window.setTimeout(() => setBriefCopy('idle'), 1500)
    } catch (err) {
      setBriefCopy('failed')
      setError(err instanceof Error ? err.message : String(err))
      window.setTimeout(() => setBriefCopy('idle'), 2000)
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <p>
          规则/资金异动即时邮件告警，可同时开启 Agent 看盘（间隔或涨跌异动后综合研判，仅买/卖发信，不下单）。在投研助手对话里创建。
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
          <button
            type="button"
            className="btn ghost"
            onClick={() => void copyBriefTemplate()}
          >
            {briefCopy === 'copied'
              ? '已复制今日闸门早盘简报'
              : briefCopy === 'failed'
                ? '复制失败'
                : '复制今日闸门早盘简报'}
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
            暂无定时任务。可在投研助手说「明天盯收藏」或「每个交易日 9 点邮件推送」。
          </p>
        ) : null}
        {jobs.length > 0 ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>标题</th>
                  <th>调度</th>
                  <th>状态</th>
                  <th>下次 / 倒计时</th>
                  <th>看盘</th>
                  <th>规则</th>
                  <th>最近运行</th>
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
                  const showCd = shouldShowCountdown(job)
                  const canPause = job.status === 'running' || job.status === 'scheduled'
                  const canResume = job.status === 'paused'
                  return (
                    <tr key={job.id}>
                      <td>
                        <div className="cell-main">{job.title}</div>
                        <div className="cell-sub">
                          {scopeLabel}
                          {scopeExtra}
                        </div>
                      </td>
                      <td>
                        <div className="cell-main">{scheduleLabel(job)}</div>
                        {job.run_time ? (
                          <div className="cell-sub mono">{job.run_time}</div>
                        ) : null}
                      </td>
                      <td>{STATUS_LABEL[job.status] || job.status}</td>
                      <td>
                        {showCd ? (
                          <>
                            <div className="cell-main mono">
                              {formatCountdown(job.next_run_at, nowMs)}
                            </div>
                            <div className="cell-sub">{formatTs(job.next_run_at)}</div>
                          </>
                        ) : job.status === 'running' && (job.kind || 'watch') === 'watch' ? (
                          <span className="cell-main">运行中</span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>{job.llm_enabled ? '开' : '关'}</td>
                      <td>
                        {(job.rules || []).map(formatRule).join('；') ||
                          (job.kind === 'run_at' ? '定点 Agent' : '—')}
                      </td>
                      <td>
                        <div className="cell-main">{formatTs(job.last_run_at)}</div>
                        {job.last_error ? (
                          <div className="cell-sub status error">{job.last_error}</div>
                        ) : null}
                      </td>
                      <td className="row-actions">
                        <button
                          type="button"
                          className="btn ghost"
                          onClick={() => setLogJob(job)}
                        >
                          日志
                        </button>
                        {canPause ? (
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
                        ) : null}
                        {canResume ? (
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
                        ) : null}
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

      {logJob ? (
        <div
          className="monitor-log-backdrop"
          role="presentation"
          onClick={() => setLogJob(null)}
        >
          <aside
            className="monitor-log-drawer"
            role="dialog"
            aria-label={`任务日志 ${logJob.title}`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="monitor-log-head">
              <div>
                <h3>{logJob.title}</h3>
                <p className="meta-line mono">{logJob.id}</p>
              </div>
              <button type="button" className="btn ghost" onClick={() => setLogJob(null)}>
                关闭
              </button>
            </div>
            {logsLoading && logs.length === 0 ? (
              <p className="status">加载日志…</p>
            ) : null}
            {logsError ? <p className="status error">{logsError}</p> : null}
            <div className="monitor-log-console" data-testid="monitor-log-console">
              {logs.length === 0 && !logsLoading ? (
                <p className="muted">暂无日志</p>
              ) : (
                logs.map((row) => (
                  <div key={row.id} className={`monitor-log-line level-${row.level}`}>
                    <span className="mono">{formatTs(row.ts)}</span>
                    <span className="monitor-log-event">{row.event}</span>
                    <span>{row.message}</span>
                  </div>
                ))
              )}
            </div>
          </aside>
        </div>
      ) : null}
    </section>
  )
}
