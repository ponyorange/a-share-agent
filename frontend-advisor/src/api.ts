import { authFetch, getToken } from './auth'

export type FactorContribution = {
  name: string
  raw: number | null
  normalized: number
  weight: number
  contribution: number
}

export type AdviceItem = {
  symbol: string
  name: string
  as_of?: string
  close?: number | null
  prev_close?: number | null
  day_chg_pct?: number | null
  score: number
  action: 'buy' | 'watch' | 'hold' | 'add' | 'sell'
  action_label: string
  has_position: boolean
  position?: {
    symbol: string
    name?: string
    qty: number
    cost: number
    note?: string | null
  } | null
  factors: FactorContribution[]
  hit_rate?: number | null
  pnl?: number | null
  pnl_pct?: number | null
  return_pct?: number | null
  base_close?: number | null
  vs_close?: number | null
  vs_date?: string
  score_source?: string
  rationale: string
  agent_note?: string
  news_headlines?: string[]
  disclaimer?: string
  error?: string
}

export type BoardRecBlock = {
  id: string
  label: string
  scanned: number
  count: number
  pool_size?: number
  precise_size?: number
  items: AdviceItem[]
}

export type RecommendationsResponse = {
  as_of: string | null
  trade_date?: string
  count: number
  buy_threshold: number
  strategy_hit_rate: number | null
  items: AdviceItem[]
  scanned: number
  pool_total?: number
  mode?: string
  board?: string
  boards?: Record<string, BoardRecBlock>
  universe_source?: string
  disclaimer?: string
  snapshot?: {
    saved?: boolean
    trade_date?: string
    reason?: string
    from_cache?: boolean
  }
  errors?: { symbol: string; error?: string; board?: string }[]
}

export type Position = {
  symbol: string
  name?: string
  qty: number
  cost: number
  note?: string | null
}

export type PortfolioResponse = {
  positions: Position[]
}

export type PortfolioMarkItem = {
  symbol: string
  name?: string | null
  qty: number
  cost: number
  price: number | null
  pre_close: number | null
  day_chg_pct: number | null
  day_pnl: number | null
  market_value: number | null
  position_pnl: number | null
  position_pnl_pct: number | null
  weight: number | null
  error?: string | null
}

export type PortfolioMarksResponse = {
  session: {
    timezone?: string
    now?: string
    is_weekday?: boolean
    is_trading_day?: boolean
    is_trading: boolean
    refresh_recommended?: boolean
  }
  updated_at?: string | null
  count: number
  total_market_value: number
  total_cost: number
  total_day_pnl: number
  total_position_pnl: number
  total_return_pct: number | null
  items: PortfolioMarkItem[]
}

export type PortfolioAdviceResponse = {
  as_of: string | null
  count: number
  items: AdviceItem[]
  errors?: { symbol?: string; error?: string }[]
  summary: {
    add: number
    hold: number
    sell: number
    error: number
  }
  disclaimer?: string
}

export type BacktestSummary = {
  as_of: string
  threshold: number
  lookback_bars: number
  symbols_tested: number
  n_signals: number
  hit_rate: number | null
  avg_next_ret: number | null
  max_drawdown_approx: number | null
  akquant_avg_return_pct: number | null
  akquant_avg_max_drawdown_pct: number | null
  akquant_avg_sharpe: number | null
  engine: string
  akquant: {
    akquant_installed: boolean
    symbols?: Record<string, unknown>[]
  }
  per_symbol: {
    symbol: string
    name: string
    n_signals: number
    hit_rate: number | null
    avg_next_ret: number | null
  }[]
  disclaimer?: string
  from_cache?: boolean
}

export type PaperAccount = {
  cash: number
  initial_cash: number
  market_value: number
  equity: number
  mark_to_market?: boolean
  positions: {
    symbol: string
    name?: string
    qty: number
    cost: number
    last?: number
    market_value?: number
    pnl?: number
    pnl_pct?: number | null
    marked?: boolean
    marked_at?: string
  }[]
}

export function fetchRecommendations(
  top = 15,
  board: 'etf' | 'hs' | 'star' | 'all' = 'all',
  refreshUniverse = false,
): Promise<RecommendationsResponse> {
  const q = new URLSearchParams({
    top: String(top),
    board,
    refresh_universe: refreshUniverse ? 'true' : 'false',
    persist: 'true',
  })
  return authFetch(`/api/advisor/recommendations?${q}`)
}

