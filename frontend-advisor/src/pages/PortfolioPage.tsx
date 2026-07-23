import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  fetchPortfolio,
  fetchPortfolioAdvice,
  formatPct,
  formatScore,
  savePortfolio,
  type AdviceItem,
  type PortfolioAdviceResponse,
  type Position,
} from '../api'
import { ActionBadge } from '../components/AdviceCard'

const emptyRow = (): Position => ({
  symbol: '',
  name: '',
  qty: 0,
  cost: 0,
  note: '',
})

function pnlClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return ''
  if (v > 0) return 'up'
  if (v < 0) return 'down'
  return ''
}

export default function PortfolioPage() {
  const [rows, setRows] = useState<Position[]>([emptyRow()])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [diagnosing, setDiagnosing] = useState(false)
  const [advice, setAdvice] = useState<PortfolioAdviceResponse | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const runDiagnosis = useCallback(() => {
    setDiagnosing(true)
    setError(null)
    fetchPortfolioAdvice()
      .then(setAdvice)
      .catch((err: Error) => setError(err.message))
      .finally(() => setDiagnosing(false))
  }, [])

  useEffect(() => {
    fetchPortfolio()
      .then((res) => {
        setRows(res.positions.length ? res.positions : [emptyRow()])
        if (res.positions.length) {
          runDiagnosis()
        }
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [runDiagnosis])

  function update(i: number, patch: Partial<Position>) {
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
        ...r,
        symbol: r.symbol.trim(),
        qty: Number(r.qty) || 0,
        cost: Number(r.cost) || 0,
      }))
      .filter((r) => r.symbol.length >= 6)
    try {
      const saved = await savePortfolio(cleaned)
      setRows(saved.positions.length ? saved.positions : [emptyRow()])
      setMessage(`已保存 ${saved.positions.length} 条持仓`)
      if (saved.positions.length) {
        runDiagnosis()
      } else {
        setAdvice(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <h1>我的持仓</h1>
        <p>进入本页自动诊断全部持仓；按评分给出卖 / 持有 / 加仓建议。</p>
      </div>

      {loading ? <p className="status">加载持仓…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {message ? <p className="status ok">{message}</p> : null}

      <div className="diag-block">
        <div className="form-actions" style={{ marginTop: 0 }}>
          <h2 className="section-title">持仓诊断</h2>
          <button
            type="button"
            className="btn"
            disabled={diagnosing || loading}
            onClick={runDiagnosis}
          >
            {diagnosing ? '诊断中…' : '重新诊断'}
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
        <div className="table-wrap">
          <table className="data-table editable">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>数量</th>
                <th>成本</th>
                <th>备注</th>
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
                      value={row.name || ''}
                      onChange={(e) => update(i, { name: e.target.value })}
                      placeholder="可选"
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      type="number"
                      min={0}
                      value={row.qty}
                      onChange={(e) => update(i, { qty: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      type="number"
                      min={0}
                      step="0.001"
                      value={row.cost}
                      onChange={(e) => update(i, { cost: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      className="input"
                      value={row.note || ''}
                      onChange={(e) => update(i, { note: e.target.value })}
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
