import { useEffect, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { agentApplyStrategy, agentChat, fetchLlmSettings } from '../agentApi'
import { fetchStrategy, type UserStrategy } from '../api'

function tryExtractPatch(text: string): Record<string, unknown> | null {
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i)
  const raw = fence ? fence[1] : text
  const obj = raw.match(/\{[\s\S]*\}/)
  if (!obj) return null
  try {
    const parsed = JSON.parse(obj[0]) as Record<string, unknown>
    const patch: Record<string, unknown> = {}
    for (const k of [
      'buy_threshold',
      'add_threshold',
      'sell_threshold',
      'layer_weights',
      'market_scale',
      'weights',
      'high_vol_penalty',
      'high_vol_ann_threshold',
    ]) {
      if (parsed[k] != null) patch[k] = parsed[k]
    }
    // nested config_patch
    if (parsed.config_patch && typeof parsed.config_patch === 'object') {
      return parsed.config_patch as Record<string, unknown>
    }
    return Object.keys(patch).length ? patch : null
  } catch {
    return null
  }
}

export default function AgentStrategyPage() {
  const [ready, setReady] = useState<boolean | null>(null)
  const [strategy, setStrategy] = useState<UserStrategy | null>(null)
  const [instruction, setInstruction] = useState('')
  const [reply, setReply] = useState<string | null>(null)
  const [patch, setPatch] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([fetchLlmSettings(), fetchStrategy()])
      .then(([llm, st]) => {
        setReady(Boolean(llm.configured))
        setStrategy(st)
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err))
        setReady(false)
      })
  }, [])

  if (ready === null) {
    return (
      <section className="page">
        <p className="status">加载中…</p>
      </section>
    )
  }
  if (!ready) {
    return <Navigate to="/agent/settings" replace />
  }

  async function propose() {
    const text = instruction.trim()
    if (!text) return
    setLoading(true)
    setError(null)
    setMsg(null)
    setReply(null)
    setPatch(null)
    try {
      const res = await agentChat(
        `你是策略副驾。根据意图提出可落库的 config_patch。` +
          `意图：${text}。请先读取当前策略，再给出简要说明，并在回复中附带一段 JSON（可含 buy_threshold/add_threshold/sell_threshold/layer_weights/market_scale/weights）。不要直接 apply。`,
        [],
        undefined,
      )
      setReply(res.reply)
      setPatch(tryExtractPatch(res.reply))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function apply() {
    if (!patch) {
      setError('未解析到可用 config_patch，请调整意图后重试，或手动在回复 JSON 中包含字段')
      return
    }
    if (!window.confirm('确认将此补丁写入「我的策略」（source=agent）？')) return
    setLoading(true)
    setError(null)
    try {
      const doc = (await agentApplyStrategy(patch, `策略副驾: ${instruction.slice(0, 80)}`)) as UserStrategy & {
        hint?: string
      }
      setStrategy(doc)
      setMsg(doc.hint || '已写入策略。请到基础面板刷新候选池。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const cfg = strategy?.config

  return (
    <section className="page">
      <div className="page-hero">
        <p>
          用自然语言提出调参意图 → Agent 生成 config_patch → 你确认后写入（source=agent）。
          <Link className="text-link" to="/agent">
            {' '}
            返回助手
          </Link>
        </p>
      </div>

      {strategy ? (
        <p className="meta-line">
          当前 v{strategy.version} · {strategy.source} · 买入阈值{' '}
          {cfg?.buy_threshold ?? '—'} · 加仓 {cfg?.add_threshold ?? '—'} · 卖出{' '}
          {cfg?.sell_threshold ?? '—'}
          {cfg?.layer_weights
            ? ` · 分层 tech=${cfg.layer_weights.tech ?? '—'}`
            : ''}
        </p>
      ) : null}

      <label className="strategy-field">
        <span>调参意图</span>
        <textarea
          className="input"
          rows={3}
          placeholder="例如：更偏重近 5 日动量，买入阈值提高到 0.6"
          value={instruction}
          disabled={loading}
          onChange={(e) => setInstruction(e.target.value)}
        />
      </label>

      <div className="form-actions">
        <button type="button" className="btn" disabled={loading || !instruction.trim()} onClick={propose}>
          {loading ? '生成中…' : '生成补丁建议'}
        </button>
        <button type="button" className="btn" disabled={loading || !patch} onClick={apply}>
          确认写入策略
        </button>
      </div>

      {error ? <p className="status error">{error}</p> : null}
      {msg ? <p className="status ok">{msg}</p> : null}

      {patch ? (
        <div className="agent-patch">
          <h2 className="section-title">待确认 patch</h2>
          <pre className="agent-patch-json">{JSON.stringify(patch, null, 2)}</pre>
        </div>
      ) : null}

      {reply ? (
        <div className="agent-bubble assistant" style={{ marginTop: '1rem' }}>
          <div className="agent-bubble-role">助手说明</div>
          <div className="agent-bubble-body">{reply}</div>
        </div>
      ) : null}
    </section>
  )
}
