import { useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  addWatchlist,
  fetchAdvice,
  fetchWatchlistStatus,
  removeWatchlist,
  type AdviceItem,
} from '../api'
import { AdviceCard } from '../components/AdviceCard'

export default function AdvicePage() {
  const [params, setParams] = useSearchParams()
  const initial = params.get('symbol') || ''
  const [symbol, setSymbol] = useState(initial)
  const [item, setItem] = useState<AdviceItem | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [starred, setStarred] = useState(false)
  const [starBusy, setStarBusy] = useState(false)

  function run(sym: string) {
    const s = sym.trim()
    if (!s) return
    setLoading(true)
    setError(null)
    setParams({ symbol: s })
    fetchAdvice(s)
      .then((res) => {
        setItem(res)
        return fetchWatchlistStatus([res.symbol]).then((st) => {
          setStarred(Boolean(st.starred?.[res.symbol]))
        })
      })
      .catch((err: Error) => {
        setItem(null)
        setStarred(false)
        setError(err.message)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (initial) run(initial)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    run(symbol)
  }

  async function onToggleStar(next: boolean) {
    if (!item) return
    setStarBusy(true)
    setStarred(next)
    try {
      if (next) await addWatchlist(item.symbol, item.name || undefined)
      else await removeWatchlist(item.symbol)
    } catch (err) {
      setStarred(!next)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setStarBusy(false)
    }
  }

  return (
    <section className="page">
      <div className="page-hero">
        <p>输入股票 / ETF 代码。有持仓时给出卖 / 持有 / 加仓；无持仓时判断是否值得短买。</p>
      </div>

      <form className="search-row" onSubmit={onSubmit}>
        <input
          className="input mono"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="例如 510300"
          maxLength={12}
        />
        <button className="btn" type="submit" disabled={loading}>
          {loading ? '分析中…' : '诊断'}
        </button>
      </form>

      {error ? <p className="status error">{error}</p> : null}
      {item && !item.error ? (
        <AdviceCard
          item={item}
          starred={starred}
          starBusy={starBusy}
          onToggleStar={onToggleStar}
        />
      ) : null}
      {item?.error ? <p className="status error">{item.error}</p> : null}
    </section>
  )
}
