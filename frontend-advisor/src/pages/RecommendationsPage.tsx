import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchActiveRecommendationsRefresh,
  fetchRecommendations,
  formatPct,
  formatScore,
  streamOneClickBuy,
  streamRecommendationsRefresh,
  streamRecommendationsRefreshJob,
  streamRecQuotes,
  type AdviceItem,
  type RecommendationsResponse,
} from '../api'
import { ActionBadge } from '../components/AdviceCard'
import { MobileDisclosure } from '../components/MobileDisclosure'
import { RecommendationCard } from '../components/RecommendationCard'
import { ResponsiveDataView } from '../components/ResponsiveDataView'
import { explorerKlineUrl } from '../explorerLinks'

type BoardTab = 'etf' | 'hs' | 'star'

const TABS: { id: BoardTab; label: string }[] = [
  { id: 'etf', label: 'ETF' },
  { id: 'hs', label: '沪深股' },
  { id: 'star', label: '科创股' },
]

function chgClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return ''
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return ''
}

function BoardTable({ items }: { items: AdviceItem[] }) {
  if (!items.length) {
    return <p className="status">本板暂无推荐，可稍后重试或调低买入阈值。</p>
  }
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>收盘</th>
            <th>日涨幅</th>
            <th>评分</th>
            <th>建议</th>
            <th>命中率</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.symbol}>
              <td className="mono">{item.symbol}</td>
              <td className="name-cell">{item.name}</td>
              <td>{item.close != null ? item.close : '—'}</td>
              <td className={chgClass(item.day_chg_pct)}>
                {item.day_chg_pct == null ? '—' : formatPct(item.day_chg_pct, 2)}
              </td>
              <td>{formatScore(item.score)}</td>
              <td>
                <ActionBadge action={item.action} label={item.action_label} />
              </td>
              <td>{formatPct(item.hit_rate)}</td>
              <td className="row-actions">
                <Link className="text-link" to={`/advice?symbol=${item.symbol}`}>
                  诊断
                </Link>
                <a
                  className="text-link"
                  href={explorerKlineUrl(item.symbol)}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看 K 线
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function patchQuote(
  data: RecommendationsResponse,
  symbol: string,
  patch: Partial<AdviceItem>,
): RecommendationsResponse {
  const boards = { ...(data.boards || {}) }
  for (const bid of Object.keys(boards)) {
    const block = boards[bid]
    boards[bid] = {
      ...block,
      items: block.items.map((it) =>
        it.symbol === symbol ? { ...it, ...patch } : it,
      ),
    }
  }
  return {
    ...data,
    boards,
    items: (data.items || []).map((it) =>
      it.symbol === symbol ? { ...it, ...patch } : it,
    ),
  }
}

