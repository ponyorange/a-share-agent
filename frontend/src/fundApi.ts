export type FundSearchItem = {
  symbol: string
  name: string
  type: string
  pinyin: string
}

export type FundSearchResponse = {
  source: string
  q: string
  items: FundSearchItem[]
}

export type FundNavPoint = {
  date: string
  nav: number
  change_pct: number | null
}

export type FundOverview = {
  full_name: string
  type: string
  establish_date: string
  scale: string
  manager: string
  company: string
  custodian: string
  benchmark: string
  tracking: string
  fees: {
    management: string
    custody: string
    sales: string
    subscribe: string
    redeem: string
  }
}

export type FundDetailResponse = {
  source: string
  symbol: string
  name: string
  overview: FundOverview | null
  nav: { latest: FundNavPoint; series: FundNavPoint[] } | null
  nav_error?: string
}

export function normalizeFundSymbol(raw: string): string {
  return (raw || '').replace(/\D/g, '').slice(0, 6)
}

export function formatNav(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toFixed(4)
}

export function formatNavPct(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

async function readError(res: Response, fallback: string): Promise<string> {
  let detail = fallback
  try {
    const body = await res.json()
    if (body?.detail) detail = String(body.detail)
  } catch {
    /* ignore */
  }
  return detail
}

export async function searchFunds(
  q: string,
  source = 'akshare',
  limit = 20,
): Promise<FundSearchResponse> {
  const qs = new URLSearchParams({ q, limit: String(limit) })
  const res = await fetch(
    `/api/${encodeURIComponent(source)}/fund/search?${qs}`,
  )
  if (!res.ok) {
    throw new Error(await readError(res, `搜索失败 HTTP ${res.status}`))
  }
  return res.json()
}

export async function fetchFundDetail(
  symbol: string,
  source = 'akshare',
): Promise<FundDetailResponse> {
  const res = await fetch(
    `/api/${encodeURIComponent(source)}/fund/${encodeURIComponent(symbol)}`,
  )
  if (!res.ok) {
    throw new Error(await readError(res, `详情失败 HTTP ${res.status}`))
  }
  return res.json()
}
