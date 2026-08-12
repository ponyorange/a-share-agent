import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  fetchPaperTraderCockpit,
  pausePaperTrader,
  patchPaperTrader,
  resumePaperTrader,
  startPaperTrader,
  stopPaperTrader,
  type PaperTraderCockpit,
} from '../api'
import PaperTraderChart from '../components/PaperTraderChart'
import { explorerKlineUrl } from '../explorerLinks'

const POLL_TRADING_MS = 20_000
const POLL_IDLE_MS = 60_000

function statusLabel(status: string): string {
  switch (status) {
    case 'running':
      return '运行中'
    case 'paused':
      return '已暂停'
    case 'halted':
      return '已熔断'
    case 'stopped':
    default:
      return '未启动'
  }
}

export default function PaperTraderPage() {
  const [cockpit, setCockpit] = useState<PaperTraderCockpit | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [expandedDecision, setExpandedDecision] = useState<string | null>(null)
  const [riskOpen, setRiskOpen] = useState(false)
  const [mode, setMode] = useState('signal_first')
  const [intervalSec, setIntervalSec] = useState(600)
  const [riskDraft, setRiskDraft] = useState({
    max_single_position: 0.25,
    max_total_exposure: 0.9,
    max_positions: 10,
    max_trades_per_day: 30,
    max_daily_loss_pct: 0.05,
  })

  const refresh = useCallback(async () => {
    try {
      const data = await fetchPaperTraderCockpit()
      setCockpit(data)
      setError(null)
      const sess = data.session
      if (sess.mode) setMode(String(sess.mode))
      if (sess.interval_sec) setIntervalSec(Number(sess.interval_sec))
      const risk = sess.risk || {}
      setRiskDraft((prev) => ({
        max_single_position: Number(risk.max_single_position ?? prev.max_single_position),
        max_total_exposure: Number(risk.max_total_exposure ?? prev.max_total_exposure),
        max_positions: Number(risk.max_positions ?? prev.max_positions),
        max_trades_per_day: Number(risk.max_trades_per_day ?? prev.max_trades_per_day),
        max_daily_loss_pct: Number(risk.max_daily_loss_pct ?? prev.max_daily_loss_pct),
      }))
      setSelectedSymbol((cur) => {
        if (cur) return cur
        return (
          data.candidates[0]?.symbol ||
          (data.paper.positions[0]?.symbol
            ? String(data.paper.positions[0].symbol)
            : null)
        )
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const trading = Boolean(cockpit?.meta.is_trading)
    const ms = trading ? POLL_TRADING_MS : POLL_IDLE_MS
    const id = window.setInterval(() => {
      void refresh()
    }, ms)
    return () => window.clearInterval(id)
  }, [cockpit?.meta.is_trading, refresh])

  const status = String(cockpit?.session.status || 'stopped')

  async function runAction(fn: () => Promise<unknown>, okMsg: string) {
    if (busy) return
    setBusy(true)
    setMessage(null)
    setError(null)
    try {
      await fn()
      setMessage(okMsg)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function onResume() {
    if (status === 'halted') {
      const ok = window.confirm(
        '交易员处于熔断状态，确认恢复自动交易？（将 confirm_halt_resume）',
      )
      if (!ok) return
      await runAction(
        () => resumePaperTrader({ confirm_halt_resume: true }),
        '已从熔断恢复',
      )
      return
    }
    await runAction(() => resumePaperTrader(), '已继续')
  }

  async function onSaveConfig() {
    await runAction(
      () =>
        patchPaperTrader({
          mode,
          interval_sec: intervalSec,
          risk: riskDraft,
        }),
      '配置已保存，下轮生效',
    )
  }

  const decisions = cockpit?.decisions.items || []
  const candidates = cockpit?.candidates || []
  const positions = cockpit?.paper.positions || []
  const equity = cockpit?.paper.equity

  const controlBar = useMemo(
    () => (
      <div className="paper-trader-toolbar">
        <div className="paper-trader-toolbar-main">
          <strong>交易员驾驶舱</strong>
          <span className={`badge status-${status}`}>{statusLabel(status)}</span>
          {equity != null ? (
            <span className="metric-label">净值 {Number(equity).toFixed(2)}</span>
          ) : null}
          <Link to="/paper">模拟盘</Link>
        </div>
        <div className="paper-trader-actions">
          {status === 'stopped' || status === 'paused' ? (
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() =>
                void runAction(
                  () =>
                    status === 'paused'
                      ? resumePaperTrader()
                      : startPaperTrader({ mode, interval_sec: intervalSec }),
                  status === 'paused' ? '已继续' : '已启动',
                )
              }
            >
              {status === 'paused' ? '继续' : '启动'}
            </button>
          ) : null}
          {status === 'running' ? (
            <>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void runAction(() => pausePaperTrader(), '已暂停')}
              >
                暂停
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void runAction(() => stopPaperTrader(), '已停止')}
              >
                停止
              </button>
            </>
          ) : null}
          {status === 'paused' ? (
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void runAction(() => stopPaperTrader(), '已停止')}
            >
              停止
            </button>
          ) : null}
          {status === 'halted' ? (
            <button type="button" className="btn" disabled={busy} onClick={() => void onResume()}>
              恢复（熔断）
            </button>
          ) : null}
        </div>
      </div>
    ),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- handlers close over latest
    [busy, status, equity, mode, intervalSec],
  )

  return (
    <section className="page paper-trader-page">
      {controlBar}

      <details className="mobile-disclosure paper-trader-config-wrap">
        <summary>模式与风控</summary>
        <div className="paper-trader-config">
          <label>
            模式
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="signal_first">signal_first</option>
              <option value="llm_first">llm_first</option>
            </select>
          </label>
          <label>
            间隔（秒）
            <input
              type="number"
              min={300}
              max={900}
              value={intervalSec}
              onChange={(e) => setIntervalSec(Number(e.target.value) || 600)}
            />
          </label>
          <button type="button" className="btn linkish" onClick={() => setRiskOpen((v) => !v)}>
            {riskOpen ? '收起风控数值' : '展开风控数值'}
          </button>
          {riskOpen ? (
            <div className="paper-trader-risk-grid">
              {(
                [
                  ['max_single_position', '单票上限'],
                  ['max_total_exposure', '总仓上限'],
                  ['max_positions', '最多持仓数'],
                  ['max_trades_per_day', '日成交笔数'],
                  ['max_daily_loss_pct', '日亏损熔断'],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    type="number"
                    step="any"
                    value={Number(riskDraft[key])}
                    onChange={(e) =>
                      setRiskDraft((prev) => ({
                        ...prev,
                        [key]: Number(e.target.value),
                      }))
                    }
                  />
                </label>
              ))}
            </div>
          ) : null}
          <button type="button" className="btn" disabled={busy} onClick={() => void onSaveConfig()}>
            保存配置
          </button>
        </div>
      </details>

      {loading ? <p className="status">加载中…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {message ? <p className="status ok">{message}</p> : null}
      {cockpit?.session.halt_reason ? (
        <p className="status error">熔断原因：{String(cockpit.session.halt_reason)}</p>
      ) : null}
      {cockpit?.errors ? (
        <p className="status error">部分数据失败：{JSON.stringify(cockpit.errors)}</p>
      ) : null}

      <div className="paper-trader-grid">
        <div className="paper-trader-col">
          <h3>候选池</h3>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>方向</th>
                  <th>分数</th>
                  <th>图</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <tr
                    key={c.symbol}
                    className={selectedSymbol === c.symbol ? 'is-selected' : undefined}
                    onClick={() => setSelectedSymbol(c.symbol)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td>
                      {c.symbol} {c.name || ''}
                    </td>
                    <td className={`dir-${c.direction || 'neutral'}`}>{c.direction || '—'}</td>
                    <td>{c.rule_score != null ? Number(c.rule_score).toFixed(2) : '—'}</td>
                    <td>{c.graph_action || '—'}</td>
                  </tr>
                ))}
                {!candidates.length ? (
                  <tr>
                    <td colSpan={4}>
                      暂无候选（需「今日关注」有归档，或自选/模拟盘持仓）
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <h3>迷你持仓</h3>
          <ul className="paper-trader-positions">
            {positions.map((p) => (
              <li key={String(p.symbol)}>
                <button type="button" className="btn linkish" onClick={() => setSelectedSymbol(String(p.symbol))}>
                  {p.symbol} {p.name || ''} · qty {p.qty}
                </button>
              </li>
            ))}
            {!positions.length ? <li>无持仓</li> : null}
          </ul>
          {cockpit ? (
            <p className="muted">
              今日：轮次 {cockpit.session.stats_today?.rounds ?? 0} / 成交{' '}
              {cockpit.session.stats_today?.trades ?? 0} / 拦截{' '}
              {cockpit.session.stats_today?.blocked ?? 0}
            </p>
          ) : null}
        </div>

        <div className="paper-trader-col">
          <div className="paper-trader-chart-head">
            <h3>日 K {selectedSymbol ? `· ${selectedSymbol}` : ''}</h3>
            {selectedSymbol ? (
              <a href={explorerKlineUrl(selectedSymbol)} target="_blank" rel="noreferrer">
                详细 K 线
              </a>
            ) : null}
          </div>
          <PaperTraderChart symbol={selectedSymbol} />

          <h3>决策时间线</h3>
          <ul className="paper-trader-decisions">
            {decisions.map((d) => {
              const id = String(d.id || d.run_id || '')
              const open = expandedDecision === id
              return (
                <li key={id || JSON.stringify(d)}>
                  <button
                    type="button"
                    className="btn linkish"
                    onClick={() => setExpandedDecision(open ? null : id)}
                  >
                    {String(d.finished_at || d.started_at || '—')} · skip=
                    {String(d.skip_reason || '—')} · 成交{' '}
                    {Array.isArray(d.orders_placed) ? d.orders_placed.length : 0} · 拦截{' '}
                    {Array.isArray(d.risk_blocked) ? d.risk_blocked.length : 0}
                  </button>
                  {open ? (
                    <pre className="paper-trader-decision-detail">
                      {JSON.stringify(
                        {
                          llm_actions: d.llm_actions,
                          risk_blocked: d.risk_blocked,
                          orders_placed: d.orders_placed,
                          skip_reason: d.skip_reason,
                          error: d.error,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  ) : null}
                </li>
              )
            })}
            {!decisions.length ? <li>尚无决策轮次</li> : null}
          </ul>
        </div>
      </div>
    </section>
  )
}