/** SSE：强制刷新候选池，推送 universe / 精算进度。 */
export async function streamRecommendationsRefresh(
  top = 10,
  board: 'etf' | 'hs' | 'star' | 'all' = 'all',
  handlers: {
    onMeta?: (meta: {
      trade_date?: string | null
      top?: number
      board?: string
      phase?: string
      job_id?: string
      status?: string
    }) => void
    onProgress?: (row: {
      done: number
      total: number
      phase?: string
      step?: string
      message?: string
      symbol?: string
      name?: string
      board?: string
      precise_total?: number
      job_id?: string
      status?: string
    }) => void
    onDone?: (data: {
      job_id?: string
      status?: string
      trade_date?: string
      progress?: Record<string, unknown>
    }) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const q = new URLSearchParams({
    top: String(top),
    board,
    persist: 'true',
  })
  const token = getToken()
  const res = await fetch(
    `/api/advisor/recommendations/refresh/stream?${q}`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal,
    },
  )
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      let eventName = 'message'
      let dataLine = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(
          data as {
            trade_date?: string | null
            top?: number
            board?: string
            phase?: string
            job_id?: string
            status?: string
          },
        )
      } else if (eventName === 'progress') {
        handlers.onProgress?.(
          data as {
            done: number
            total: number
            phase?: string
            step?: string
            message?: string
            symbol?: string
            name?: string
            board?: string
            precise_total?: number
            job_id?: string
            status?: string
          },
        )
      } else if (eventName === 'done') {
        handlers.onDone?.(
          data as {
            job_id?: string
            status?: string
            trade_date?: string
            progress?: Record<string, unknown>
          },
        )
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || '刷新候选池失败'))
      }
    }
  }
}

export type RecRefreshJob = {
  job_id: string
  user_id?: string
  trade_date?: string
  status: string
  top?: number
  board?: string
  progress?: {
    phase?: string
    done?: number
    total?: number
    message?: string
    symbol?: string
    name?: string
    step?: string
    board?: string
  }
  error?: string | null
  created_at?: string | null
  updated_at?: string | null
  finished_at?: string | null
}

export function startRecommendationsRefresh(
  top = 10,
  board: 'etf' | 'hs' | 'star' | 'all' = 'all',
): Promise<{ job: RecRefreshJob }> {
  const q = new URLSearchParams({
    top: String(top),
    board,
    persist: 'true',
  })
  return authFetch(`/api/advisor/recommendations/refresh?${q}`, {
    method: 'POST',
  })
}

export function fetchActiveRecommendationsRefresh(): Promise<{
  job: RecRefreshJob | null
}> {
  return authFetch('/api/advisor/recommendations/refresh/active')
}

/** 订阅已有后台刷新任务进度。 */
export async function streamRecommendationsRefreshJob(
  jobId: string,
  handlers: {
    onMeta?: (meta: {
      job_id?: string
      trade_date?: string | null
      status?: string
      phase?: string
    }) => void
    onProgress?: (row: {
      done?: number
      total?: number
      phase?: string
      message?: string
      symbol?: string
      name?: string
      job_id?: string
      status?: string
    }) => void
    onDone?: (data: {
      job_id?: string
      status?: string
      trade_date?: string
      progress?: Record<string, unknown>
    }) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken()
  const res = await fetch(
    `/api/advisor/recommendations/refresh/${encodeURIComponent(jobId)}/stream`,
    {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal,
    },
  )
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      let eventName = 'message'
      let dataLine = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(
          data as {
            job_id?: string
            trade_date?: string | null
            status?: string
            phase?: string
          },
        )
      } else if (eventName === 'progress') {
        handlers.onProgress?.(
          data as {
            done?: number
            total?: number
            phase?: string
            message?: string
            symbol?: string
            name?: string
            job_id?: string
            status?: string
          },
        )
      } else if (eventName === 'done') {
        handlers.onDone?.(
          data as {
            job_id?: string
            status?: string
            trade_date?: string
            progress?: Record<string, unknown>
          },
        )
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || '刷新候选池失败'))
      }
    }
  }
}

