import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchLimitUp,
  fetchRegimeSentiment,
  formatLimitUpChg,
  formatLimitUpMoney,
  shouldShowTodayTable,
  statusLabel,
  type LimitUpLadderTier,
  type LimitUpResponse,
  type LimitUpTodayItem,
  type RegimeSentiment,
} from '../api'
import { useMediaQuery } from '../components/ResponsiveDataView'
import { explorerKlineUrl } from '../explorerLinks'

const POLL_MS = 10_000
const PC_QUERY = '(min-width: 900px)'
const SENTIMENT_LABELS: Record<string, string> = {
  ice: '情绪冰点',
  strengthen: '情绪增强',
  climax: '情绪高潮',
  ebb: '情绪退潮',
  repair: '情绪修复',
  neutral: '情绪中性',
}

function chgClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return ''
  return v > 0 ? 'up' : 'down'
}

function sentimentLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return SENTIMENT_LABELS[value] || value
}

function sentimentScore(data: RegimeSentiment | null): string {
  const raw = data?.metrics?.sentiment_score
  return typeof raw === 'number' && Number.isFinite(raw) ? raw.toFixed(2) : '—'
}

/** PC 默认：≥2 连板展开，1 连板折叠。 */
export function defaultTierExpanded(boardCount: number): boolean {
  return boardCount >= 2
}

