import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  deletePaperPosition,
  fetchPaper,
  fetchPaperPnl,
  fetchOneClickPerf,
  fetchPaperTrades,
  formatPct,
  paperOrder,
  resetPaper,
  sellAllPaperPositions,
  sellPaperPosition,
  streamPaperMarkToMarket,
  type PaperAccount,
} from '../api'
import { MobileDisclosure } from '../components/MobileDisclosure'
import {
  PaperPerformanceCard,
  PaperPositionCard,
  PaperTradeCard,
} from '../components/PaperCards'
import { ResponsiveDataView } from '../components/ResponsiveDataView'
import { explorerKlineUrl } from '../explorerLinks'

const TRADE_PAGE_SIZE = 20
const PERF_PAGE_SIZE = 20

export default function PaperPage() {
  const [account, setAccount] = useState<PaperAccount | null>(null)
  const [cashInput, setCashInput] = useState('100000')
  const [symbol, setSymbol] = useState('')
  const [qty, setQty] = useState(100)
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [trades, setTrades] = useState<Record<string, unknown>[]>([])
  const [tradePage, setTradePage] = useState(1)
  const [tradeTotal, setTradeTotal] = useState(0)
  const [tradePages, setTradePages] = useState(1)
  const [tradesLoading, setTradesLoading] = useState(false)
  const [tradesError, setTradesError] = useState<string | null>(null)
  const [perfRows, setPerfRows] = useState<Record<string, unknown>[]>([])
  const [perfPage, setPerfPage] = useState(1)
  const [perfPages, setPerfPages] = useState(1)
  const [perfTotal, setPerfTotal] = useState(0)
  const [perfTradesCount, setPerfTradesCount] = useState(0)
  const [perfEquity, setPerfEquity] = useState<number | null>(null)
  const [perfLoading, setPerfLoading] = useState(false)
  const [perfError, setPerfError] = useState<string | null>(null)
  const [pnl, setPnl] = useState<{
    total: { pnl: number; return_pct: number | null }
    historical: {
      total: { pnl: number; realized: number; unrealized: number; return_pct: number | null }
      one_click: { pnl: number; realized: number; unrealized: number; return_pct: number | null }
      manual: { pnl: number; realized: number; unrealized: number; return_pct: number | null }
    }
    holding: {
      total: {
        pnl: number
        open_cost: number
        open_market_value: number
        return_pct: number | null
      }
      one_click: {
        pnl: number
        open_cost: number
        open_market_value: number
        return_pct: number | null
      }
      manual: {
        pnl: number
        open_cost: number
        open_market_value: number
        return_pct: number | null
      }
    }
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [marking, setMarking] = useState(false)
  const [sellingAll, setSellingAll] = useState(false)
  const [markProgress, setMarkProgress] = useState<{ done: number; total: number } | null>(
    null,
  )

  const loadTrades = useCallback((page: number) => {
    setTradesLoading(true)
    setTradesError(null)
    fetchPaperTrades({ page, pageSize: TRADE_PAGE_SIZE })
      .then((t) => {
        setTrades(t.trades)
        setTradePage(t.page)
        setTradeTotal(t.total)
        setTradePages(t.pages)
      })
      .catch((err: Error) => setTradesError(err.message))
      .finally(() => setTradesLoading(false))
  }, [])

  const loadPerf = useCallback((page: number) => {
    setPerfLoading(true)
    setPerfError(null)
    fetchOneClickPerf({ page, pageSize: PERF_PAGE_SIZE })
      .then((p) => {
        setPerfRows(p.open_rows)
        setPerfPage(p.page)
        setPerfPages(p.pages)
        setPerfTotal(p.open_total)
        setPerfTradesCount(p.trades_count)
        setPerfEquity(p.account_equity)
      })
      .catch((err: Error) => setPerfError(err.message))
      .finally(() => setPerfLoading(false))
  }, [])

  function reload() {
    setLoading(true)
    Promise.all([fetchPaper(), fetchPaperPnl()])
      .then(([a, p]) => {
        setAccount(a)
        setCashInput(String(a.initial_cash))
        setPnl(p)
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
    loadTrades(1)
    loadPerf(1)
  }

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once
  }, [])

  async function refreshMarkToMarket() {
    if (marking) return
    setMarking(true)
    setError(null)
    setMessage(null)
    setMarkProgress({ done: 0, total: 0 })
    try {
      await streamPaperMarkToMarket({
        onMeta: (meta) => setMarkProgress({ done: 0, total: meta.total }),
        onPosition: (row) => {
          setMarkProgress({ done: row.done, total: row.total })
          setAccount((prev) => {
            if (!prev) return prev
            const positions = prev.positions.map((p) =>
              p.symbol === row.symbol
                ? {
                    ...p,
                    last: row.last,
                    market_value: row.market_value,
                    pnl: row.pnl,
                    pnl_pct: row.pnl_pct,
                    name: row.name || p.name,
                    marked: true,
                  }
                : p,
            )
            const market_value = positions.reduce(
              (s, p) => s + (p.market_value || 0),
              0,
            )
            return {
              ...prev,
              positions,
              market_value: Math.round(market_value * 100) / 100,
              equity: Math.round((prev.cash + market_value) * 100) / 100,
            }
          })
        },
        onDone: (done) => {
          setAccount(done.account)
          setMessage('市值已刷新并写入数据库')
          fetchPaperPnl()
            .then(setPnl)
            .catch(() => undefined)
        },
        onError: (detail) => setError(detail),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setMarking(false)
      setMarkProgress(null)
    }
  }

  async function onReset(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      const a = await resetPaper(Number(cashInput) || 100000)
      setAccount(a)
      setMessage('模拟盘已重置')
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function onOrder(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setMessage(null)
    try {
      const res = await paperOrder({ symbol, side, qty: Number(qty) })
      setAccount(res.account)
      setMessage(`${side === 'buy' ? '买入' : '卖出'}成功`)
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function onSellPosition(p: PaperAccount['positions'][number]) {
    const raw = window.prompt(
      `卖出 ${p.name || p.symbol}（持有 ${p.qty}）\n请输入卖出数量（默认全部）：`,
      String(p.qty),
    )
    if (raw == null) return
    const sellQty = Number(String(raw).trim() || p.qty)
    if (!Number.isFinite(sellQty) || sellQty <= 0) {
      setError('卖出数量无效')
      return
    }
    if (sellQty > p.qty + 1e-9) {
      setError('卖出数量超过持仓')
      return
    }
    setError(null)
    setMessage(null)
    try {
      const res = await sellPaperPosition(
        p.symbol,
        sellQty,
        p.last != null ? Number(p.last) : undefined,
      )
      setAccount(res.account)
      setMessage(`已卖出 ${p.symbol} × ${sellQty}`)
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function onDeletePosition(p: PaperAccount['positions'][number]) {
    const ok = window.confirm(
      `删除 ${p.name || p.symbol}？\n将当作从未买过：回补资金、作废相关成交，不计入收益。`,
    )
    if (!ok) return
    setError(null)
    setMessage(null)
    try {
      const a = await deletePaperPosition(p.symbol)
      setAccount(a)
      setMessage(`已删除持仓 ${p.symbol}`)
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  async function onSellAll() {
    const n = account?.positions?.length || 0
    if (!n) return
    const ok = window.confirm(`一键卖出全部 ${n} 只持仓？将按库内现价成交并计入历史收益。`)
    if (!ok) return
    setSellingAll(true)
    setError(null)
    setMessage(null)
    try {
      const res = await sellAllPaperPositions()
      setAccount(res.account)
      const failHint = res.failed ? `，失败 ${res.failed} 只` : ''
      setMessage(`一键卖出完成：成功 ${res.sold} 只${failHint}`)
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSellingAll(false)
    }
  }

  const markPct =
    markProgress && markProgress.total > 0
      ? Math.round((markProgress.done / markProgress.total) * 100)
      : 0

  return (
    <section className="page paper-page">
      <div className="page-hero">
        <p>
          默认展示库内缓存价；点「刷新市值」才 SSE 逐只拉行情并写回。一键买入成交标记为
          rec_one_click。
        </p>
      </div>

      {loading ? <p className="status">加载中…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {message ? <p className="status ok">{message}</p> : null}

      {account ? (
        <>
          <div className="stat-row paper-account-desktop">
            <div className="stat">
              <span className="metric-label">现金</span>
              <strong className="metric-value">{account.cash.toFixed(2)}</strong>
            </div>
            <div className="stat">
              <span className="metric-label">市值</span>
              <strong className="metric-value">{account.market_value.toFixed(2)}</strong>
            </div>
            <div className="stat">
              <span className="metric-label">总权益</span>
              <strong className="metric-value">{account.equity.toFixed(2)}</strong>
            </div>
          </div>
          <div className="paper-account-summary" aria-label="模拟盘账户摘要">
            <div className="stat paper-account-primary">
              <span className="metric-label">总权益</span>
              <strong className="metric-value">{account.equity.toFixed(2)}</strong>
            </div>
            <div className="stat paper-account-primary">
              <span className="metric-label">总收益</span>
              <strong
                className={`metric-value ${((pnl?.total.pnl ?? 0) || 0) >= 0 ? 'up' : 'down'}`}
              >
                {(pnl?.total.pnl ?? 0) >= 0 ? '+' : ''}
                {(pnl?.total.pnl ?? 0).toFixed(2)}
              </strong>
              {pnl?.total.return_pct != null ? (
                <span className="meta-line">{formatPct(pnl.total.return_pct, 2)}</span>
              ) : null}
            </div>
            <div className="paper-account-secondary">
              <div className="stat">
                <span className="metric-label">现金</span>
                <strong className="metric-value">{account.cash.toFixed(2)}</strong>
              </div>
              <div className="stat">
                <span className="metric-label">市值</span>
                <strong className="metric-value">{account.market_value.toFixed(2)}</strong>
              </div>
            </div>
          </div>
        </>
      ) : null}

      {pnl?.historical && pnl?.holding ? (
        <div className="paper-pnl-sections">
          <section className="paper-pnl-static">
            <p className="meta-line">仅当前未平仓浮盈亏</p>
            <div className="stat-row paper-pnl-stat-row">
              <div className="stat">
                <span className="metric-label">总持仓收益</span>
                <strong
                  className={`metric-value ${(pnl.holding.total.pnl || 0) >= 0 ? 'up' : 'down'}`}
                >
                  {pnl.holding.total.pnl >= 0 ? '+' : ''}
                  {pnl.holding.total.pnl.toFixed(2)}
                </strong>
                <span className="meta-line">
                  {pnl.holding.total.open_cost
                    ? `成本 ${pnl.holding.total.open_cost.toFixed(2)}`
                    : '—'}
                  {pnl.holding.total.return_pct != null
                    ? ` · ${formatPct(pnl.holding.total.return_pct, 2)}`
                    : ''}
                </span>
              </div>
              <div className="stat">
                <span className="metric-label">持仓一键买入收益</span>
                <strong
                  className={`metric-value ${(pnl.holding.one_click.pnl || 0) >= 0 ? 'up' : 'down'}`}
                >
                  {pnl.holding.one_click.pnl >= 0 ? '+' : ''}
                  {pnl.holding.one_click.pnl.toFixed(2)}
                </strong>
                <span className="meta-line">
                  {pnl.holding.one_click.open_cost
                    ? `成本 ${pnl.holding.one_click.open_cost.toFixed(2)}`
                    : '—'}
                  {pnl.holding.one_click.return_pct != null
                    ? ` · ${formatPct(pnl.holding.one_click.return_pct, 2)}`
                    : ''}
                </span>
              </div>
              <div className="stat">
                <span className="metric-label">持仓手动买入收益</span>
                <strong
                  className={`metric-value ${(pnl.holding.manual.pnl || 0) >= 0 ? 'up' : 'down'}`}
                >
                  {pnl.holding.manual.pnl >= 0 ? '+' : ''}
                  {pnl.holding.manual.pnl.toFixed(2)}
                </strong>
                <span className="meta-line">
                  {pnl.holding.manual.open_cost
                    ? `成本 ${pnl.holding.manual.open_cost.toFixed(2)}`
                    : '—'}
                  {pnl.holding.manual.return_pct != null
                    ? ` · ${formatPct(pnl.holding.manual.return_pct, 2)}`
                    : ''}
                </span>
              </div>
            </div>
          </section>

          <MobileDisclosure summary="历史收益" className="paper-pnl-disclosure">
            <p className="meta-line">含持仓浮盈 + 卖出已实现</p>
            <div className="stat-row paper-pnl-stat-row">
              <div className="stat">
                <span className="metric-label">总收益</span>
                <strong
                  className={`metric-value ${(pnl.historical.total.pnl || 0) >= 0 ? 'up' : 'down'}`}
                >
                  {pnl.historical.total.pnl >= 0 ? '+' : ''}
                  {pnl.historical.total.pnl.toFixed(2)}
                </strong>
                <span className="meta-line">
                  浮盈 {pnl.historical.total.unrealized.toFixed(2)} · 已实现{' '}
                  {pnl.historical.total.realized.toFixed(2)}
                  {pnl.historical.total.return_pct != null
                    ? ` · ${formatPct(pnl.historical.total.return_pct, 2)}`
                    : ''}
                </span>
              </div>
              <div className="stat">
                <span className="metric-label">总一键买入收益</span>
                <strong
                  className={`metric-value ${(pnl.historical.one_click.pnl || 0) >= 0 ? 'up' : 'down'}`}
                >
                  {pnl.historical.one_click.pnl >= 0 ? '+' : ''}
                  {pnl.historical.one_click.pnl.toFixed(2)}
                </strong>
                <span className="meta-line">
                  浮盈 {pnl.historical.one_click.unrealized.toFixed(2)} · 已实现{' '}
                  {pnl.historical.one_click.realized.toFixed(2)}
                </span>
              </div>
              <div className="stat">
                <span className="metric-label">总手动买入收益</span>
                <strong
                  className={`metric-value ${(pnl.historical.manual.pnl || 0) >= 0 ? 'up' : 'down'}`}
                >
                  {pnl.historical.manual.pnl >= 0 ? '+' : ''}
                  {pnl.historical.manual.pnl.toFixed(2)}
                </strong>
                <span className="meta-line">
                  浮盈 {pnl.historical.manual.unrealized.toFixed(2)} · 已实现{' '}
                  {pnl.historical.manual.realized.toFixed(2)}
                </span>
              </div>
            </div>
          </MobileDisclosure>
        </div>
      ) : null}

      <div className="paper-primary-actions">
        <button
          className="btn"
          type="button"
          disabled={marking || loading || !(account?.positions?.length)}
          onClick={refreshMarkToMarket}
        >
          {marking && markProgress?.total
            ? `刷新市值 ${markProgress.done}/${markProgress.total}`
            : marking
              ? '刷新中…'
              : '刷新市值'}
        </button>
        <button
          className="btn"
          type="button"
          disabled={sellingAll || marking || loading || !(account?.positions?.length)}
          onClick={onSellAll}
        >
          {sellingAll ? '卖出中…' : '一键卖出'}
        </button>
        <MobileDisclosure summary="更多操作" className="paper-danger-actions">
          <h2 className="section-title">下单</h2>
          <form className="search-row paper-order-form" aria-label="下单" onSubmit={onOrder}>
            <select
              className="input"
              value={side}
              onChange={(e) => setSide(e.target.value as 'buy' | 'sell')}
              style={{ maxWidth: '6rem' }}
            >
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
            </select>
            <input
              className="input mono"
              placeholder="代码"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              style={{ maxWidth: '8rem' }}
            />
            <input
              className="input"
              type="number"
              min={100}
              step={100}
              value={qty}
              onChange={(e) => setQty(Number(e.target.value))}
              style={{ maxWidth: '8rem' }}
            />
            <button className="btn" type="submit">
              提交
            </button>
          </form>
          <form className="form-actions form-actions--aligned" onSubmit={onReset}>
            <input
              className="input"
              type="number"
              min={1000}
              value={cashInput}
              onChange={(e) => setCashInput(e.target.value)}
              style={{ maxWidth: '12rem' }}
            />
            <div className="field-actions">
              <button className="btn ghost" type="submit">
                重置资金（清空持仓）
              </button>
            </div>
          </form>
        </MobileDisclosure>
      </div>

      {marking && markProgress && markProgress.total > 0 ? (
        <div className="progress-bar" aria-valuenow={markPct}>
          <div className="progress-bar-fill" style={{ width: `${markPct}%` }} />
          <span className="progress-bar-label">
            {markProgress.done}/{markProgress.total}（{markPct}%）
          </span>
        </div>
      ) : null}

      <h2 className="section-title">持仓</h2>
      <p className="meta-line">
        现价/市值默认来自库内缓存（成交价或上次刷新）；未刷新前可能接近成本。
      </p>
      <ResponsiveDataView
        storageKey="advisor_paper_positions_view"
        label="持仓"
        cards={
          (account?.positions?.length || 0) > 0 ? (
            <div className="paper-card-list">
              {(account?.positions || []).map((p) => (
                <PaperPositionCard
                  key={p.symbol}
                  position={p}
                  onSell={onSellPosition}
                  onDelete={onDeletePosition}
                />
              ))}
            </div>
          ) : !loading ? (
            <p className="muted">暂无持仓</p>
          ) : null
        }
        table={
          <div className="table-wrap paper-positions-table">
            <table className="data-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>数量</th>
                  <th>成本</th>
                  <th>现价</th>
                  <th>市值</th>
                  <th>浮盈亏</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {(account?.positions || []).map((p) => (
                  <tr key={p.symbol}>
                    <td className="mono">{p.symbol}</td>
                    <td>{p.name}</td>
                    <td>{p.qty}</td>
                    <td>{p.cost}</td>
                    <td>{p.last}</td>
                    <td>{p.market_value != null ? p.market_value.toFixed(2) : '—'}</td>
                    <td className={(p.pnl_pct || 0) >= 0 ? 'up' : 'down'}>
                      {formatPct(p.pnl_pct, 2)}
                    </td>
                    <td className="row-actions">
                      <a
                        className="text-link"
                        href={explorerKlineUrl(p.symbol)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        查看K线
                      </a>
                      <button
                        type="button"
                        className="text-link"
                        onClick={() => onSellPosition(p)}
                      >
                        卖出
                      </button>
                      <button
                        type="button"
                        className="text-link danger"
                        onClick={() => onDeletePosition(p)}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
                {!account?.positions?.length && !loading ? (
                  <tr>
                    <td colSpan={8} className="muted">
                      暂无持仓
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        }
      />

      <section className="paper-data-section" aria-label="一键买入跟踪">
        <h2 className="section-title">一键买入跟踪</h2>
        <p className="meta-line">
          一键买入 {perfTradesCount} 笔 · 持仓跟踪 {perfTotal} 条 · 第 {perfPage}/{perfPages}{' '}
          页 · 账户权益 {perfEquity ?? '—'}
          {perfLoading ? ' · 加载中…' : ''}
        </p>
        {perfError ? (
          <p className="status error" role="alert">
            {perfError}
          </p>
        ) : null}
        <ResponsiveDataView
        storageKey="advisor_paper_performance_view"
        label="一键买入跟踪"
        cards={
          perfRows.length ? (
            <div className="paper-card-list">
              {perfRows.map((r, i) => (
                <PaperPerformanceCard
                  key={`${String(r.symbol)}-${String(r.rec_date)}-${i}`}
                  row={r}
                />
              ))}
            </div>
          ) : !perfLoading ? (
            <p className="muted">暂无一键买入持仓</p>
          ) : null
        }
        table={
          <div className="table-wrap paper-performance-table">
            <table className="data-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>推荐日</th>
                  <th>买价</th>
                  <th>现价</th>
                  <th>浮盈亏</th>
                </tr>
              </thead>
              <tbody>
                {perfRows.map((r, i) => (
                  <tr key={`${String(r.symbol)}-${String(r.rec_date)}-${i}`}>
                    <td className="mono">{String(r.symbol)}</td>
                    <td>{String(r.name || '—')}</td>
                    <td>{String(r.rec_date || '—')}</td>
                    <td>{String(r.buy_price ?? '—')}</td>
                    <td>{String(r.last ?? '—')}</td>
                    <td>{formatPct(r.unrealized_pnl_pct as number | null, 2)}</td>
                  </tr>
                ))}
                {!perfRows.length && !perfLoading ? (
                  <tr>
                    <td colSpan={6} className="muted">
                      暂无一键买入持仓
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        }
        />
        <div className="pager">
          <button
            type="button"
            className="btn ghost"
            disabled={perfPage <= 1 || perfLoading}
            onClick={() => loadPerf(perfPage - 1)}
          >
            上一页
          </button>
          <span className="pager-info">
            {perfPage} / {perfPages}
          </span>
          <button
            type="button"
            className="btn ghost"
            disabled={perfPage >= perfPages || perfLoading}
            onClick={() => loadPerf(perfPage + 1)}
          >
            下一页
          </button>
        </div>
      </section>

      <section className="paper-data-section" aria-label="成交记录">
        <h2 className="section-title">成交记录</h2>
        <p className="meta-line">
          共 {tradeTotal} 笔 · 第 {tradePage}/{tradePages} 页
          {tradesLoading ? ' · 加载中…' : ''}
        </p>
        {tradesError ? (
          <p className="status error" role="alert">
            {tradesError}
          </p>
        ) : null}
        <ResponsiveDataView
        storageKey="advisor_paper_trades_view"
        label="成交记录"
        cards={
          trades.length ? (
            <div className="paper-card-list">
              {trades.map((t, i) => (
                <PaperTradeCard key={`${String(t.created_at)}-${String(t.symbol)}-${i}`} trade={t} />
              ))}
            </div>
          ) : !tradesLoading ? (
            <p className="muted">暂无成交</p>
          ) : null
        }
        table={
          <div className="table-wrap paper-trades-table">
            <table className="data-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>方向</th>
                  <th>代码</th>
                  <th>名称</th>
                  <th>数量</th>
                  <th>价格</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={`${String(t.created_at)}-${String(t.symbol)}-${i}`}>
                    <td>{String(t.created_at || '').slice(0, 19).replace('T', ' ')}</td>
                    <td>
                      {String(t.side) === 'buy'
                        ? '买入'
                        : String(t.side) === 'sell'
                          ? '卖出'
                          : String(t.side)}
                    </td>
                    <td className="mono">{String(t.symbol)}</td>
                    <td>{String(t.name || '—')}</td>
                    <td>{String(t.qty)}</td>
                    <td>{String(t.price)}</td>
                    <td>{String(t.source)}</td>
                  </tr>
                ))}
                {!trades.length && !tradesLoading ? (
                  <tr>
                    <td colSpan={7} className="muted">
                      暂无成交
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        }
        />
        <div className="pager">
          <button
            type="button"
            className="btn ghost"
            disabled={tradePage <= 1 || tradesLoading}
            onClick={() => loadTrades(tradePage - 1)}
          >
            上一页
          </button>
          <span className="pager-info">
            {tradePage} / {tradePages}
          </span>
          <button
            type="button"
            className="btn ghost"
            disabled={tradePage >= tradePages || tradesLoading}
            onClick={() => loadTrades(tradePage + 1)}
          >
            下一页
          </button>
        </div>
      </section>
    </section>
  )
}
