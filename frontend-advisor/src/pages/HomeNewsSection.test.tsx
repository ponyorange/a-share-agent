import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { HomeNewsSection } from './HomeNewsSection'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchHomeNews: vi.fn(),
    fetchHomeNewsBrief: vi.fn(),
    refreshHomeNewsBrief: vi.fn(),
  }
})

const emptyGroups = {
  cctv: {
    ok: true,
    source: 'c',
    error: null,
    items: [
      { title: '联播头条', summary: null, published_at: null, url: null, tags: null },
    ],
  },
  macro: { ok: false, source: null, error: 'x', items: [] },
  index_sentiment: { ok: false, source: null, error: 'x', items: [] },
  sectors: {
    ok: true,
    source: 's',
    error: null,
    items: [
      {
        title: '人工智能',
        summary: '+5%',
        published_at: null,
        url: null,
        tags: ['sector'],
      },
    ],
  },
  web: { ok: false, source: null, error: null, items: [] },
}

describe('HomeNewsSection', () => {
  beforeEach(() => {
    vi.mocked(api.fetchHomeNews).mockReset()
    vi.mocked(api.fetchHomeNewsBrief).mockReset()
    vi.mocked(api.refreshHomeNewsBrief).mockReset()
  })

  it('loads news and idle brief without calling refresh', async () => {
    vi.mocked(api.fetchHomeNews).mockResolvedValue({
      trade_date: '2026-08-01',
      as_of: 't',
      groups: emptyGroups,
    })
    vi.mocked(api.fetchHomeNewsBrief).mockResolvedValue({
      trade_date: '2026-08-01',
      status: 'idle',
      summary: '',
      bullets: [],
      sectors: [],
      symbols: [],
    })
    render(<HomeNewsSection />)
    await waitFor(() => expect(screen.getByText('联播头条')).toBeInTheDocument())
    expect(screen.getByText(/点「刷新解读」/)).toBeInTheDocument()
    expect(api.refreshHomeNewsBrief).not.toHaveBeenCalled()
  })

  it('refresh button posts and shows ready brief', async () => {
    vi.mocked(api.fetchHomeNews).mockResolvedValue({
      trade_date: '2026-08-01',
      as_of: 't',
      groups: emptyGroups,
    })
    vi.mocked(api.fetchHomeNewsBrief).mockResolvedValue({
      trade_date: '2026-08-01',
      status: 'idle',
      summary: '',
      bullets: [],
      sectors: [],
      symbols: [],
    })
    vi.mocked(api.refreshHomeNewsBrief).mockResolvedValue({
      trade_date: '2026-08-01',
      status: 'ready',
      summary: '政策偏暖',
      bullets: ['要点一'],
      sectors: [{ name: '人工智能', reason: '活跃' }],
      symbols: [{ symbol: '600519', name: '贵州茅台', reason: '观察' }],
    })
    render(<HomeNewsSection />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '刷新解读' })).toBeEnabled(),
    )
    await userEvent.click(screen.getByRole('button', { name: '刷新解读' }))
    expect(api.refreshHomeNewsBrief).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.getByText('政策偏暖')).toBeInTheDocument())
    expect(screen.getAllByText('人工智能').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/600519/)).toBeInTheDocument()
  })
})