export type RecQuoteItem = {
  symbol: string
  name?: string
  close?: number | null
  prev_close?: number | null
  day_chg_pct?: number | null
  as_of?: string
  board?: string
  done: number
  total: number
  error?: string
}

/** SSE：逐只加载收盘价与当日涨幅 */
export async function streamRecQuotes(
  tradeDate: string | undefined,
  board: string,
  handlers: {
    onMeta?: (meta: {
      trade_date: string
      total: number
      is_trading?: boolean
      live?: boolean
    }) => void
    onQuote?: (item: RecQuoteItem) => void
    onDone?: (done: { trade_date: string; total: number }) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const q = new URLSearchParams({ board })
  if (tradeDate) q.set('trade_date', tradeDate)
  const token = getToken()
  const res = await fetch(`/api/advisor/recommendations/quotes/stream?${q}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      let eventName = 'message'
      let dataLine = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(
          data as {
            trade_date: string
            total: number
            is_trading?: boolean
            live?: boolean
          },
        )
      } else if (eventName === 'quote') {
        handlers.onQuote?.(data as RecQuoteItem)
      } else if (eventName === 'done') {
        handlers.onDone?.(data as { trade_date: string; total: number })
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || '加载行情失败'))
      }
    }
  }
}

export function fetchAdvice(symbol: string): Promise<AdviceItem> {
  const q = encodeURIComponent(symbol.trim())
  return authFetch(`/api/advisor/advice?symbol=${q}`)
}

export function fetchPortfolio(): Promise<PortfolioResponse> {
  return authFetch('/api/advisor/portfolio')
}

export function fetchPortfolioMarks(): Promise<PortfolioMarksResponse> {
  return authFetch('/api/advisor/portfolio/marks')
}

export type WatchlistItem = {
  symbol: string
  name?: string | null
  added_at?: string | null
}

export type WatchlistResponse = { items: WatchlistItem[] }

export type WatchlistMarkItem = WatchlistItem & {
  price: number | null
  pre_close: number | null
  day_chg_pct: number | null
  error?: string | null
}

export type WatchlistMarksResponse = {
  session: {
    timezone?: string
    now?: string
    is_weekday?: boolean
    is_trading_day?: boolean
    is_trading: boolean
    refresh_recommended?: boolean
  }
  updated_at?: string | null
  count: number
  items: WatchlistMarkItem[]
}

export type WatchlistStatusResponse = { starred: Record<string, boolean> }

export function fetchWatchlist(): Promise<WatchlistResponse> {
  return authFetch('/api/advisor/watchlist')
}

export function fetchWatchlistMarks(): Promise<WatchlistMarksResponse> {
  return authFetch('/api/advisor/watchlist/marks')
}

export function fetchWatchlistStatus(symbols: string[]): Promise<WatchlistStatusResponse> {
  const q = encodeURIComponent(symbols.join(','))
  return authFetch(`/api/advisor/watchlist/status?symbols=${q}`)
}

export function addWatchlist(symbol: string, name?: string): Promise<WatchlistResponse> {
  const qs = name ? `?name=${encodeURIComponent(name)}` : ''
  return authFetch(`/api/advisor/watchlist/${encodeURIComponent(symbol)}${qs}`, {
    method: 'POST',
  })
}

export function removeWatchlist(symbol: string): Promise<WatchlistResponse> {
  return authFetch(`/api/advisor/watchlist/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  })
}

export type MonitorRule = {
  id?: string
  type:
    | 'price_below'
    | 'price_above'
    | 'day_chg_below'
    | 'day_chg_above'
    | 'flow_spike_in'
    | 'flow_spike_out'
    | string
  value: number
  hint?: string | null
  mult?: number | null
  window_days?: number | null
}

