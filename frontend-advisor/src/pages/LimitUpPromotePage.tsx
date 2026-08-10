import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchLimitUpPromote,
  fetchLimitUpPromoteHistory,
  fetchLimitUpPromoteHistoryDay,
  fetchLimitUpPromoteStatus,
  refreshLimitUpPromote,
  type LimitUpPromoteAccuracy,
  type LimitUpPromoteHistoryItem,
  type LimitUpPromotePick,
  type LimitUpPromoteResponse,
} from '../api'
import { explorerKlineUrl } from '../explorerLinks'

const DISCLAIMER =
  '研究观察、不保证次日涨停，非投资建议与下单指令。'
const POLL_MS = 2500

function scoreLabel(score: number): string {
  if (score >= 5) return '很高'
  if (score >= 4) return '较高'
  if (score >= 3) return '中等'
  if (score >= 2) return '偏低'
  return '很低'
}

function isMissingKeyError(message: string): boolean {
  return /DeepSeek|API Key/i.test(message)
}

function themeUsedLabel(used?: {
  news?: boolean
  hot_sectors?: boolean
  brief?: boolean
} | null): string {
  const parts = [
    used?.news ? '今日资讯/政策' : null,
    used?.hot_sectors ? '热点板块' : null,
    used?.brief ? 'Agent 解读' : null,
  ].filter(Boolean)
  return parts.length
    ? `已结合：${parts.join(' · ')}`
    : '未取到资讯/热点（仍按封板池研判）'
}

function formatHitRate(rate: number | null | undefined): string {
  if (rate == null || Number.isNaN(rate)) return '—'
  return `${(rate * 100).toFixed(1)}%`
}

function pickBadge(row: LimitUpPromotePick): string | null {
  if (row.t1_status === 'broken' || row.broken) return '炸板'
  if (row.t1_status === 'sealed' || row.hit) return '封板'
  if (row.t1_status === 'miss') return '未中'
  return null
}

