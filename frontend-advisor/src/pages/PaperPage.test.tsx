import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import PaperPage from './PaperPage'
import type { PaperAccount } from '../api'

const deletePaperPosition = vi.hoisted(() => vi.fn())
const fetchOneClickPerf = vi.hoisted(() => vi.fn())
const fetchPaper = vi.hoisted(() => vi.fn())
const fetchPaperPnl = vi.hoisted(() => vi.fn())
const fetchPaperTrades = vi.hoisted(() => vi.fn())
const paperOrder = vi.hoisted(() => vi.fn())
const resetPaper = vi.hoisted(() => vi.fn())
const sellAllPaperPositions = vi.hoisted(() => vi.fn())
const sellPaperPosition = vi.hoisted(() => vi.fn())
const streamPaperMarkToMarket = vi.hoisted(() => vi.fn())

vi.mock('../api', () => ({
  deletePaperPosition,
  fetchOneClickPerf,
  fetchPaper,
  fetchPaperPnl,
  fetchPaperTrades,
  formatPct: (v: number | null | undefined, digits = 1) =>
    v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`,
  paperOrder,
  resetPaper,
  sellAllPaperPositions,
  sellPaperPosition,
  streamPaperMarkToMarket,
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

const account: PaperAccount = {
  cash: 50000,
  initial_cash: 100000,
  market_value: 50500,
  equity: 100500,
  positions: [
    {
      symbol: '159518',
      name: '标普油气ETF嘉实',
      qty: 200,
      cost: 1.23,
      last: 1.35,
      market_value: 270,
      pnl: 24,
      pnl_pct: 0.09756,
    },
  ],
}

beforeEach(() => {
  localStorage.clear()
  setViewport(true)
  vi.restoreAllMocks()
  fetchPaper.mockResolvedValue(account)
  fetchPaperPnl.mockResolvedValue({
    total: { pnl: 500, return_pct: 0.005 },
    historical: {
      total: { pnl: 500, realized: 476, unrealized: 24, return_pct: 0.005 },
      one_click: { pnl: 300, realized: 280, unrealized: 20, return_pct: 0.003 },
      manual: { pnl: 200, realized: 196, unrealized: 4, return_pct: 0.002 },
    },
    holding: {
      total: { pnl: 24, open_cost: 246, open_market_value: 270, return_pct: 0.09756 },
      one_click: { pnl: 20, open_cost: 200, open_market_value: 220, return_pct: 0.1 },
      manual: { pnl: 4, open_cost: 46, open_market_value: 50, return_pct: 0.08696 },
    },
  })
  fetchOneClickPerf.mockResolvedValue({
    open_rows: [
      {
        symbol: '600519',
        name: '贵州茅台',
        rec_date: '2026-07-24',
        buy_price: 1510.5,
        last: 1525.2,
        unrealized_pnl_pct: 0.00973,
      },
    ],
    page: 1,
    pages: 1,
    open_total: 1,
    trades_count: 1,
    account_equity: 100500,
  })
  fetchPaperTrades.mockResolvedValue({
    trades: [
      {
        created_at: '2026-07-24T10:12:13',
        side: 'sell',
        symbol: '510300',
        name: '沪深300ETF',
        qty: 100,
        price: 4.12,
        source: 'manual',
      },
    ],
    page: 1,
    pages: 1,
    total: 1,
  })
  deletePaperPosition.mockResolvedValue(account)
})

it('移动端展示模拟盘摘要、折叠操作和默认卡片视图，并保留删除确认', async () => {
  const user = userEvent.setup()
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

  render(<PaperPage />)

  const summary = await screen.findByLabelText('模拟盘账户摘要')
  expect(within(summary).getByText('总权益')).toBeInTheDocument()
  expect(within(summary).getByText('100500.00')).toBeInTheDocument()
  expect(within(summary).getByText('总收益')).toBeInTheDocument()
  expect(within(summary).getByText('+500.00')).toBeInTheDocument()
  expect(within(summary).getByText('现金')).toBeInTheDocument()
  expect(within(summary).getByText('50000.00')).toBeInTheDocument()
  expect(within(summary).getByText('市值')).toBeInTheDocument()
  expect(within(summary).getByText('50500.00')).toBeInTheDocument()

  expect(screen.queryByText('持仓收益')).not.toBeInTheDocument()
  const holding = screen.getByText('总持仓收益').closest('section')
  expect(holding).not.toBeNull()
  expect(within(holding as HTMLElement).getByText('总持仓收益')).toBeVisible()
  expect(
    within(holding as HTMLElement).getByText('总持仓收益').closest('.stat-row'),
  ).toHaveClass('paper-pnl-stat-row')

  const historical = screen.getByText('历史收益').closest('details')
  expect(historical).not.toBeNull()
  expect(
    (holding as HTMLElement).compareDocumentPosition(historical as HTMLElement) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
  expect(historical).not.toHaveAttribute('open')
  await user.click(within(historical as HTMLElement).getByText('历史收益'))
  expect(historical).toHaveAttribute('open')
  expect(within(historical as HTMLElement).getByText('总一键买入收益')).toBeInTheDocument()
  expect(
    within(historical as HTMLElement).getByText('总收益').closest('.stat-row'),
  ).toHaveClass('paper-pnl-stat-row')

  const moreActions = screen.getByText('更多操作').closest('details')
  expect(moreActions).not.toBeNull()
  expect(moreActions).not.toHaveAttribute('open')

  const refreshButton = screen.getByRole('button', { name: '刷新市值' })
  const primaryActions = refreshButton.closest('.paper-primary-actions')
  expect(primaryActions).not.toBeNull()
  const sellAllButton = screen.getByRole('button', { name: '一键卖出' })
  expect(sellAllButton.parentElement).toBe(primaryActions)
  expect(
    within(moreActions as HTMLElement).queryByRole('button', { name: '一键卖出' }),
  ).not.toBeInTheDocument()

  await user.click(within(moreActions as HTMLElement).getByText('更多操作'))
  expect(moreActions).toHaveAttribute('open')
  expect(
    within(moreActions as HTMLElement).getByRole('button', { name: '重置资金（清空持仓）' }),
  ).toBeInTheDocument()

  const orderForm = screen.getByRole('form', { name: '下单' })
  expect(moreActions).toContainElement(orderForm)
  expect(within(orderForm).getByRole('combobox')).toHaveValue('buy')
  expect(within(orderForm).getByPlaceholderText('代码')).toBeInTheDocument()
  expect(within(orderForm).getByRole('spinbutton')).toHaveValue(100)

  const positionsView = await screen.findByRole('group', { name: '持仓视图' })
  expect(screen.getByRole('article', { name: '标普油气ETF嘉实 159518' })).toBeInTheDocument()
  expect(within(positionsView).getByRole('button', { name: '卡片视图' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await user.click(within(positionsView).getByRole('button', { name: '表格视图' }))
  await waitFor(() => expect(screen.getByRole('columnheader', { name: '代码' })).toBeInTheDocument())

  const performanceView = screen.getByRole('group', { name: '一键买入跟踪视图' })
  expect(screen.getByRole('article', { name: '贵州茅台 600519' })).toBeInTheDocument()
  await user.click(within(performanceView).getByRole('button', { name: '表格视图' }))
  await waitFor(() => expect(screen.getByRole('columnheader', { name: '推荐日' })).toBeInTheDocument())

  const tradesView = screen.getByRole('group', { name: '成交记录视图' })
  expect(screen.getByRole('article', { name: '沪深300ETF 510300' })).toBeInTheDocument()
  await user.click(within(tradesView).getByRole('button', { name: '表格视图' }))
  await waitFor(() => expect(screen.getByRole('columnheader', { name: '方向' })).toBeInTheDocument())

  await user.click(within(positionsView).getByRole('button', { name: '卡片视图' }))
  await user.click(
    within(screen.getByRole('article', { name: '标普油气ETF嘉实 159518' })).getByRole(
      'button',
      { name: '删除' },
    ),
  )
  expect(confirmSpy).toHaveBeenCalledWith(
    '删除 标普油气ETF嘉实？\n将当作从未买过：回补资金、作废相关成交，不计入收益。',
  )
  expect(deletePaperPosition).toHaveBeenCalledWith('159518')
})

it('跟踪分页失败时保留已有数据并在对应区块展示错误', async () => {
  const user = userEvent.setup()
  fetchOneClickPerf
    .mockResolvedValueOnce({
      open_rows: [
        {
          symbol: '600519',
          name: '贵州茅台',
          rec_date: '2026-07-24',
          buy_price: 1510.5,
          last: 1525.2,
          unrealized_pnl_pct: 0.00973,
        },
      ],
      page: 1,
      pages: 2,
      open_total: 21,
      trades_count: 1,
      account_equity: 100500,
    })
    .mockRejectedValueOnce(new Error('跟踪加载失败'))

  render(<PaperPage />)

  const section = await screen.findByRole('region', { name: '一键买入跟踪' })
  expect(within(section).getByRole('article', { name: '贵州茅台 600519' })).toBeInTheDocument()
  await user.click(within(section).getByRole('button', { name: '下一页' }))

  expect(await within(section).findByRole('alert')).toHaveTextContent('跟踪加载失败')
  expect(within(section).getByRole('article', { name: '贵州茅台 600519' })).toBeInTheDocument()
})

it('成交分页失败时保留已有数据并在对应区块展示错误', async () => {
  const user = userEvent.setup()
  fetchPaperTrades
    .mockResolvedValueOnce({
      trades: [
        {
          created_at: '2026-07-24T10:12:13',
          side: 'sell',
          symbol: '510300',
          name: '沪深300ETF',
          qty: 100,
          price: 4.12,
          source: 'manual',
        },
      ],
      page: 1,
      pages: 2,
      total: 21,
    })
    .mockRejectedValueOnce(new Error('成交加载失败'))

  render(<PaperPage />)

  const section = await screen.findByRole('region', { name: '成交记录' })
  expect(within(section).getByRole('article', { name: '沪深300ETF 510300' })).toBeInTheDocument()
  await user.click(within(section).getByRole('button', { name: '下一页' }))

  expect(await within(section).findByRole('alert')).toHaveTextContent('成交加载失败')
  expect(within(section).getByRole('article', { name: '沪深300ETF 510300' })).toBeInTheDocument()
})
