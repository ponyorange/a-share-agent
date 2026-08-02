import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import LimitUpPage, { defaultTierExpanded } from './LimitUpPage'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchLimitUp: vi.fn(),
    fetchRegimeSentiment: vi.fn(),
  }
})

const sample: api.LimitUpResponse = {
  source: 'akshare',
  as_of: '2026-07-31T10:00:00+08:00',
  date: '20260731',
  session: { is_trading: true, is_trading_day: true },
  today: [
    {
      symbol: '000001',
      name: '平安银行',
      day_chg_pct: 0.1,
      board_count: 1,
      status: 'sealed',
      limit_up_price: 12.5,
      main_inflow: 58_759_776,
      main_outflow: 28_347_768,
      main_net_inflow: 30_412_008,
    },
    {
      symbol: '000002',
      name: '万科A',
      day_chg_pct: 0.05,
      board_count: 1,
      status: 'broken',
      limit_up_price: null,
      main_inflow: null,
      main_outflow: null,
      main_net_inflow: -1_200_000,
    },
  ],
  ladder: [
    {
      board_count: 2,
      items: [
        {
          symbol: '600000',
          name: '浦发银行',
          day_chg_pct: 0.1,
          main_inflow: 100_000_000,
          main_outflow: 55_000_000,
          main_net_inflow: 45_000_000,
        },
      ],
    },
    {
      board_count: 1,
      items: [
        {
          symbol: '000001',
          name: '平安银行',
          day_chg_pct: 0.1,
          main_net_inflow: 30_412_008,
        },
        { symbol: '000002', name: '万科A', day_chg_pct: 0.05 },
      ],
    },
  ],
}

function setViewport(pc: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: pc ? query.includes('min-width: 900px') : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

describe('LimitUpPage', () => {
  beforeEach(() => {
    vi.mocked(api.fetchLimitUp).mockReset()
    vi.mocked(api.fetchRegimeSentiment).mockReset()
    vi.mocked(api.fetchRegimeSentiment).mockResolvedValue({
      sentiment_cycle: 'repair',
      metrics: { sentiment_score: 0.42 },
    })
    setViewport(false)
  })

  it('窄屏：交易时段展示当天涨停与连板，顺序为当天在前', async () => {
    vi.mocked(api.fetchLimitUp).mockResolvedValue(sample)
    render(
      <MemoryRouter>
        <LimitUpPage />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getAllByText('平安银行').length).toBeGreaterThan(0)
    })
    expect(screen.getByText('当前涨停')).toBeInTheDocument()
    expect(screen.getByText('浦发银行')).toBeInTheDocument()
    const today = screen.getByTestId('today-section')
    const ladder = screen.getByTestId('ladder-section')
    expect(within(today).getByText('平安银行')).toBeInTheDocument()
    expect(today.compareDocumentPosition(ladder) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(today).getByText('+3041.20万')).toBeInTheDocument()
    expect(within(ladder).getByText('+4500.00万')).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader', { name: '主力流入' }).length).toBeGreaterThan(0)
  })

  it('展示市场情绪周期与分数，并链接到市场状态页', async () => {
    vi.mocked(api.fetchLimitUp).mockResolvedValue(sample)
    vi.mocked(api.fetchRegimeSentiment).mockResolvedValue({
      sentiment_cycle: 'strengthen',
      metrics: { sentiment_score: 0.876 },
    })

    render(
      <MemoryRouter>
        <LimitUpPage />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('市场情绪')).toBeInTheDocument()
    })
    expect(screen.getByText('情绪增强')).toBeInTheDocument()
    expect(screen.getByText('0.88')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看市场状态' })).toHaveAttribute(
      'href',
      '/regime',
    )
  })

  it('非交易时段隐藏当天涨停明细', async () => {
    vi.mocked(api.fetchLimitUp).mockResolvedValue({
      ...sample,
      session: { is_trading: false, is_trading_day: true },
    })
    render(
      <MemoryRouter>
        <LimitUpPage />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getByTestId('today-hidden')).toBeInTheDocument()
    })
    expect(screen.queryByText('当前涨停')).not.toBeInTheDocument()
    expect(screen.getByText('浦发银行')).toBeInTheDocument()
  })

  it('PC：连板在前；≥2 展开、1 连板与当天涨停默认折叠', async () => {
    setViewport(true)
    vi.mocked(api.fetchLimitUp).mockResolvedValue(sample)
    render(
      <MemoryRouter>
        <LimitUpPage />
      </MemoryRouter>,
    )
    await waitFor(() => {
      expect(screen.getByText('浦发银行')).toBeInTheDocument()
    })
    const today = screen.getByTestId('today-section')
    const ladder = screen.getByTestId('ladder-section')
    expect(
      ladder.compareDocumentPosition(today) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

    expect(screen.getByTestId('tier-2')).toHaveAttribute('data-expanded', 'true')
    expect(screen.getByTestId('tier-1')).toHaveAttribute('data-expanded', 'false')
    expect(within(screen.getByTestId('tier-1')).queryByText('平安银行')).toBeNull()
    expect(screen.queryByText('当前涨停')).not.toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /1 连板 · 2 只/ }))
    expect(screen.getByTestId('tier-1')).toHaveAttribute('data-expanded', 'true')
    expect(within(screen.getByTestId('tier-1')).getByText('平安银行')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /当天涨停 · 封板 1 \/ 炸板 1/ }))
    expect(screen.getByText('当前涨停')).toBeInTheDocument()
  })
})

describe('limitUp helpers', () => {
  it('formats decimal ratio as percent', () => {
    expect(api.formatLimitUpChg(0.1)).toBe('+10.00%')
    expect(api.formatLimitUpChg(-0.05)).toBe('-5.00%')
  })

  it('shows today table only while trading', () => {
    expect(api.shouldShowTodayTable({ is_trading: true })).toBe(true)
    expect(api.shouldShowTodayTable({ is_trading: false })).toBe(false)
  })

  it('defaults tier expand for multi-board only', () => {
    expect(defaultTierExpanded(1)).toBe(false)
    expect(defaultTierExpanded(2)).toBe(true)
    expect(defaultTierExpanded(5)).toBe(true)
  })

  it('formats fund money as wan/yi', () => {
    expect(api.formatLimitUpMoney(30_412_008)).toBe('+3041.20万')
    expect(api.formatLimitUpMoney(1.2e8)).toBe('+1.20亿')
    expect(api.formatLimitUpMoney(-1_200_000)).toBe('-120.00万')
    expect(api.formatLimitUpMoney(null)).toBe('—')
  })
})
