import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import RecommendationsPage from './RecommendationsPage'

const fetchActiveRecommendationsRefresh = vi.hoisted(() => vi.fn())
const fetchRecommendations = vi.hoisted(() => vi.fn())
const fetchWatchlistStatus = vi.hoisted(() => vi.fn())
const streamOneClickBuy = vi.hoisted(() => vi.fn())
const streamRecommendationsRefresh = vi.hoisted(() => vi.fn())
const streamRecommendationsRefreshJob = vi.hoisted(() => vi.fn())
const streamRecQuotes = vi.hoisted(() => vi.fn())

vi.mock('../api', () => ({
  fetchActiveRecommendationsRefresh,
  fetchRecommendations,
  fetchWatchlistStatus,
  addWatchlist: vi.fn(),
  removeWatchlist: vi.fn(),
  formatPct: (v: number | null | undefined, digits = 1) =>
    v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`,
  formatScore: (v: number | null | undefined) =>
    v == null || Number.isNaN(v) ? '—' : v.toFixed(2),
  streamOneClickBuy,
  streamRecommendationsRefresh,
  streamRecommendationsRefreshJob,
  streamRecQuotes,
}))

function setViewport(mobile: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: mobile,
      media: '(max-width: 768px)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

beforeEach(() => {
  localStorage.clear()
  setViewport(true)
  fetchActiveRecommendationsRefresh.mockReset()
  fetchRecommendations.mockReset()
  fetchWatchlistStatus.mockReset()
  streamOneClickBuy.mockReset()
  streamRecommendationsRefresh.mockReset()
  streamRecommendationsRefreshJob.mockReset()
  streamRecQuotes.mockReset()
  streamRecQuotes.mockImplementation(
    async (
      _tradeDate: string | undefined,
      _board: string,
      handlers: {
        onMeta?: (meta: {
          trade_date: string
          total: number
          is_trading?: boolean
        }) => void
        onQuote?: (q: {
          symbol: string
          close: number | null
          prev_close: number | null
          day_chg_pct: number | null
          as_of: string
          done: number
          total: number
        }) => void
        onDone?: () => void
      },
    ) => {
      handlers.onMeta?.({
        trade_date: '2026-07-24',
        total: 1,
        is_trading: false,
      })
      handlers.onQuote?.({
        symbol: '159518',
        close: 1.25,
        prev_close: 1.234,
        day_chg_pct: 0.013,
        as_of: '2026-07-24 15:00:00',
        done: 1,
        total: 1,
      })
      handlers.onDone?.()
    },
  )
  fetchActiveRecommendationsRefresh.mockResolvedValue({ job: null })
  fetchWatchlistStatus.mockResolvedValue({ starred: {} })
  fetchRecommendations.mockResolvedValue({
    as_of: '2026-07-24 15:00:00',
    trade_date: '2026-07-24',
    count: 1,
    buy_threshold: 0.7,
    strategy_hit_rate: 0.61,
    items: [],
    scanned: 9,
    mode: '归档优先',
    universe_source: 'akshare',
    regime: {
      gate_level: 'risk_off',
      position_cap: 0.15,
      pool_policy: 'block',
      data_quality: 'ok',
      override_applied: false,
    },
    gate_blocked_buys: true,
    snapshot: { from_cache: true, trade_date: '2026-07-24' },
    boards: {
      etf: {
        id: 'etf',
        label: 'ETF',
        scanned: 9,
        pool_size: 30,
        precise_size: 9,
        count: 1,
        items: [
          {
            symbol: '159518',
            name: '标普油气ETF嘉实',
            close: 1.234,
            day_chg_pct: 0.0123,
            score: 0.876,
            action: 'buy',
            action_label: '买入关注',
            has_position: false,
            factors: [],
            hit_rate: 0.654,
            rationale: '测试推荐理由',
          },
        ],
      },
      hs: { id: 'hs', label: '沪深股', scanned: 0, count: 0, items: [] },
      star: { id: 'star', label: '科创股', scanned: 0, count: 0, items: [] },
    },
  })
})

it('从查询参数透传市场状态 override，并展示闸门徽章与覆盖提示', async () => {
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={['/?regime_override=1']}>
      <RecommendationsPage />
    </MemoryRouter>,
  )

  await waitFor(() => {
    expect(fetchRecommendations).toHaveBeenCalledWith(10, 'all', false, true)
  })
  expect(screen.getByText('今日闸门：风险关闭')).toBeInTheDocument()
  expect(screen.queryByText('block')).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: '查看今日闸门' })).toHaveAttribute(
    'href',
    '/regime',
  )
  expect(screen.getAllByText(/已开启 override/).length).toBeGreaterThan(0)

  cleanup()
  fetchRecommendations.mockClear()
  fetchRecommendations.mockResolvedValueOnce({
    as_of: '2026-07-24 15:00:00',
    trade_date: '2026-07-24',
    count: 1,
    buy_threshold: 0.7,
    strategy_hit_rate: 0.61,
    items: [],
    scanned: 9,
    mode: '归档优先',
    gate_blocked_buys: true,
    regime: {
      gate_level: 'risk_off',
      position_cap: 0.15,
      pool_policy: 'block',
      data_quality: 'ok',
      override_applied: false,
    },
    boards: {
      etf: { id: 'etf', label: 'ETF', scanned: 1, count: 0, items: [] },
      hs: { id: 'hs', label: '沪深股', scanned: 0, count: 0, items: [] },
      star: { id: 'star', label: '科创股', scanned: 0, count: 0, items: [] },
    },
  })

  render(
    <MemoryRouter>
      <RecommendationsPage />
    </MemoryRouter>,
  )

  expect(await screen.findByText(/今日闸门为风险关闭/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '仍要查看推荐' }))
  await waitFor(() => {
    expect(fetchRecommendations).toHaveBeenLastCalledWith(10, 'all', false, true)
  })
})

it('移动端以推荐卡片展示今日关注并保留页面级买入操作', async () => {
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <RecommendationsPage />
    </MemoryRouter>,
  )

  const mobileMeta = await screen.findByLabelText('推荐日期与归档状态')
  expect(within(mobileMeta).getByText('推荐日 2026-07-24')).toBeInTheDocument()
  expect(within(mobileMeta).getByText('来自归档')).toBeInTheDocument()

  const heroDesc = screen.getByText('说明').closest('details')
  expect(heroDesc).not.toBeNull()
  expect(heroDesc).not.toHaveAttribute('open')
  expect(screen.queryByLabelText('推荐摘要')).not.toBeVisible()

  await user.click(within(heroDesc as HTMLElement).getByText('说明'))
  expect(heroDesc).toHaveAttribute('open')
  expect(
    within(heroDesc as HTMLElement).getByText(/大池粗筛 \+ Top 精算/),
  ).toBeInTheDocument()

  const summary = within(heroDesc as HTMLElement).getByLabelText('推荐摘要')
  expect(within(summary).getByText('大池')).toBeInTheDocument()
  expect(within(summary).getByText('30')).toBeInTheDocument()
  expect(within(summary).getByText('精算')).toBeInTheDocument()
  expect(within(summary).getByText('9')).toBeInTheDocument()
  expect(within(summary).getByText('推荐')).toBeInTheDocument()
  expect(within(summary).getByText('1')).toBeInTheDocument()

  const disclosure = screen.getByText('筛选与数据源').closest('details')
  expect(disclosure).not.toBeNull()
  expect(within(disclosure as HTMLElement).getByText(/有效交易日 2026-07-24/)).toBeInTheDocument()
  expect(within(disclosure as HTMLElement).getByText(/来自归档/)).toBeInTheDocument()
  expect(within(disclosure as HTMLElement).getByText(/源 akshare/)).toBeInTheDocument()

  expect(screen.getByText('标普油气ETF嘉实')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '诊断' })).toHaveAttribute(
    'href',
    '/advice?symbol=159518',
  )
  expect(screen.getByRole('button', { name: '一键买入 ETF/沪深' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '买入' })).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '表格视图' }))
  await waitFor(() => expect(screen.getByRole('columnheader', { name: '代码' })).toBeInTheDocument())
  expect(screen.getByRole('columnheader', { name: '名称' })).toBeInTheDocument()
})
