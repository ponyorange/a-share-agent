import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  streamLimitUpPromote,
  type LimitUpPromotePick,
  type LimitUpPromoteResponse,
} from '../api'
import { explorerKlineUrl } from '../explorerLinks'

const DISCLAIMER =
  '研究观察、不保证次日涨停，非投资建议与下单指令。'

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

export default function LimitUpPromotePage() {
  const [data, setData] = useState<LimitUpPromoteResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [thinking, setThinking] = useState('')
  const [thinkingOpen, setThinkingOpen] = useState(true)
  const abortRef = useRef<AbortController | null>(null)
  const reqIdRef = useRef(0)

  const load = useCallback(async (force: boolean) => {
    const reqId = ++reqIdRef.current
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    if (force) setRefreshing(true)
    else setLoading(true)
    setError(null)
    setStatus(force ? '正在刷新研判…' : '正在加载研判…')
    setThinking('')
    try {
      await streamLimitUpPromote(
        force,
        {
          onProgress: (row) => {
            if (reqId !== reqIdRef.current) return
            setStatus(row.message || '研判中…')
          },
          onThinking: (delta) => {
            if (reqId !== reqIdRef.current) return
            if (!delta) return
            setThinking((prev) => prev + delta)
            setThinkingOpen(true)
          },
          onDone: (payload) => {
            if (reqId !== reqIdRef.current) return
            setData(payload)
            setStatus(null)
            setError(null)
          },
          onError: (detail) => {
            if (reqId !== reqIdRef.current) return
            setError(detail)
            setStatus(null)
            if (!force) setData(null)
          },
        },
        ac.signal,
      )
    } catch (e) {
      if (reqId !== reqIdRef.current) return
      if ((e as { name?: string })?.name === 'AbortError') return
      const msg = e instanceof Error ? e.message : '加载失败'
      setError(msg)
      setStatus(null)
      if (!force) setData(null)
    } finally {
      if (reqId === reqIdRef.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => {
    void load(false)
    return () => {
      abortRef.current?.abort()
    }
  }, [load])

  const needsKey = error ? isMissingKeyError(error) : false
  const busy = loading || refreshing

  return (
    <section className="page">
      <div className="page-hero">
        <p>
          基于当日当前封板池，用你的 DeepSeek 做结构化研判，列出次日相对更值得观察的候选。
          {DISCLAIMER}
        </p>
      </div>

      <div className="diag-block">
        <div className="form-actions" style={{ marginTop: 0 }}>
          <h2 className="section-title">打板晋级</h2>
          <button
            type="button"
            className="btn ghost"
            disabled={busy}
            onClick={() => void load(true)}
          >
            {refreshing ? '研判中…' : '刷新研判'}
          </button>
        </div>

        {status ? (
          <p className="status" role="status" data-testid="promote-progress">
            {status}
          </p>
        ) : null}

        {thinking ? (
          <details
            className="regime-details"
            open={thinkingOpen}
            onToggle={(e) => setThinkingOpen((e.target as HTMLDetailsElement).open)}
            data-testid="promote-thinking"
          >
            <summary>思考过程</summary>
            <pre className="muted" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
              {thinking}
            </pre>
          </details>
        ) : busy && !error ? (
          <p className="muted" data-testid="promote-thinking-empty">
            等待模型思考内容（部分模型可能不返回）…
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

        {data ? (
          <>
            <p className="meta-line" data-testid="promote-meta">
              池日期 {data.date || '—'}
              {' · '}
              封板样本 {data.candidate_count} 只
              {' · '}
              研判时间 {data.as_of || '—'}
              {data.from_cache ? ' · 缓存结果' : ''}
            </p>
            <p className="muted" data-testid="promote-theme">
              {themeUsedLabel(data.theme_used)}
            </p>
            {data.summary ? (
              <p data-testid="promote-summary">{data.summary}</p>
            ) : null}
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
                      <th>理由</th>
                      <th>K线</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.picks.map((row: LimitUpPromotePick) => (
                      <tr key={row.symbol}>
                        <td>{row.symbol}</td>
                        <td>{row.name}</td>
                        <td>{row.board_count}连板</td>
                        <td>
                          {row.score}/5 · {scoreLabel(row.score)}
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
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : null}
      </div>
    </section>
  )
}
