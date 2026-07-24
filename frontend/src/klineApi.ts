export type KlineRange = 'realtime' | '5d' | 'daily' | 'weekly' | 'monthly'

/** A-share ETF/fund codes: SH 5xxxxx, SZ 1xxxxx — quote to 3 decimals. */
export function isEtfSymbol(symbol: string): boolean {
  return /^[15]\d{5}$/.test(symbol)
}

export function priceDecimals(symbol: string): number {
  return isEtfSymbol(symbol) ? 3 : 2
}

export function formatPrice(value: number, symbol: string): string {
  return value.toFixed(priceDecimals(symbol))
}

export type KlineBar = {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
  avg_price?: number
}

export type KlineResponse = {
  symbol: string
  name: string
  range: KlineRange
  chart_type: 'candle' | 'line'
  pre_close: number | null
  source: string
  count: number
  last: KlineBar
  bars: KlineBar[]
}

export async function fetchKline(
  symbol: string,
  range: KlineRange,
  source = 'akshare',
): Promise<KlineResponse> {
  const qs = new URLSearchParams({ symbol, range })
  const res = await fetch(`/api/${encodeURIComponent(source)}/kline?${qs}`)
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

/** Simple moving average of close; first period-1 points are null. */
export function computeSma(
  bars: KlineBar[],
  period: number,
): Array<number | null> {
  const out: Array<number | null> = new Array(bars.length).fill(null)
  if (period <= 0 || bars.length < period) return out
  let sum = 0
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close
    if (i >= period) sum -= bars[i - period].close
    if (i >= period - 1) out[i] = sum / period
  }
  return out
}

export const AVG_PRICE_COLOR = '#f0c27a'

export function isValidAvgPrice(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
}

export function shouldShowAvgPrice(
  range: KlineRange,
  bars: KlineBar[],
): boolean {
  return range === 'realtime' && bars.some((bar) => isValidAvgPrice(bar.avg_price))
}

export const DAILY_MA_COLORS = {
  ma5: '#f0c27a',
  ma10: '#5b8def',
  ma20: '#c084fc',
} as const