export default function LimitUpPromotePage() {
  const [data, setData] = useState<LimitUpPromoteResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [history, setHistory] = useState<LimitUpPromoteHistoryItem[]>([])
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [accuracy, setAccuracy] = useState<LimitUpPromoteAccuracy | null>(null)
  const pollRef = useRef<number | null>(null)
  const reqIdRef = useRef(0)

  const stopPoll = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const applyDoc = useCallback((payload: LimitUpPromoteResponse) => {
    setData(payload)
    const st = payload.status || 'ready'
    if (st === 'running') {
      setStatus(payload.progress?.message || '后台研判中…')
      setError(null)
    } else if (st === 'error') {
      setStatus(null)
      setError(payload.error || '晋级研判失败')
    } else {
      setStatus(null)
      if (payload.error) setError(payload.error)
      else setError(null)
    }
  }, [])

  const startPoll = useCallback(
    (tradeDate?: string) => {
      stopPoll()
      pollRef.current = window.setInterval(() => {
        void fetchLimitUpPromoteStatus(tradeDate)
          .then((row) => {
            applyDoc(row)
            if (row.status === 'ready' || row.status === 'error' || row.status === 'idle') {
              stopPoll()
              setRefreshing(false)
              setLoading(false)
              if (row.status === 'ready') {
                void fetchLimitUpPromoteHistory(30)
                  .then((res) => setHistory(res.items || []))
                  .catch(() => undefined)
              }
            }
          })
          .catch(() => undefined)
      }, POLL_MS)
    },
    [applyDoc, stopPoll],
  )

  const loadToday = useCallback(async () => {
    const reqId = ++reqIdRef.current
    setLoading(true)
    setError(null)
    setSelectedDate(null)
    setAccuracy(null)
    setStatus('正在加载…')
    try {
      const payload = await fetchLimitUpPromote()
      if (reqId !== reqIdRef.current) return
      applyDoc(payload)
      if (payload.status === 'running') {
        startPoll(payload.trade_date || payload.date)
      } else {
        stopPoll()
      }
    } catch (e) {
      if (reqId !== reqIdRef.current) return
      const msg = e instanceof Error ? e.message : '加载失败'
      setError(msg)
      setStatus(null)
      setData(null)
    } finally {
      if (reqId === reqIdRef.current) setLoading(false)
    }
  }, [applyDoc, startPoll, stopPoll])

  const refresh = useCallback(async () => {
    const reqId = ++reqIdRef.current
    setRefreshing(true)
    setError(null)
    setStatus('已启动后台刷新，可离开本页…')
    setSelectedDate(null)
    setAccuracy(null)
    try {
      const payload = await refreshLimitUpPromote(false)
      if (reqId !== reqIdRef.current) return
      applyDoc(payload)
      startPoll(payload.trade_date || payload.date)
    } catch (e) {
      if (reqId !== reqIdRef.current) return
      const msg = e instanceof Error ? e.message : '刷新失败'
      setError(msg)
      setStatus(null)
      setRefreshing(false)
    }
  }, [applyDoc, startPoll])

  const loadHistoryList = useCallback(async () => {
    try {
      const res = await fetchLimitUpPromoteHistory(30)
      setHistory(res.items || [])
    } catch {
      setHistory([])
    }
  }, [])

  const openHistoryDay = useCallback(async (tradeDate: string) => {
    setSelectedDate(tradeDate)
    setLoading(true)
    setError(null)
    setStatus(null)
    stopPoll()
    try {
      const res = await fetchLimitUpPromoteHistoryDay(tradeDate)
      applyDoc(res.doc)
      setAccuracy(res.accuracy)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载历史失败'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [applyDoc, stopPoll])

  useEffect(() => {
    void loadToday()
    void loadHistoryList()
    return () => {
      stopPoll()
    }
  }, [loadToday, loadHistoryList, stopPoll])

  const needsKey = error ? isMissingKeyError(error) : false
  const busy = loading || refreshing || data?.status === 'running'
  const viewingHistory = Boolean(selectedDate)
  const outcomePicks =
    accuracy?.ok && accuracy.hits
      ? new Map(
          [...(accuracy.hits || []), ...(accuracy.misses || [])].map((p) => [
            p.symbol,
            p,
          ]),
        )
      : data?.outcome?.picks
        ? new Map(data.outcome.picks.map((p) => [p.symbol, p]))
        : null

  return (
    <section className="page">
      <div className="page-hero">
        <p>
          基于当日当前封板池，用你的 DeepSeek 做结构化研判，结果按天归档。
          无当日数据时自动后台刷新，可离开页面稍后回来查看。
          {DISCLAIMER}
        </p>
      </div>

      <div className="diag-block">
        <div className="form-actions" style={{ marginTop: 0 }}>
          <h2 className="section-title">打板晋级</h2>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {viewingHistory ? (
              <button
                type="button"
                className="btn ghost"
                onClick={() => void loadToday()}
              >
                回今日
              </button>
            ) : null}
            <button
              type="button"
              className="btn ghost"
              disabled={busy && !viewingHistory}
              onClick={() => void refresh()}
            >
              {refreshing || data?.status === 'running' ? '后台研判中…' : '刷新研判'}
            </button>
          </div>
        </div>

        {status ? (
          <p className="status" role="status" data-testid="promote-progress">
            {status}
          </p>
        ) : null}

        {loading && !data && !status ? <p className="muted">正在加载研判…</p> : null}

        {error ? (
          <div className="status error" role="alert">
            <p>{error}</p>
            {needsKey ? (
              <p>
                请先在{' '}
                <Link className="text-link" to="/agent/settings">
                  DeepSeek 配置
                </Link>{' '}
                填写 API Key。
              </p>
            ) : null}
          </div>
        ) : null}

        {data && (data.status === 'ready' || (data.picks && data.picks.length > 0)) ? (
          <>
            <p className="meta-line" data-testid="promote-meta">
              池日期 {data.date || data.trade_date || '—'}
              {' · '}
              封板样本 {data.candidate_count} 只
              {' · '}
              研判时间 {data.as_of || '—'}
              {viewingHistory ? ' · 历史归档' : ''}
            </p>
            <p className="muted" data-testid="promote-theme">
              {themeUsedLabel(data.theme_used)}
            </p>
            {data.summary ? (
              <p data-testid="promote-summary">{data.summary}</p>
            ) : null}

            {(accuracy?.ok || data.outcome) && (
              <p className="meta-line" data-testid="promote-accuracy">
                次日成功率{' '}
                {formatHitRate(accuracy?.hit_rate ?? data.outcome?.hit_rate)}
                {' · '}
                命中 {accuracy?.hit_count ?? data.outcome?.hit_count ?? 0}/
                {accuracy?.pick_count ?? data.outcome?.pick_count ?? data.picks.length}
                {' · '}
                其中炸板{' '}
                {accuracy?.broken_hit_count ?? data.outcome?.broken_hit_count ?? 0}
                {accuracy?.pending ? ' · 次日未到暂不可统计' : ''}
                {accuracy && !accuracy.ok && accuracy.error
                  ? ` · ${accuracy.error}`
                  : ''}
              </p>
            )}

            <p className="muted">{DISCLAIMER}</p>

            {data.picks.length === 0 ? (
              <p className="muted">暂无候选（封板池为空或模型未给出有效标的）。</p>
            ) : (
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th>名称</th>
                      <th>现连板</th>
                      <th>关注度</th>
                      <th>次日</th>
                      <th>理由</th>
                      <th>K线</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.picks.map((row: LimitUpPromotePick) => {
                      const enriched = outcomePicks?.get(row.symbol)
                      const badge = pickBadge(enriched || row)
                      return (
                        <tr key={row.symbol}>
                          <td>{row.symbol}</td>
                          <td>{row.name}</td>
                          <td>{row.board_count}连板</td>
                          <td>
                            {row.score}/5 · {scoreLabel(row.score)}
                          </td>
                          <td data-testid={`promote-t1-${row.symbol}`}>
                            {badge ? (
                              <span
                                className={
                                  badge === '炸板'
                                    ? 'status'
                                    : badge === '未中'
                                      ? 'muted'
                                      : ''
                                }
                              >
                                {badge}
                              </span>
                            ) : (
                              '—'
                            )}
                          </td>
                          <td>{row.reason}</td>
                          <td>
                            <a
                              className="text-link"
                              href={explorerKlineUrl(row.symbol)}
                              target="_blank"
                              rel="noreferrer"
                            >
                              查看
                            </a>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : null}
      </div>

      <div className="diag-block">
        <h2 className="section-title">历史晋级</h2>
        <p className="muted">
          按天查看归档；成功率=次日出现在涨停池（含炸板），炸板单独标记。
        </p>
        {history.length === 0 ? (
          <p className="muted">暂无历史归档。</p>
        ) : (
          <ul className="plain-list" data-testid="promote-history">
            {history.map((item) => (
              <li key={item.trade_date}>
                <button
                  type="button"
                  className="text-link"
                  style={{
                    background: 'none',
                    border: 'none',
                    padding: 0,
                    cursor: 'pointer',
                    fontWeight:
                      selectedDate === item.trade_date ? 700 : undefined,
                  }}
                  onClick={() => void openHistoryDay(item.trade_date)}
                >
                  {item.trade_date}
                </button>
                {' · '}
                {item.pick_count} 只
                {item.summary ? ` · ${item.summary}` : ''}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
