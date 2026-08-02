import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import HomePage, { breadthFromRegime } from './HomePage'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchMarket: vi.fn(),
    fetchRegimeSummary: vi.fn(),
    fetchLimitUp: vi.fn(),
    fetchHomeSectors: vi.fn(),
  }
})

describe('HomePage', () => {
  beforeEach(() => {
    vi.mocked(api.fetchMarket).mockReset()
    vi.mocked(api.fetchRegimeSummary).mockReset()
    vi.mocked(api.fetchLimitUp).mockReset()
    vi.mocked(api.fetchHomeSectors).mockReset()
  })

  it('breadthFromRegime reads evidence when metrics omit breadth', () => {
    expect(
      breadthFromRegime({
        gate_level: 'normal',
        trend_regime: 'range',
        sentiment_cycle: 'strengthen',
        position_cap: 0.7,
        data_quality: 'ok',
        metrics: {},
        evidence: [{ key: 'breadth', value: '0.8889', note: '上涨家数占比' }],
      }),
    ).toBeCloseTo(0.8889)
  })

  it('renders market tiles even when regime summary hangs', async () => {
    vi.mocked(api.fetchMarket).mockResolvedValue({
      featured: [
        { symbol: '000300', name: '沪深300', price: 4588, change_pct: -0.8 },
      ],
    })
    vi.mocked(api.fetchRegimeSummary).mockImplementation(() => new Promise(() => {}))
    vi.mocked(api.fetchLimitUp).mockResolvedValue({
      source: 'akshare',
      as_of: '2026-08-01T10:00:00+08:00',
      date: '20260801',
      session: { is_trading: false, is_trading_day: true },
      today: [],
      ladder: [{ board_count: 5, items: [] }],
    })
    vi.mocked(api.fetchHomeSectors).mockResolvedValue({
      trade_date: '2026-08-01',
      ok: true,
      source: 't',
      items: [{ rank: 1, name: '人工智能', change_pct: 5.1, strength: 1 }],
    })

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('沪深300')).toBeInTheDocument()
    })
    expect(screen.getByText(/人工智能/)).toBeInTheDocument()
    expect(screen.getByText('趋势 · 情绪 · 闸门')).toBeInTheDocument()
  })

  it('shows breadth from evidence and featured up/down counts', async () => {
    vi.mocked(api.fetchMarket).mockResolvedValue({
      featured: [
        { symbol: '000300', name: '沪深300', change_pct: 1.2 },
        { symbol: '000001', name: '上证指数', change_pct: -0.3 },
        { symbol: '399006', name: '创业板指', change_pct: 0.5 },
      ],
    })
    vi.mocked(api.fetchRegimeSummary).mockResolvedValue({
      gate_level: 'normal',
      trend_regime: 'range',
      sentiment_cycle: 'strengthen',
      position_cap: 0.7,
      data_quality: 'ok',
      metrics: { max_board: 7, promotion_rate: 0.2, limit_up_count: 60 },
      evidence: [{ key: 'breadth', value: '0.8889', note: '上涨家数占比' }],
    } as api.RegimeCurrent)
    vi.mocked(api.fetchLimitUp).mockResolvedValue({
      source: 'akshare',
      as_of: '2026-08-01T10:00:00+08:00',
      date: '20260801',
      session: { is_trading: false, is_trading_day: true },
      today: [],
      ladder: [],
    })
    vi.mocked(api.fetchHomeSectors).mockResolvedValue({
      trade_date: '2026-08-01',
      ok: true,
      source: 't',
      items: [],
    })

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(/上涨 2/)).toBeInTheDocument()
    })
    expect(screen.getByText(/下跌 1/)).toBeInTheDocument()
    expect(screen.getByText('88.9%')).toBeInTheDocument()
  })

  it('links to regime and limitup', async () => {
    vi.mocked(api.fetchMarket).mockResolvedValue({ featured: [] })
    vi.mocked(api.fetchRegimeSummary).mockResolvedValue({
      gate_level: 'normal',
      trend_regime: 'range',
      sentiment_cycle: 'strengthen',
      position_cap: 0.7,
      data_quality: 'ok',
      metrics: {
        breadth: 0.52,
        max_board: 7,
        promotion_rate: 0.2,
        limit_up_count: 60,
      },
      evidence: [],
    } as api.RegimeCurrent)
    vi.mocked(api.fetchLimitUp).mockResolvedValue({
      source: 'akshare',
      as_of: '2026-08-01T10:00:00+08:00',
      date: '20260801',
      session: { is_trading: false, is_trading_day: true },
      today: [],
      ladder: [],
    })
    vi.mocked(api.fetchHomeSectors).mockResolvedValue({
      trade_date: '2026-08-01',
      ok: true,
      source: 't',
      items: [],
    })

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('link', { name: '查看今日闸门' })).toHaveAttribute(
        'href',
        '/regime',
      )
    })
    expect(screen.getByRole('link', { name: '打开打板' })).toHaveAttribute(
      'href',
      '/limitup',
    )
  })
})
