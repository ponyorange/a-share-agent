// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import PaperTraderPage from './PaperTraderPage'

const startPaperTrader = vi.fn(async () => ({ status: 'running' }))
const fetchPaperTraderCockpit = vi.fn(async () => ({
  session: { status: 'stopped', mode: 'signal_first', interval_sec: 600, risk: {}, stats_today: {} },
  paper: { cash: 100000, equity: 100000, market_value: 0, positions: [], positions_count: 0 },
  candidates: [{ symbol: '600000', direction: 'buy', rule_score: 0.7 }],
  decisions: { page: 1, page_size: 20, total: 0, items: [] },
  meta: { is_trading: false, is_trading_day: true, server_now: '2026-08-12T00:00:00Z' },
}))

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchPaperTraderCockpit: () => fetchPaperTraderCockpit(),
    startPaperTrader: () => startPaperTrader(),
    pausePaperTrader: vi.fn(),
    stopPaperTrader: vi.fn(),
    resumePaperTrader: vi.fn(),
    patchPaperTrader: vi.fn(),
    fetchAdvisorKline: vi.fn(async () => ({ bars: [] })),
  }
})

vi.mock('../components/PaperTraderChart', () => ({
  default: ({ symbol }: { symbol: string | null }) => (
    <div data-testid="chart">chart:{symbol || 'none'}</div>
  ),
}))

beforeEach(() => {
  startPaperTrader.mockClear()
  fetchPaperTraderCockpit.mockClear()
})

it('loads cockpit and starts trader', async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <PaperTraderPage />
    </MemoryRouter>,
  )
  await waitFor(() => {
    expect(screen.getByText('未启动')).toBeInTheDocument()
  })
  await user.click(screen.getByRole('button', { name: '启动' }))
  await waitFor(() => {
    expect(startPaperTrader).toHaveBeenCalled()
  })
})

it('renders three cabin panels and selects first candidate for chart', async () => {
  render(
    <MemoryRouter>
      <PaperTraderPage />
    </MemoryRouter>,
  )
  await waitFor(() => {
    expect(screen.getByText('候选池')).toBeInTheDocument()
  })
  expect(screen.getByText('迷你持仓')).toBeInTheDocument()
  expect(screen.getByText('决策时间线')).toBeInTheDocument()
  await waitFor(() => {
    expect(screen.getByTestId('chart')).toHaveTextContent('chart:600000')
  })
})