export default function RecommendationsPage() {
  const [tab, setTab] = useState<BoardTab>('etf')
  const [data, setData] = useState<RecommendationsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [buyMsg, setBuyMsg] = useState<string | null>(null)
  const [buying, setBuying] = useState(false)
  const [limitEnabled, setLimitEnabled] = useState(false)
  const [maxBuyCount, setMaxBuyCount] = useState(5)
  const [buyProgress, setBuyProgress] = useState<{ done: number; total: number } | null>(
    null,
  )
  const [quoteLoading, setQuoteLoading] = useState(false)
  const [quoteProgress, setQuoteProgress] = useState<{
    done: number
    total: number
  } | null>(null)
  const [refreshProgress, setRefreshProgress] = useState<{
    done: number
    total: number
    phase?: string
  } | null>(null)
  const [refreshStatus, setRefreshStatus] = useState<string | null>(null)
  const refreshAbortRef = useRef<AbortController | null>(null)
  const refreshReqRef = useRef(0)

  const applyProgress = useCallback(
    (row: {
      done?: number
      total?: number
      phase?: string
      message?: string
      symbol?: string
      name?: string
    }) => {
      setRefreshProgress({
        done: row.done ?? 0,
        total: row.total ?? 0,
        phase: row.phase,
      })
      if (row.phase === 'universe') {
        setRefreshStatus(row.message || '拉取候选池…')
        return
      }
      if (row.phase === 'screen') {
        setRefreshStatus(row.message || '粗筛精算名单…')
        return
      }
      if (row.phase === 'persist') {
        setRefreshStatus(row.message || '写入归档…')
        return
      }
      if (row.phase === 'precise') {
        setRefreshStatus(
          row.symbol
            ? `精算 ${row.done ?? 0}/${row.total ?? 0} · ${row.name || row.symbol}`
            : row.message || `精算 ${row.done ?? 0}/${row.total ?? 0}`,
        )
        return
      }
      if (row.message) setRefreshStatus(row.message)
    },
    [],
  )

  const attachRefreshStream = useCallback(
    async (jobId: string | null, reqId: number, ac: AbortController) => {
      let completed = false
      let failed = false

      const handlers = {
        onMeta: (meta: { phase?: string }) => {
          if (reqId !== refreshReqRef.current) return
          setRefreshProgress({
            done: 0,
            total: 0,
            phase: meta.phase || 'universe',
          })
          setRefreshStatus('后台刷新进行中…')
        },
        onProgress: (row: {
          done?: number
          total?: number
          phase?: string
          message?: string
          symbol?: string
          name?: string
        }) => {
          if (reqId !== refreshReqRef.current) return
          applyProgress(row)
        },
        onDone: () => {
          completed = true
        },
        onError: (detail: string) => {
          failed = true
          if (reqId !== refreshReqRef.current) return
          setError(detail)
          setLoading(false)
          setRefreshProgress(null)
        },
      }

      if (jobId) {
        await streamRecommendationsRefreshJob(jobId, handlers, ac.signal)
      } else {
        await streamRecommendationsRefresh(10, 'all', handlers, ac.signal)
      }

      if (failed || reqId !== refreshReqRef.current) return
      if (!completed) {
        // 仅断开 SSE，后台任务可能仍在跑
        setLoading(false)
        setRefreshProgress(null)
        setRefreshStatus('已断开进度连接；后台仍在刷新，稍后回来可查看结果')
        return
      }

      setRefreshStatus('刷新完成，正在加载归档…')
      try {
        const payload = await fetchRecommendations(10, 'all', false)
        if (reqId !== refreshReqRef.current) return
        setData(payload)
        setRefreshStatus('刷新完成')
      } catch (err) {
        if (reqId !== refreshReqRef.current) return
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        if (reqId === refreshReqRef.current) {
          setLoading(false)
          setRefreshProgress(null)
          setTimeout(() => {
            if (reqId === refreshReqRef.current) setRefreshStatus(null)
          }, 1500)
        }
      }
    },
    [applyProgress],
  )

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    setRefreshProgress(null)
    setRefreshStatus(null)
    try {
      const active = await fetchActiveRecommendationsRefresh()
      if (active.job && (active.job.status === 'queued' || active.job.status === 'running')) {
        const reqId = ++refreshReqRef.current
        refreshAbortRef.current?.abort()
        const ac = new AbortController()
        refreshAbortRef.current = ac
        setRefreshStatus('检测到进行中的刷新，正在续订进度…')
        const p = active.job.progress || {}
        setRefreshProgress({
          done: p.done ?? 0,
          total: p.total ?? 0,
          phase: p.phase,
        })
        await attachRefreshStream(active.job.job_id, reqId, ac)
        return
      }
      const payload = await fetchRecommendations(10, 'all', false)
      setData(payload)
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [attachRefreshStream])

  const refreshPool = useCallback(async () => {
    const reqId = ++refreshReqRef.current
    refreshAbortRef.current?.abort()
    const ac = new AbortController()
    refreshAbortRef.current = ac
    setLoading(true)
    setError(null)
    setRefreshStatus('正在启动后台刷新…')
    setRefreshProgress({ done: 0, total: 0, phase: 'universe' })
    try {
      // 兼容入口：服务端会建后台任务；断线后任务继续，回页可续订
      await attachRefreshStream(null, reqId, ac)
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return
      if (reqId !== refreshReqRef.current) return
      setError(err instanceof Error ? err.message : String(err))
      setLoading(false)
      setRefreshProgress(null)
    }
  }, [attachRefreshStream])

  useEffect(() => {
    void load()
    return () => {
      refreshAbortRef.current?.abort()
    }
  }, [load])

  const board = data?.boards?.[tab]
  const items = board?.items ?? []

  const needQuotes = useMemo(() => {
    if (!data?.boards) return false
    return Object.values(data.boards).some((b) =>
      (b.items || []).some((it) => it.close == null || it.day_chg_pct == null),
    )
  }, [data])

  async function loadQuotes() {
    if (!data || quoteLoading) return
    setQuoteLoading(true)
    setError(null)
    setQuoteProgress({ done: 0, total: 0 })
    const tradeDate = data.trade_date || data.snapshot?.trade_date
    try {
      await streamRecQuotes(tradeDate, 'all', {
        onMeta: (meta) => setQuoteProgress({ done: 0, total: meta.total }),
        onQuote: (q) => {
          setQuoteProgress({ done: q.done, total: q.total })
          setData((prev) =>
            prev
              ? patchQuote(prev, q.symbol, {
                  close: q.close ?? undefined,
                  prev_close: q.prev_close,
                  day_chg_pct: q.day_chg_pct,
                  as_of: q.as_of || prev.as_of || undefined,
                })
              : prev,
          )
        },
        onError: (detail) => setError(detail),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setQuoteLoading(false)
      setQuoteProgress(null)
    }
  }

  async function handleOneClick(mode: 'balanced' | 'full' = 'balanced') {
    const maxCount =
      limitEnabled && Number.isFinite(maxBuyCount) && maxBuyCount >= 1
        ? Math.floor(maxBuyCount)
        : null
    if (limitEnabled && (maxCount == null || maxCount < 1)) {
      setError('请填写有效的最大买入个数（≥1）')
      return
    }
    if (mode === 'full') {
      const limitHint = maxCount ? `，最多 ${maxCount} 只` : ''
      const ok = window.confirm(
        `将用模拟盘剩余现金，按评分尽量买满今日 ETF/沪深推荐（不含科创${limitHint}）。确认继续？`,
      )
      if (!ok) return
    }
    setBuying(true)
    setBuyMsg(null)
    setError(null)
    setBuyProgress({ done: 0, total: 0 })
    try {
      await streamOneClickBuy(
        'all',
        {
          onMeta: (meta) => {
            setBuyProgress({ done: 0, total: meta.total })
            const limitNote =
              meta.max_count != null ? `，限 ${meta.max_count} 只` : ''
            setBuyMsg(
              mode === 'full'
                ? `满仓中，可用现金 ${meta.cash?.toFixed?.(2) ?? meta.cash}${limitNote}…`
                : `买入中，可用现金 ${meta.cash?.toFixed?.(2) ?? meta.cash}${limitNote}…`,
            )
          },
          onProgress: (row) => {
            setBuyProgress({ done: row.done, total: row.total })
            if (row.message) setBuyMsg(row.message)
          },
          onTrade: (row) => {
            setBuyProgress({ done: row.done, total: row.total })
            setBuyMsg(`已买入 ${row.trade.symbol} × ${row.trade.qty} @ ${row.trade.price}`)
          },
          onSkip: (row) => setBuyProgress({ done: row.done, total: row.total }),
          onDone: (done) => {
            const limitNote = maxCount ? `，最多 ${maxCount} 只` : ''
            if (mode === 'full') {
              const spent =
                done.spent != null
                  ? `，动用 ${done.spent.toFixed(2)}，剩余现金 ${done.cash_left?.toFixed(2) ?? '—'}`
                  : ''
              setBuyMsg(
                `一键满仓完成 ${done.trades_count} 笔（仅 ETF/沪深${limitNote}` +
                  (done.skipped ? `，跳过 ${done.skipped}` : '') +
                  `${spent}），推荐日 ${done.rec_date || '—'}，可在模拟盘查看`,
              )
            } else {
              setBuyMsg(
                `一键买入完成 ${done.trades_count} 笔（仅 ETF/沪深${limitNote}` +
                  (done.skipped ? `，跳过 ${done.skipped}` : '') +
                  `），推荐日 ${done.rec_date || '—'}，可在模拟盘查看`,
              )
            }
          },
          onError: (detail) => setError(detail),
        },
        undefined,
        mode,
        maxCount,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBuying(false)
      setBuyProgress(null)
    }
  }

  const buyPct =
    buyProgress && buyProgress.total > 0
      ? Math.round((buyProgress.done / buyProgress.total) * 100)
      : 0
  const quotePct =
    quoteProgress && quoteProgress.total > 0
      ? Math.round((quoteProgress.done / quoteProgress.total) * 100)
      : 0
  const refreshPct =
    refreshProgress && refreshProgress.total > 0
      ? Math.round((refreshProgress.done / refreshProgress.total) * 100)
      : refreshProgress?.phase === 'universe' || refreshProgress?.phase === 'screen'
        ? Math.min(12, (refreshProgress.done || 0) * 6)
        : 0

  const recommendationDate =
    data?.trade_date || data?.snapshot?.trade_date || data?.as_of?.slice(0, 10) || '—'
  const archiveStatus = data?.snapshot?.from_cache
    ? '来自归档'
    : data?.snapshot?.saved
      ? '已归档'
      : data?.snapshot?.reason
        ? '未归档'
        : '归档状态未知'

  const metaLine = (
    <>
      {board
        ? `大池 ${board.pool_size ?? '—'} → 精算 ${board.precise_size ?? board.scanned} → 推荐 ${board.count}`
        : ''}
      {data?.trade_date || data?.snapshot?.trade_date
        ? ` · 有效交易日 ${data.trade_date || data.snapshot?.trade_date}`
        : ''}
      {data?.as_of ? ` · 截至 ${data.as_of}` : ''}
      {data?.mode ? ` · ${data.mode}` : ''}
      {data?.snapshot?.from_cache
        ? ' · 来自归档（未重算）'
        : data?.snapshot?.saved
          ? ' · 已写入/覆盖归档'
          : data?.snapshot?.reason
            ? ` · 未归档：${data.snapshot.reason}`
            : ''}
      {needQuotes ? ' · 缺日涨幅时可点「加载价格/日涨幅」' : ''}
      {(data?.errors?.length ?? 0) > 0
        ? ` · 精算失败 ${data!.errors!.length}（已尽量用粗分兜底）`
        : ''}
      {data?.universe_source ? ` · 源 ${data.universe_source}` : ''}
    </>
  )

  return (
    <section className="page recommendations-page">
      <div className="page-hero">
        <MobileDisclosure summary="说明" className="recommendation-hero-desc">
          <p>
            大池粗筛 + Top 精算；按有效交易日归档。点「刷新候选池」会在后台跑完（关页面也继续），稍后回来可看新结果。
          </p>
          {data && !loading ? (
            <div className="recommendation-summary" aria-label="推荐摘要">
              <div className="summary-stat">
                <span>大池</span>
                <strong>{board?.pool_size ?? '—'}</strong>
              </div>
              <div className="summary-stat">
                <span>精算</span>
                <strong>{board?.precise_size ?? board?.scanned ?? '—'}</strong>
              </div>
              <div className="summary-stat">
                <span>推荐</span>
                <strong>{board?.count ?? 0}</strong>
              </div>
            </div>
          ) : null}
        </MobileDisclosure>
        {data && !loading ? (
          <div className="recommendation-mobile-meta" aria-label="推荐日期与归档状态">
            <span>推荐日 {recommendationDate}</span>
            <span>{archiveStatus}</span>
          </div>
        ) : null}
      </div>

      <div className="form-actions">
        <div className="board-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`board-tab${tab === t.id ? ' active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {data?.boards?.[t.id] != null
                ? ` (${data.boards[t.id].count})`
                : ''}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="btn ghost"
          disabled={loading || buying}
          onClick={() => void refreshPool()}
        >
          {loading && refreshProgress
            ? refreshProgress.phase === 'precise' && refreshProgress.total
              ? `精算 ${refreshProgress.done}/${refreshProgress.total}`
              : '刷新中…'
            : loading
              ? '加载中…'
              : '刷新候选池'}
        </button>
        {needQuotes ? (
          <button
            type="button"
            className="btn ghost"
            disabled={loading || quoteLoading || !items.length}
            onClick={loadQuotes}
          >
            {quoteLoading && quoteProgress?.total
              ? `行情 ${quoteProgress.done}/${quoteProgress.total}`
              : quoteLoading
                ? '加载行情…'
                : '加载价格/日涨幅'}
          </button>
        ) : null}
        <button
          type="button"
          className="btn"
          disabled={loading || buying || !items.length}
          onClick={() => handleOneClick('balanced')}
        >
          {buying
            ? buyProgress && buyProgress.total
              ? `买入中 ${buyProgress.done}/${buyProgress.total}`
              : '买入中…'
            : '一键买入 ETF/沪深'}
        </button>
        <button
          type="button"
          className="btn"
          disabled={loading || buying || !items.length}
          onClick={() => handleOneClick('full')}
        >
          {buying ? '满仓中…' : '一键满仓 ETF/沪深'}
        </button>
      </div>

      <div className="buy-cap" role="group" aria-label="买入股票个数上限">
        <span className="buy-cap-label">买入股票个数上限</span>
        <div className="buy-cap-seg">
          <button
            type="button"
            className={`buy-cap-opt${!limitEnabled ? ' active' : ''}`}
            disabled={buying}
            onClick={() => setLimitEnabled(false)}
          >
            不限
          </button>
          <button
            type="button"
            className={`buy-cap-opt${limitEnabled ? ' active' : ''}`}
            disabled={buying}
            onClick={() => setLimitEnabled(true)}
          >
            限制
          </button>
        </div>
        {limitEnabled ? (
          <div className="buy-cap-stepper">
            <button
              type="button"
              className="buy-cap-step"
              disabled={buying || maxBuyCount <= 1}
              aria-label="减少"
              onClick={() => setMaxBuyCount((n) => Math.max(1, Math.floor(n) - 1))}
            >
              −
            </button>
            <input
              className="buy-cap-value"
              type="number"
              min={1}
              max={200}
              step={1}
              value={maxBuyCount}
              disabled={buying}
              aria-label="最多买入只数"
              onChange={(e) => {
                const v = Number(e.target.value)
                if (!Number.isFinite(v)) return
                setMaxBuyCount(Math.min(200, Math.max(1, Math.floor(v))))
              }}
            />
            <button
              type="button"
              className="buy-cap-step"
              disabled={buying || maxBuyCount >= 200}
              aria-label="增加"
              onClick={() => setMaxBuyCount((n) => Math.min(200, Math.floor(n) + 1))}
            >
              +
            </button>
            <span className="buy-cap-unit">只</span>
          </div>
        ) : null}
      </div>

      {quoteLoading && quoteProgress && quoteProgress.total > 0 ? (
        <div className="progress-bar" aria-valuenow={quotePct}>
          <div className="progress-bar-fill" style={{ width: `${quotePct}%` }} />
          <span className="progress-bar-label">
            行情 {quoteProgress.done}/{quoteProgress.total}（{quotePct}%）
          </span>
        </div>
      ) : null}

      {buying && buyProgress && buyProgress.total > 0 ? (
        <div className="progress-bar" aria-valuenow={buyPct} aria-valuemin={0} aria-valuemax={100}>
          <div className="progress-bar-fill" style={{ width: `${buyPct}%` }} />
          <span className="progress-bar-label">
            {buyProgress.done}/{buyProgress.total}（{buyPct}%）
          </span>
        </div>
      ) : null}

      {loading && refreshProgress ? (
        <div
          className="progress-bar"
          aria-valuenow={refreshPct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="progress-bar-fill"
            style={{
              width: `${
                refreshProgress.phase === 'precise' && refreshProgress.total > 0
                  ? refreshPct
                  : Math.max(8, refreshPct || 8)
              }%`,
            }}
          />
          <span className="progress-bar-label">
            {refreshProgress.phase === 'precise' && refreshProgress.total > 0
              ? `精算 ${refreshProgress.done}/${refreshProgress.total}（${refreshPct}%）`
              : refreshProgress.phase === 'universe'
                ? '拉取候选池…'
                : refreshProgress.phase === 'screen'
                  ? '粗筛中…'
                  : refreshProgress.phase === 'persist'
                    ? '写入归档…'
                    : '刷新中…'}
          </span>
        </div>
      ) : null}

      {loading && !refreshProgress ? (
        <p className="status">加载今日关注…（无归档时会粗筛精算；有归档则直接读库）</p>
      ) : null}
      {refreshStatus ? <p className="status">{refreshStatus}</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {buyMsg ? <p className="status ok">{buyMsg}</p> : null}

      {data && !loading ? (
        <>
          <MobileDisclosure summary="筛选与数据源">
            <p className="meta-line">{metaLine}</p>
          </MobileDisclosure>
          <ResponsiveDataView
            storageKey="advisor_recommendations_view"
            label="推荐"
            cards={
              items.length ? (
                <div className="recommendation-card-list">
                  {items.map((item) => (
                    <RecommendationCard key={item.symbol} item={item} />
                  ))}
                </div>
              ) : (
                <p className="status">本板暂无推荐，可稍后重试或调低买入阈值。</p>
              )
            }
            table={<BoardTable items={items} />}
          />
          {!items.length && (data.errors?.length ?? 0) > 0 ? (
            <p className="status error">
              精算日线全部失败：{data.errors![0]?.error || '未知错误'}
            </p>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