function TodayTable({ today }: { today: LimitUpTodayItem[] }) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>标记</th>
            <th>名称</th>
            <th>代码</th>
            <th>当日涨幅</th>
            <th>连板</th>
            <th>主力流入</th>
            <th>主力流出</th>
            <th>净流入</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {today.map((row) => (
            <tr key={row.symbol}>
              <td>
                <span
                  className={
                    row.status === 'sealed'
                      ? 'limitup-badge sealed'
                      : 'limitup-badge broken'
                  }
                >
                  {statusLabel(row.status)}
                </span>
              </td>
              <td>{row.name}</td>
              <td className="mono">{row.symbol}</td>
              <td className={chgClass(row.day_chg_pct)}>
                {formatLimitUpChg(row.day_chg_pct)}
              </td>
              <td>{row.board_count}</td>
              <td>{formatLimitUpMoney(row.main_inflow)}</td>
              <td>{formatLimitUpMoney(row.main_outflow)}</td>
              <td className={chgClass(row.main_net_inflow)}>
                {formatLimitUpMoney(row.main_net_inflow)}
              </td>
              <td>
                <a
                  className="text-link"
                  href={explorerKlineUrl(row.symbol)}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看K线
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {today.length === 0 ? <p className="muted">暂无涨停/炸板数据</p> : null}
    </div>
  )
}

function TierTable({
  tier,
  scrollable,
}: {
  tier: LimitUpLadderTier
  scrollable: boolean
}) {
  return (
    <div
      className={
        scrollable ? 'table-wrap limitup-tier-scroll' : 'table-wrap'
      }
    >
      <table className="data-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>代码</th>
            <th>当日涨幅</th>
            <th>主力流入</th>
            <th>主力流出</th>
            <th>净流入</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {tier.items.map((item) => (
            <tr key={item.symbol}>
              <td>{item.name}</td>
              <td className="mono">{item.symbol}</td>
              <td className={chgClass(item.day_chg_pct)}>
                {formatLimitUpChg(item.day_chg_pct)}
              </td>
              <td>{formatLimitUpMoney(item.main_inflow)}</td>
              <td>{formatLimitUpMoney(item.main_outflow)}</td>
              <td className={chgClass(item.main_net_inflow)}>
                {formatLimitUpMoney(item.main_net_inflow)}
              </td>
              <td>
                <a
                  className="text-link"
                  href={explorerKlineUrl(item.symbol)}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看K线
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function LimitUpPage() {
  const isPc = useMediaQuery(PC_QUERY)
  const [data, setData] = useState<LimitUpResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tierOpen, setTierOpen] = useState<Record<number, boolean>>({})
  const [todayExpanded, setTodayExpanded] = useState(false)
  const [sentiment, setSentiment] = useState<RegimeSentiment | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetchLimitUp()
      setData(res)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    void load()
  }, [load])

  useEffect(() => {
    let alive = true
    fetchRegimeSentiment()
      .then((res) => {
        if (alive) setSentiment(res)
      })
      .catch(() => {
        if (alive) setSentiment(null)
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    const isTrading = shouldShowTodayTable(data?.session)
    if (!isTrading) return

    const tick = () => {
      if (document.visibilityState === 'hidden') return
      void load()
    }
    const id = window.setInterval(tick, POLL_MS)
    const onVis = () => {
      if (document.visibilityState === 'visible') tick()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [data?.session, load])

  const isTrading = shouldShowTodayTable(data?.session)
  const today = data?.today ?? []
  const ladder = data?.ladder ?? []
  const sealedCount = today.filter((r) => r.status === 'sealed').length
  const brokenCount = today.filter((r) => r.status === 'broken').length

  const resolveTierExpanded = (
    boardCount: number,
    overrides: Record<number, boolean>,
  ) => {
    if (!isPc) return true
    if (Object.prototype.hasOwnProperty.call(overrides, boardCount)) {
      return overrides[boardCount]
    }
    return defaultTierExpanded(boardCount)
  }

  const isTierExpanded = (boardCount: number) =>
    resolveTierExpanded(boardCount, tierOpen)

  const toggleTier = (boardCount: number) => {
    setTierOpen((prev) => ({
      ...prev,
      [boardCount]: !resolveTierExpanded(boardCount, prev),
    }))
  }

  const showTodayBody = !isPc || todayExpanded

  const todaySection = (
    <div className="limitup-section" data-testid="today-section">
      {isPc ? (
        <button
          type="button"
          className="limitup-fold-btn"
          aria-expanded={todayExpanded}
          onClick={() => setTodayExpanded((v) => !v)}
        >
          <span>
            当天涨停 · 封板 {sealedCount} / 炸板 {brokenCount}
          </span>
          <span className="limitup-fold-chevron" aria-hidden="true">
            {todayExpanded ? '▾' : '▸'}
          </span>
        </button>
      ) : (
        <h3>当天涨停</h3>
      )}
      {showTodayBody ? (
        !isTrading ? (
          <p className="muted" data-testid="today-hidden">
            非交易时段不展示「当天涨停」。连板看板仍可查看。
          </p>
        ) : (
          <TodayTable today={today} />
        )
      ) : null}
    </div>
  )

  const ladderSection = (
    <div className="limitup-section" data-testid="ladder-section">
      <h3>连板看板</h3>
      {ladder.length === 0 ? (
        <p className="muted">暂无连板数据</p>
      ) : (
        <div className="limitup-ladder">
          {ladder.map((tier) => {
            const expanded = isTierExpanded(tier.board_count)
            const label = `${tier.board_count} 连板 · ${tier.items.length} 只`
            return (
              <div
                key={tier.board_count}
                className="limitup-tier"
                data-testid={`tier-${tier.board_count}`}
                data-expanded={expanded ? 'true' : 'false'}
              >
                {isPc ? (
                  <button
                    type="button"
                    className="limitup-fold-btn"
                    aria-expanded={expanded}
                    onClick={() => toggleTier(tier.board_count)}
                  >
                    <span>{label}</span>
                    <span className="limitup-fold-chevron" aria-hidden="true">
                      {expanded ? '▾' : '▸'}
                    </span>
                  </button>
                ) : (
                  <h4>{label}</h4>
                )}
                {expanded ? (
                  <TierTable
                    tier={tier}
                    scrollable={isPc && tier.board_count === 1}
                  />
                ) : null}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )

  return (
    <section className={`page${isPc ? ' limitup-page--pc' : ' limitup-page--narrow'}`}>
      <div className="page-hero">
        <p>
          A 股涨停池与炸板池：连板看板按当前连板数分档；交易时段可查看「当天涨停」并约
          10 秒刷新，收盘后连板结构仍可查看。
        </p>
      </div>

      <div className="diag-block">
        <div className="form-actions" style={{ marginTop: 0 }}>
          <h2 className="section-title">打板</h2>
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
          {data?.date ? `池日期 ${data.date}` : '—'}
          {data?.as_of
            ? ` · 更新 ${new Date(data.as_of).toLocaleString('zh-CN', { hour12: false })}`
            : ''}
          {isTrading ? ' · 交易中 · 约 10 秒刷新' : ' · 非交易时段'}
        </p>
        <div className="meta-line" role="status">
          <span>市场情绪</span>
          {' · '}
          <strong>{sentimentLabel(sentiment?.sentiment_cycle)}</strong>
          {' · '}
          <span className="mono">{sentimentScore(sentiment)}</span>
          {' · '}
          <Link className="text-link" to="/regime">
            查看市场状态
          </Link>
        </div>
        {error ? <p className="status error">{error}</p> : null}
        {loading && !data ? <p className="status">正在拉取涨停数据…</p> : null}

        {isPc ? (
          <>
            {ladderSection}
            {todaySection}
          </>
        ) : (
          <>
            {todaySection}
            {ladderSection}
          </>
        )}
      </div>
    </section>
  )
}