export type MonitorJob = {
  id: string
  title: string
  status: 'running' | 'paused' | string
  scope: 'watchlist' | 'portfolio' | 'symbols' | string
  symbols?: string[]
  rules: MonitorRule[]
  note?: string | null
  notify_email?: string | null
  cooldown_sec?: number
  llm_enabled?: boolean
  llm_interval_sec?: number
  llm_anomaly_abs_chg?: number
  knowledge_ids?: string[]
  last_run_at?: string | null
  last_alert_at?: string | null
  last_llm_at?: string | null
  last_error?: string | null
  last_llm_error?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export type MonitorJobsResponse = {
  jobs: MonitorJob[]
  count: number
}

export function fetchMonitorJobs(): Promise<MonitorJobsResponse> {
  return authFetch('/api/advisor/monitor/jobs')
}

export function pauseMonitorJob(jobId: string): Promise<MonitorJob> {
  return authFetch(`/api/advisor/monitor/jobs/${encodeURIComponent(jobId)}/pause`, {
    method: 'POST',
  })
}

export function resumeMonitorJob(jobId: string): Promise<MonitorJob> {
  return authFetch(`/api/advisor/monitor/jobs/${encodeURIComponent(jobId)}/resume`, {
    method: 'POST',
  })
}

export function deleteMonitorJob(jobId: string): Promise<{ ok: boolean; id: string }> {
  return authFetch(`/api/advisor/monitor/jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  })
}

export function fetchPortfolioAdvice(): Promise<PortfolioAdviceResponse> {
  return authFetch('/api/advisor/portfolio/advice')
}

export function savePortfolio(positions: Position[]): Promise<PortfolioResponse> {
  return authFetch('/api/advisor/portfolio', {
    method: 'POST',
    body: JSON.stringify({ positions }),
  })
}

export function fetchBacktestSummary(force = false): Promise<BacktestSummary> {
  return authFetch(`/api/advisor/backtest/summary?force=${force ? 'true' : 'false'}`)
}

/** SSE：策略回测进度。meta → progress* → done。 */
export async function streamBacktestSummary(
  force: boolean,
  handlers: {
    onMeta?: (meta: {
      total: number
      cached?: boolean
      phase?: string
      symbols?: number
      akquant_symbols?: number
    }) => void
    onProgress?: (row: {
      done: number
      total: number
      phase?: string
      symbol?: string
      name?: string
      ok?: boolean
      message?: string
      step?: string
    }) => void
    onDone?: (data: BacktestSummary) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const q = new URLSearchParams({ force: force ? 'true' : 'false' })
  const token = getToken()
  const res = await fetch(`/api/advisor/backtest/summary/stream?${q}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      let eventName = 'message'
      let dataLine = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(
          data as {
            total: number
            cached?: boolean
            phase?: string
            symbols?: number
            akquant_symbols?: number
          },
        )
      } else if (eventName === 'progress') {
        handlers.onProgress?.(
          data as {
            done: number
            total: number
            phase?: string
            symbol?: string
            name?: string
            ok?: boolean
          },
        )
      } else if (eventName === 'done') {
        handlers.onDone?.(data as BacktestSummary)
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || '回测失败'))
      }
    }
  }
}

export function fetchRecDates(): Promise<{ dates: string[] }> {
  return authFetch('/api/advisor/recommendations/dates')
}

export function fetchRecHistory(tradeDate: string) {
  const q = new URLSearchParams({ trade_date: tradeDate })
  return authFetch<RecommendationsResponse & {
    trade_date: string
    returns_computed?: boolean
  }>(`/api/advisor/recommendations/history?${q}`)
}

export type HistoryReturnItem = {
  index: number
  symbol: string
  name?: string
  score?: number
  action?: string
  action_label?: string
  close?: number
  base_close?: number | null
  vs_close?: number | null
  vs_date?: string
  return_pct?: number | null
  error?: string | null
}

export type HistoryStreamAccuracy = {
  all_hit_rate: number | null
  buy_hit_rate: number | null
  all_n: number
  buy_n: number
  note?: string
}

