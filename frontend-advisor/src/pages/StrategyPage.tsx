import { useEffect, useState } from 'react'
import {
  fetchStrategy,
  resetStrategy,
  saveStrategy,
  type UserStrategy,
} from '../api'

const LAYER_LABELS: Record<string, string> = {
  tech: '量价/动量',
  flow: '个股资金',
  sector: '板块强弱',
  value: '估值分位',
}

const WEIGHT_LABELS: Record<string, string> = {
  mom_1: '近1日动量',
  mom_5: '近5日动量',
  mom_10: '近10日动量',
  mom_20: '近20日动量',
  rs_300: '相对沪深300',
  ma20_bias: '相对20日均线',
  vol_z: '成交额 z-score',
  vol_ratio: '量比',
  low_vol: '低波动偏好',
}

const SOURCE_LABEL: Record<string, string> = {
  default: '系统默认',
  manual: '手动调整',
  agent: 'Agent 调优',
}

export default function StrategyPage() {
  const [data, setData] = useState<UserStrategy | null>(null)
  const [buyTh, setBuyTh] = useState(0.55)
  const [addTh, setAddTh] = useState(0.65)
  const [sellTh, setSellTh] = useState(0.35)
  const [layerWeights, setLayerWeights] = useState<Record<string, number>>({})
  const [marketBase, setMarketBase] = useState(0.85)
  const [marketScale, setMarketScale] = useState(0.3)
  const [weights, setWeights] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  function applyDoc(doc: UserStrategy) {
    setData(doc)
    setBuyTh(Number(doc.config.buy_threshold ?? 0.55))
    setAddTh(Number(doc.config.add_threshold ?? 0.65))
    setSellTh(Number(doc.config.sell_threshold ?? 0.35))
    setLayerWeights({ ...(doc.config.layer_weights || {}) })
    const ms = doc.config.market_scale || {}
    setMarketBase(Number(ms.base ?? 0.85))
    setMarketScale(Number(ms.scale ?? 0.3))
    setWeights({ ...(doc.config.weights || {}) })
  }

  async function load() {
    setLoading(true)
    setError(null)
    try {
      applyDoc(await fetchStrategy())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function handleSave() {
    setSaving(true)
    setError(null)
    setMsg(null)
    try {
      const doc = await saveStrategy({
        config_patch: {
          buy_threshold: buyTh,
          add_threshold: addTh,
          sell_threshold: sellTh,
          layer_weights: layerWeights,
          market_scale: { base: marketBase, scale: marketScale },
          weights,
        },
        source: 'manual',
        notes: '手动保存阈值与分层权重',
      })
      applyDoc(doc)
      setMsg('已保存。请到「今日关注」点「刷新候选池」按新策略重算。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  async function handleReset() {
    const ok = window.confirm('重置为系统默认策略？当前手动修改将丢失。')
    if (!ok) return
    setSaving(true)
    setError(null)
    setMsg(null)
    try {
      applyDoc(await resetStrategy())
      setMsg('已重置为系统默认。请刷新候选池使今日关注生效。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const layerKeys = Object.keys(LAYER_LABELS)
  const weightKeys = Object.keys(WEIGHT_LABELS)

  return (
    <section className="page">
      <div className="page-hero">
        <h1>我的策略</h1>
        <p>
          每人独立配置；默认与系统一致。修改后需在今日关注「刷新候选池」才会按新策略生成推荐。
          综合分 = 分层权重合成 × 市场缩放。
        </p>
      </div>

      {loading ? <p className="status">加载策略…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {msg ? <p className="status ok">{msg}</p> : null}

      {data && !loading ? (
        <>
          <p className="meta-line">
            来源 {SOURCE_LABEL[data.source] || data.source} · 版本 v{data.version}
            {data.updated_at ? ` · 更新于 ${data.updated_at}` : ''}
            {data.notes ? ` · ${data.notes}` : ''}
          </p>

          <h2 className="section-title">动作阈值</h2>
          <div className="strategy-grid">
            <label className="strategy-field">
              <span>买入阈值</span>
              <input
                className="input mono"
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={buyTh}
                onChange={(e) => setBuyTh(Number(e.target.value))}
              />
            </label>
            <label className="strategy-field">
              <span>加仓阈值</span>
              <input
                className="input mono"
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={addTh}
                onChange={(e) => setAddTh(Number(e.target.value))}
              />
            </label>
            <label className="strategy-field">
              <span>卖出阈值</span>
              <input
                className="input mono"
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={sellTh}
                onChange={(e) => setSellTh(Number(e.target.value))}
              />
            </label>
          </div>

          <h2 className="section-title">分层权重</h2>
          <p className="meta-line">
            stock = tech×w + flow×w + sector×w + value×w（保存后按和归一化）
          </p>
          <div className="strategy-grid">
            {layerKeys.map((key) => (
              <label key={key} className="strategy-field">
                <span>{LAYER_LABELS[key] || key}</span>
                <input
                  className="input mono"
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={layerWeights[key] ?? 0}
                  onChange={(e) =>
                    setLayerWeights((prev) => ({
                      ...prev,
                      [key]: Number(e.target.value),
                    }))
                  }
                />
              </label>
            ))}
          </div>

          <h2 className="section-title">市场缩放</h2>
          <p className="meta-line">
            final = stock × (base + scale × market_score)；默认 0.85 + 0.30×market
          </p>
          <div className="strategy-grid">
            <label className="strategy-field">
              <span>base</span>
              <input
                className="input mono"
                type="number"
                min={0}
                max={2}
                step={0.01}
                value={marketBase}
                onChange={(e) => setMarketBase(Number(e.target.value))}
              />
            </label>
            <label className="strategy-field">
              <span>scale</span>
              <input
                className="input mono"
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={marketScale}
                onChange={(e) => setMarketScale(Number(e.target.value))}
              />
            </label>
          </div>

          <h2 className="section-title">Tech 子因子权重</h2>
          <div className="strategy-grid">
            {weightKeys.map((key) => (
              <label key={key} className="strategy-field">
                <span>{WEIGHT_LABELS[key] || key}</span>
                <input
                  className="input mono"
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={weights[key] ?? 0}
                  onChange={(e) =>
                    setWeights((prev) => ({
                      ...prev,
                      [key]: Number(e.target.value),
                    }))
                  }
                />
              </label>
            ))}
          </div>

          <div className="form-actions">
            <button
              type="button"
              className="btn"
              disabled={saving}
              onClick={handleSave}
            >
              {saving ? '保存中…' : '保存策略'}
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={saving}
              onClick={handleReset}
            >
              重置为默认
            </button>
          </div>
        </>
      ) : null}
    </section>
  )
}
