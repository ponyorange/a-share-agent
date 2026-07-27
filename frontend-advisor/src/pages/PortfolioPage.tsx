import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  fetchPortfolio,
  fetchPortfolioAdvice,
  fetchPortfolioMarks,
  formatPct,
  formatScore,
  savePortfolio,
  type AdviceItem,
  type PortfolioAdviceResponse,
  type PortfolioMarksResponse,
  type Position,
} from '../api'
import { ActionBadge } from '../components/AdviceCard'
import { explorerKlineUrl } from '../explorerLinks'

/** Editable row: qty/cost as strings so empty field is allowed (avoids Number('') → 0). */
type FormRow = {
  symbol: string
  qty: string
  cost: string
}

const emptyRow = (): FormRow => ({
  symbol: '',
  qty: '',
  cost: '',
})

function positionToForm(p: Position): FormRow {
  return {
    symbol: p.symbol || '',
    qty: p.qty == null || Number.isNaN(Number(p.qty)) ? '' : String(p.qty),
    cost: p.cost == null || Number.isNaN(Number(p.cost)) ? '' : String(p.cost),
  }
}

function parseNonNegNumber(raw: string): number {
  const n = Number(String(raw).trim())
  if (!Number.isFinite(n) || n < 0) return 0
  return n
}

function pnlClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return ''
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return ''
}