/** SSE：逐只推送涨跌幅。用 fetch+流解析以携带 Authorization。 */
export async function streamRecHistoryReturns(
  tradeDate: string,
  vsDate: string | undefined,
  handlers: {
    onMeta?: (meta: { trade_date: string; vs_date: string; total: number }) => void
    onItem?: (item: HistoryReturnItem) => void
    onDone?: (done: {
      trade_date: string
      vs_date: string
      accuracy: HistoryStreamAccuracy
    }) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const q = new URLSearchParams({ trade_date: tradeDate })
  if (vsDate) q.set('vs_date', vsDate)
  const token = getToken()
  const res = await fetch(`/api/advisor/recommendations/history/stream?${q}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let eventName = 'message'

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      const lines = chunk.split('\n')
      let dataLine = ''
      eventName = 'message'
      for (const line of lines) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(data as { trade_date: string; vs_date: string; total: number })
      } else if (eventName === 'item') {
        handlers.onItem?.(data as HistoryReturnItem)
      } else if (eventName === 'done') {
        handlers.onDone?.(
          data as {
            trade_date: string
            vs_date: string
            accuracy: HistoryStreamAccuracy
          },
        )
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || '计算失败'))
      }
    }
  }
}

export function fetchRecAccuracy(limitDays = 30) {
  return authFetch<{
    overall_buy_hit_rate: number | null
    days: number
    rows: {
      trade_date: string
      vs_date?: string
      buy_hit_rate?: number | null
      buy_n?: number
      all_hit_rate?: number | null
      error?: string
    }[]
  }>(`/api/advisor/recommendations/accuracy?limit_days=${limitDays}`)
}

export function fetchPaper(): Promise<PaperAccount> {
  return authFetch('/api/advisor/paper')
}

/** SSE：逐只刷新持仓现价/市值并写回库 */
export async function streamPaperMarkToMarket(
  handlers: {
    onMeta?: (meta: { total: number }) => void
    onPosition?: (row: {
      done: number
      total: number
      symbol: string
      name?: string
      qty: number
      cost: number
      last: number
      market_value: number
      pnl: number
      pnl_pct: number | null
      error?: string | null
    }) => void
    onDone?: (done: { account: PaperAccount }) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const token = getToken()
  const res = await fetch('/api/advisor/paper/mark-to-market/stream', {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      let eventName = 'message'
      let dataLine = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(data as { total: number })
      } else if (eventName === 'position') {
        handlers.onPosition?.(
          data as {
            done: number
            total: number
            symbol: string
            name?: string
            qty: number
            cost: number
            last: number
            market_value: number
            pnl: number
            pnl_pct: number | null
            error?: string | null
          },
        )
      } else if (eventName === 'done') {
        handlers.onDone?.(data as { account: PaperAccount })
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || '刷新市值失败'))
      }
    }
  }
}

export function resetPaper(cash: number): Promise<PaperAccount> {
  return authFetch('/api/advisor/paper/reset', {
    method: 'POST',
    body: JSON.stringify({ cash }),
  })
}

export function paperOrder(body: {
  symbol: string
  side: 'buy' | 'sell'
  qty: number
  price?: number
  name?: string
}) {
  return authFetch<{ trade: Record<string, unknown>; account: PaperAccount }>(
    '/api/advisor/paper/order',
    { method: 'POST', body: JSON.stringify(body) },
  )
}

/** 删除持仓：当作从未买过，不计入收益。 */
export function deletePaperPosition(symbol: string): Promise<PaperAccount> {
  return authFetch(`/api/advisor/paper/positions/${encodeURIComponent(symbol)}`, {
    method: 'DELETE',
  })
}

/** 卖出持仓：qty 缺省为全部。 */
export function sellPaperPosition(symbol: string, qty?: number, price?: number) {
  const body: { qty?: number; price?: number } = {}
  if (qty != null) body.qty = qty
  if (price != null) body.price = price
  return authFetch<{ trade: Record<string, unknown>; account: PaperAccount }>(
    `/api/advisor/paper/positions/${encodeURIComponent(symbol)}/sell`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  )
}

/** 一键卖出全部持仓。 */
export function sellAllPaperPositions() {
  return authFetch<{
    sold: number
    failed: number
    trades: Record<string, unknown>[]
    errors: { symbol: string; detail: string }[]
    account: PaperAccount
  }>('/api/advisor/paper/sell-all', { method: 'POST', body: '{}' })
}

export function oneClickBuy(
  top = 10,
  board = 'all',
  mode: 'balanced' | 'full' = 'balanced',
  maxCount?: number | null,
) {
  const body: { top: number; board: string; mode: string; max_count?: number } = {
    top,
    board,
    mode,
  }
  if (maxCount != null && maxCount > 0) body.max_count = maxCount
  return authFetch<{
    trades: Record<string, unknown>[]
    account: PaperAccount
    rec_date?: string
    source: string
    mode?: string
    max_count?: number | null
  }>('/api/advisor/paper/one-click-buy', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** SSE 一键买入，带进度。mode=full 尽量用尽现金满仓。 */
export async function streamOneClickBuy(
  board = 'all',
  handlers: {
    onMeta?: (meta: {
      total: number
      cash: number
      rec_date?: string
      mode?: string
      max_count?: number | null
    }) => void
    onProgress?: (data: {
      done: number
      total: number
      symbol?: string
      phase?: string
      message?: string
    }) => void
    onTrade?: (data: {
      done: number
      total: number
      trade: Record<string, unknown>
      cash: number
      phase?: string
    }) => void
    onSkip?: (data: {
      done: number
      total: number
      symbol?: string
      reason?: string
      phase?: string
    }) => void
    onDone?: (data: {
      trades_count: number
      skipped: number
      rec_date?: string
      mode?: string
      cash_before?: number
      cash_left?: number
      spent?: number
      account: PaperAccount
    }) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
  mode: 'balanced' | 'full' = 'balanced',
  maxCount?: number | null,
): Promise<void> {
  const q = new URLSearchParams({ board, mode })
  if (maxCount != null && maxCount > 0) q.set('max_count', String(maxCount))
  const token = getToken()
  const res = await fetch(`/api/advisor/paper/one-click-buy/stream?${q}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      let eventName = 'message'
      let dataLine = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(
          data as {
            total: number
            cash: number
            rec_date?: string
            mode?: string
            max_count?: number | null
          },
        )
      } else if (eventName === 'progress') {
        handlers.onProgress?.(
          data as {
            done: number
            total: number
            symbol?: string
            phase?: string
            message?: string
          },
        )
      } else if (eventName === 'trade') {
        handlers.onTrade?.(
          data as {
            done: number
            total: number
            trade: Record<string, unknown>
            cash: number
            phase?: string
          },
        )
      } else if (eventName === 'skip') {
        handlers.onSkip?.(
          data as {
            done: number
            total: number
            symbol?: string
            reason?: string
            phase?: string
          },
        )
      } else if (eventName === 'done') {
        handlers.onDone?.(
          data as {
            trades_count: number
            skipped: number
            rec_date?: string
            mode?: string
            cash_before?: number
            cash_left?: number
            spent?: number
            account: PaperAccount
          },
        )
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || '一键买入失败'))
      }
    }
  }
}

