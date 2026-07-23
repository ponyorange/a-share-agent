export type QuoteLevel = {
  price: number | null
  volume: number | null
}

export type QuoteTick = {
  time: string
  price: number | null
  volume: number | null
  side: string
  side_code: string
}

export type QuoteSnapshot = {
  symbol: string
  name: string
  price: number | null
  change: number | null
  change_pct: number | null
  open: number | null
  high: number | null
  low: number | null
  pre_close: number | null
  avg_price: number | null
  volume: number | null
  amount: number | null
  turnover: number | null
  volume_ratio: number | null
  limit_up: number | null
  limit_down: number | null
  outer_vol: number | null
  inner_vol: number | null
  asks: QuoteLevel[]
  bids: QuoteLevel[]
  book_source?: string | null
  book_as_of?: string | null
}

export type QuoteSession = {
  timezone: string
  now: string
  is_weekday: boolean
  is_trading: boolean
  refresh_recommended: boolean
}

export type QuoteResponse = {
  symbol: string
  name: string
  updated_at: string
  source: string
  session: QuoteSession
  book_available: boolean
  book_live: boolean
  book_note: string | null
  snapshot: QuoteSnapshot
  ticks: QuoteTick[]
  errors: {
    snapshot: string | null
    ticks: string | null
  }
}

export async function fetchQuote(
  symbol: string,
  source = 'akshare',
  tickLimit = 40,
): Promise<QuoteResponse> {
  const qs = new URLSearchParams({
    symbol,
    tick_limit: String(tickLimit),
  })
  const res = await fetch(`/api/${encodeURIComponent(source)}/quote?${qs}`)
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
