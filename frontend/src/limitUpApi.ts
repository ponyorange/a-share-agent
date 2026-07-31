export type LimitUpStatus = 'sealed' | 'broken'

export type LimitUpTodayItem = {
  symbol: string
  name: string
  day_chg_pct: number | null
  board_count: number
  status: LimitUpStatus
  limit_up_price: number | null
}

export type LimitUpLadderItem = {
  symbol: string
  name: string
  day_chg_pct: number | null
}

export type LimitUpLadderTier = {
  board_count: number
  items: LimitUpLadderItem[]
}

export type LimitUpResponse = {
  source: string
  as_of: string
  date?: string
  session: {
    is_trading: boolean
    is_trading_day: boolean
  }
  today: LimitUpTodayItem[]
  ladder: LimitUpLadderTier[]
}

export async function fetchLimitUp(source = 'akshare'): Promise<LimitUpResponse> {
  const res = await fetch(`/api/${encodeURIComponent(source)}/limit-up`)
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

/** day_chg_pct is decimal ratio (0.10 = 10%). */
export function formatDayChgPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  const pct = value * 100
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

export function trendClass(value: number | null | undefined): string {
  if (value == null || value === 0) return ''
  return value > 0 ? 'up' : 'down'
}

export function shouldShowTodayTable(session: {
  is_trading?: boolean
} | null | undefined): boolean {
  return Boolean(session?.is_trading)
}

export function statusLabel(status: LimitUpStatus): string {
  return status === 'sealed' ? '当前涨停' : '曾涨停'
}