export function fetchPaperPnl() {
  return authFetch<{
    initial_cash: number
    cash: number
    equity: number
    market_value: number
    total: { pnl: number; return_pct: number | null; equity_pnl?: number }
    historical: {
      total: {
        pnl: number
        realized: number
        unrealized: number
        return_pct: number | null
      }
      one_click: {
        pnl: number
        realized: number
        unrealized: number
        return_pct: number | null
      }
      manual: {
        pnl: number
        realized: number
        unrealized: number
        return_pct: number | null
      }
    }
    holding: {
      total: {
        pnl: number
        open_cost: number
        open_market_value: number
        return_pct: number | null
      }
      one_click: {
        pnl: number
        open_cost: number
        open_market_value: number
        return_pct: number | null
      }
      manual: {
        pnl: number
        open_cost: number
        open_market_value: number
        return_pct: number | null
      }
    }
    note?: string
  }>('/api/advisor/paper/pnl')
}

export function fetchOneClickPerf(opts?: { page?: number; pageSize?: number }) {
  const q = new URLSearchParams()
  q.set('page', String(opts?.page ?? 1))
  q.set('page_size', String(opts?.pageSize ?? 20))
  return authFetch<{
    trades_count: number
    open_total: number
    open_rows: Record<string, unknown>[]
    closed_rows: Record<string, unknown>[]
    account_equity: number
    page: number
    page_size: number
    pages: number
  }>(`/api/advisor/paper/one-click-perf?${q}`)
}

export function fetchPaperTrades(opts?: {
  page?: number
  pageSize?: number
  source?: string
}) {
  const q = new URLSearchParams()
  q.set('page', String(opts?.page ?? 1))
  q.set('page_size', String(opts?.pageSize ?? 20))
  if (opts?.source) q.set('source', opts.source)
  return authFetch<{
    trades: Record<string, unknown>[]
    total: number
    page: number
    page_size: number
    pages: number
  }>(`/api/advisor/paper/trades?${q}`)
}

