export type MarketIndex = {
  symbol: string
  name: string
  price: number | null
  change: number | null
  change_pct: number | null
  open: number | null
  high: number | null
  low: number | null
  pre_close: number | null
  volume: number | null
  amount: number | null
  featured: boolean
}

export type BoardStock = {
  rank: number
  symbol: string
  name: string
  price: number | null
  change: number | null
  change_pct: number | null
  open: number | null
  high: number | null
  low: number | null
  pre_close: number | null
  volume: number | null
  amount: number | null
}

export type MarketBoards = {
  gainers: BoardStock[]
  losers: BoardStock[]
  amount: BoardStock[]
  source: string | null
  error: string | null
}

export type MarketResponse = {
  updated_at: string
  source: string
  summary: {
    amount_sh: number | null
    amount_sz: number | null
    amount_total: number | null
  }
  featured: MarketIndex[]
  indices: MarketIndex[]
  boards: MarketBoards
}

export async function fetchMarket(source = 'akshare'): Promise<MarketResponse> {
  const res = await fetch(`/api/${encodeURIComponent(source)}/market`)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json()
}

export function formatIndexPrice(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  if (Math.abs(value) >= 1000) return value.toFixed(2)
  if (Math.abs(value) >= 100) return value.toFixed(2)
  return value.toFixed(2)
}

export function formatChange(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}`
}

export function formatPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

/** 成交额 → 亿 */
export function formatAmountYi(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  const yi = value / 1e8
  if (yi >= 10000) return `${(yi / 10000).toFixed(2)} 万亿`
  return `${yi.toFixed(2)} 亿`
}

export function trendClass(change: number | null | undefined): string {
  if (change == null || change === 0) return ''
  return change > 0 ? 'up' : 'down'
}