function formatMoney(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}`
}

function formatPrice(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return Number(v).toFixed(3)
}

export default function PortfolioPage() {
  const [rows, setRows] = useState<FormRow[]>([emptyRow()])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [diagnosing, setDiagnosing] = useState(false)
  const [advice, setAdvice] = useState<PortfolioAdviceResponse | null>(null)
  const [marks, setMarks] = useState<PortfolioMarksResponse | null>(null)
  const [marksLoading, setMarksLoading] = useState(false)
  const [marksError, setMarksError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const marksInFlight = useRef(false)
  const hasPositions = rows.some((r) => r.symbol.trim().length >= 6)

  const runDiagnosis = useCallback(() => {
    setDiagnosing(true)
    setError(null)
    fetchPortfolioAdvice()
      .then(setAdvice)
      .catch((err: Error) => setError(err.message))
      .finally(() => setDiagnosing(false))
  }, [])

  const refreshMarks = useCallback(async (opts?: { silent?: boolean }) => {
    if (marksInFlight.current) return null
    marksInFlight.current = true
    if (!opts?.silent) setMarksLoading(true)
    setMarksError(null)
    try {
      const res = await fetchPortfolioMarks()
      setMarks(res)
      return res
    } catch (err) {
      setMarksError(err instanceof Error ? err.message : String(err))
      return null
    } finally {
      marksInFlight.current = false
      if (!opts?.silent) setMarksLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPortfolio()
      .then((res) => {
        setRows(res.positions.length ? res.positions.map(positionToForm) : [emptyRow()])
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  // 进入页面：非交易时段刷新一次；交易时段每 3 秒刷新
  useEffect(() => {
    if (loading) return
    let cancelled = false
    let timer: number | null = null
    let first = true

    const schedule = (isTrading: boolean) => {
      if (cancelled || !isTrading) return
      timer = window.setTimeout(() => {
        void tick()
      }, 3000)
    }

    const tick = async () => {
      try {
        if (first) setMarksLoading(true)
        const res = await fetchPortfolioMarks()
        if (cancelled) return
        setMarks(res)
        setMarksError(null)
        schedule(Boolean(res.session?.is_trading))
      } catch (err) {
        if (cancelled) return
        setMarksError(err instanceof Error ? err.message : String(err))
      } finally {
        first = false
        if (!cancelled) setMarksLoading(false)
      }
    }

    void tick()

    return () => {
      cancelled = true
      if (timer != null) window.clearTimeout(timer)
    }
  }, [loading])

  function update(i: number, patch: Partial<FormRow>) {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  function addRow() {
    setRows((prev) => [...prev, emptyRow()])
  }

  function removeRow(i: number) {
    setRows((prev) => (prev.length <= 1 ? [emptyRow()] : prev.filter((_, idx) => idx !== i)))
  }

  async function onSave(e: FormEvent) {
    e.preventDefault()
    setSaving(true)
    setMessage(null)
    setError(null)
    const cleaned = rows
      .map((r) => ({
        symbol: r.symbol.trim(),
        qty: parseNonNegNumber(r.qty),
        cost: parseNonNegNumber(r.cost),
      }))
      .filter((r) => r.symbol.length >= 6)
    try {
      const saved = await savePortfolio(cleaned)
      setRows(saved.positions.length ? saved.positions.map(positionToForm) : [emptyRow()])
      setMessage(`已保存 ${saved.positions.length} 条持仓`)
      if (!saved.positions.length) {
        setAdvice(null)
        setMarks(null)
      } else {
        void refreshMarks({ silent: true })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const trading = Boolean(marks?.session?.is_trading)

  return (
    <section className="page">
      <div className="page-hero">
        <p>持仓市值盘中约 3 秒刷新；非交易时段进入页面刷新一次。诊断需手动点击。</p>
      </div>

      {loading ? <p className="status">加载持仓…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {message ? <p className="status ok">{message}</p> : null}

      <div className="stat-row">
        <div className="stat">
          <span className="metric-label">总市值</span>
          <strong className="metric-value">
            {marks ? marks.total_market_value.toFixed(2) : '—'}
          </strong>
        </div>
        <div className="stat">
          <span className="metric-label">总成本</span>
          <strong className="metric-value">
            {marks ? marks.total_cost.toFixed(2) : '—'}
          </strong>
        </div>
        <div className="stat">
          <span className="metric-label">总收益</span>
          <strong className={`metric-value ${pnlClass(marks?.total_position_pnl)}`}>
            {marks ? formatMoney(marks.total_position_pnl) : '—'}
          </strong>
        </div>
        <div className="stat">
          <span className="metric-label">总收益率</span>
          <strong className={`metric-value ${pnlClass(marks?.total_return_pct)}`}>
            {marks ? formatPct(marks.total_return_pct, 2) : '—'}
          </strong>
        </div>
      </div>

      <div className="diag-block">
        <div className="form-actions" style={{ marginTop: 0 }}>
          <h2 className="section-title">持仓一览</h2>
          <button
            type="button"
            className="btn ghost"
            disabled={marksLoading || loading || !hasPositions}
            onClick={() => void refreshMarks()}
          >
            {marksLoading ? '刷新中…' : '刷新行情'}
          </button>
        </div>
        <p className="meta-line">
          {marks?.updated_at ? `更新 ${marks.updated_at}` : '尚未刷新'}
          {' · '}
          {trading ? '交易中 · 约 3 秒自动刷新' : '非交易时段 · 进入页面刷新一次'}
          {marks ? (
            <>
              {' · '}
              <span className={pnlClass(marks.total_day_pnl)}>
                今日 {formatMoney(marks.total_day_pnl)}
              </span>
            </>
          ) : null}
        </p>
        {marksError ? <p className="status error">{marksError}</p> : null}
        {marksLoading && !marks ? <p className="status">正在拉取行情…</p> : null}
        {marks && marks.items.length === 0 ? (
          <p className="muted">暂无持仓，先在下方编辑并保存。</p>
        ) : null}
        {marks && marks.items.length > 0 ? (
          <div className="table-wrap">
            <table className="data-table portfolio-marks-table">
              <thead>
                <tr>
                  <th>名称/代码</th>
                  <th>市值/股数</th>
                  <th>现价/成本</th>
                  <th>今日盈亏</th>
                  <th>持仓盈亏</th>
                  <th>仓位</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {marks.items.map((item) => (
                  <tr key={item.symbol}>
                    <td>
                      <div className="cell-main">{item.name || '—'}</div>
                      <div className="cell-sub mono">{item.symbol}</div>
                    </td>
                    <td>
                      <div className="cell-main">
                        {item.market_value != null
                          ? item.market_value.toFixed(2)
                          : '—'}
                      </div>
                      <div className="cell-sub">{item.qty}</div>
                    </td>
                    <td>
                      <div className={`cell-main ${pnlClass(item.day_chg_pct)}`}>
                        {formatPrice(item.price)}
                        {item.day_chg_pct != null
                          ? ` (${formatPct(item.day_chg_pct, 2)})`
                          : ''}
                      </div>
                      <div className="cell-sub">{formatPrice(item.cost)}</div>
                    </td>
                    <td className={pnlClass(item.day_pnl)}>
                      <div className="cell-main">{formatMoney(item.day_pnl)}</div>
                      <div className="cell-sub">
                        {item.day_chg_pct == null
                          ? '—'
                          : formatPct(item.day_chg_pct, 2)}
                      </div>
                    </td>
                    <td className={pnlClass(item.position_pnl)}>
                      <div className="cell-main">{formatMoney(item.position_pnl)}</div>
                      <div className="cell-sub">
                        {item.position_pnl_pct == null
                          ? '—'
                          : formatPct(item.position_pnl_pct, 2)}
                      </div>
                    </td>
                    <td>
                      <div className="cell-main">
                        {item.weight == null ? '—' : formatPct(item.weight, 1)}
                      </div>
                    </td>
                    <td>
                      <div className="cell-main">
                        <a
                          className="text-link"
                          href={explorerKlineUrl(item.symbol)}
                          target="_blank"
                          rel="noreferrer"
                        >
                          查看K线
                        </a>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      <div className="diag-block">
        <div className="form-actions" style={{ marginTop: 0 }}>
          <h2 className="section-title">持仓诊断</h2>
          <button
            type="button"
            className="btn"
            disabled={diagnosing || loading || !hasPositions}
            onClick={runDiagnosis}
          >
            {diagnosing ? '诊断中…' : '诊断持仓'}
          </button>
        </div>

        {diagnosing && !advice ? <p className="status">正在诊断全部持仓…</p> : null}

        {advice ? (
          <>
            <p className="meta-line">
              共 {advice.count} 只
              {advice.as_of ? ` · 截至 ${advice.as_of}` : ''}
              {' · '}
              <span className="action-buy">加仓 {advice.summary.add}</span>
              {' / '}
              <span className="action-hold">持有 {advice.summary.hold}</span>
              {' / '}
              <span className="action-sell">卖出 {advice.summary.sell}</span>
              {advice.summary.error ? ` / 失败 ${advice.summary.error}` : ''}
            </p>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>建议</th>
                    <th>评分</th>
                    <th>收盘</th>
                    <th>浮盈亏</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {advice.items.map((item: AdviceItem) => (
                    <tr key={item.symbol}>
                      <td className="mono">{item.symbol}</td>
                      <td>{item.name}</td>
                      <td>
                        <ActionBadge action={item.action} label={item.action_label} />
                      </td>
                      <td>{formatScore(item.score)}</td>
                      <td>{item.close != null ? item.close : '—'}</td>
                      <td className={pnlClass(item.pnl_pct)}>
                        {item.pnl_pct == null ? '—' : formatPct(item.pnl_pct, 2)}
                        {item.pnl != null ? ` (${item.pnl > 0 ? '+' : ''}${item.pnl})` : ''}
                      </td>
                      <td className="rationale-cell">{item.rationale}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {advice.errors?.length ? (
              <p className="status error">
                部分失败：
                {advice.errors.map((e) => `${e.symbol || '?'}: ${e.error}`).join('；')}
              </p>
            ) : null}
          </>
        ) : null}
      </div>

      <form onSubmit={onSave}>
        <h2 className="section-title">编辑持仓</h2>
        <p className="muted">只需填写代码、数量与成本；名称保存时自动获取。</p>
        <div className="table-wrap">
          <table className="data-table editable">
            <thead>
              <tr>
                <th>代码</th>
                <th>数量</th>
                <th>成本</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  <td>
                    <input
                      className="input mono"
                      value={row.symbol}
                      onChange={(e) => update(i, { symbol: e.target.value })}
                      placeholder="510300"
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      type="number"
                      min={0}
                      inputMode="decimal"
                      value={row.qty}
                      onChange={(e) => update(i, { qty: e.target.value })}
                      placeholder="0"
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      type="number"
                      min={0}
                      step="0.001"
                      inputMode="decimal"
                      value={row.cost}
                      onChange={(e) => update(i, { cost: e.target.value })}
                      placeholder="0"
                    />
                  </td>
                  <td>
                    <button type="button" className="btn ghost" onClick={() => removeRow(i)}>
                      删
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="form-actions">
          <button type="button" className="btn ghost" onClick={addRow}>
            添加一行
          </button>
          <button type="submit" className="btn" disabled={saving}>
            {saving ? '保存中…' : '保存持仓'}
          </button>
        </div>
      </form>
    </section>
  )
}