export function formatPct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

export function formatScore(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(2)
}

export type LeaderboardBoardId = 'etf' | 'hs' | 'star'
export type LeaderboardListId = 'gainers' | 'losers' | 'inflow' | 'outflow'

export type LeaderboardItem = {
  symbol: string
  name: string
  board: LeaderboardBoardId
  price?: number | null
  pct_chg?: number | null
  amount?: number | null
  main_net_inflow?: number | null
  main_net_inflow_pct?: number | null
}

export type LeaderboardPayload = {
  trade_date: string
  as_of?: string
  source?: string
  top?: number
  boards: Record<
    LeaderboardListId,
    Record<LeaderboardBoardId, LeaderboardItem[]>
  > | null
  list_labels?: Record<string, string>
  board_labels?: Record<string, string>
  errors?: { list: string; board: string; detail: string }[]
  from_cache?: boolean
  message?: string
}

export function fetchLeaderboard(tradeDate?: string): Promise<LeaderboardPayload> {
  const q = tradeDate ? `?trade_date=${encodeURIComponent(tradeDate)}` : ''
  return authFetch(`/api/advisor/leaderboard${q}`)
}

/** SSE：龙虎榜拉取。meta → progress* → done。 */
export async function streamLeaderboard(
  force: boolean,
  handlers: {
    onMeta?: (meta: {
      total: number
      cached?: boolean
      trade_date?: string
      phase?: string
      top?: number
    }) => void
    onProgress?: (row: {
      done: number
      total: number
      list_id?: string
      list_label?: string
      board?: string
      board_label?: string
      label?: string
      ok?: boolean
      detail?: string
      count?: number
    }) => void
    onDone?: (data: LeaderboardPayload) => void
    onError?: (detail: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  const q = new URLSearchParams({ force: force ? 'true' : 'false' })
  const token = getToken()
  const res = await fetch(`/api/advisor/leaderboard/stream?${q}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (res.status === 401) {
    handlers.onError?.('请先登录')
    return
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop() || ''
    for (const chunk of parts) {
      let eventName = 'message'
      let dataLine = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
      }
      if (!dataLine) continue
      let data: Record<string, unknown>
      try {
        data = JSON.parse(dataLine) as Record<string, unknown>
      } catch {
        continue
      }
      if (eventName === 'meta') {
        handlers.onMeta?.(
          data as {
            total: number
            cached?: boolean
            trade_date?: string
            phase?: string
            top?: number
          },
        )
      } else if (eventName === 'progress') {
        handlers.onProgress?.(
          data as {
            done: number
            total: number
            list_id?: string
            list_label?: string
            board?: string
            board_label?: string
            label?: string
            ok?: boolean
            detail?: string
            count?: number
          },
        )
      } else if (eventName === 'done') {
        handlers.onDone?.(data as LeaderboardPayload)
      } else if (eventName === 'error') {
        handlers.onError?.(String(data.detail || '拉取失败'))
      }
    }
  }
}

export type UserStrategy = {
  user_id?: string
  source: string
  version: number
  notes?: string | null
  updated_at?: string
  config: {
    buy_threshold?: number
    add_threshold?: number
    sell_threshold?: number
    layer_weights?: Record<string, number>
    market_scale?: { base?: number; scale?: number }
    weights?: Record<string, number>
    high_vol_penalty?: number
    high_vol_ann_threshold?: number
    recommendations?: Record<string, unknown>
    backtest?: Record<string, unknown>
    disclaimer?: string
  }
  defaults?: {
    buy_threshold?: number
    add_threshold?: number
    sell_threshold?: number
    layer_weights?: Record<string, number>
    market_scale?: { base?: number; scale?: number }
    weights?: Record<string, number>
  }
}

export function fetchStrategy(): Promise<UserStrategy> {
  return authFetch('/api/advisor/strategy')
}

export function saveStrategy(body: {
  config_patch?: Record<string, unknown>
  config?: Record<string, unknown>
  notes?: string
  source?: string
}): Promise<UserStrategy> {
  return authFetch('/api/advisor/strategy', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function resetStrategy(): Promise<UserStrategy> {
  return authFetch('/api/advisor/strategy/reset', {
    method: 'POST',
    body: '{}',
  })
}

