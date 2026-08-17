import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import LimitUpPromotePage from './LimitUpPromotePage'

const fetchLimitUpPromote = vi.hoisted(() => vi.fn())
const refreshLimitUpPromote = vi.hoisted(() => vi.fn())
const fetchLimitUpPromoteStatus = vi.hoisted(() => vi.fn())
const fetchLimitUpPromoteHistory = vi.hoisted(() => vi.fn())
const fetchLimitUpPromoteHistoryDay = vi.hoisted(() => vi.fn())

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchLimitUpPromote,
    refreshLimitUpPromote,
    fetchLimitUpPromoteStatus,
    fetchLimitUpPromoteHistory,
    fetchLimitUpPromoteHistoryDay,
  }
})

const sample = {
  trade_date: '2026-07-31',
  date: '2026-07-31',
  status: 'ready',
  as_of: '2026-07-31T14:30:00+08:00',
  summary: '高连板封单相对更稳，优先观察龙头。',
  candidate_count: 12,
  theme_used: { news: true, hot_sectors: true, brief: false },
  picks: [
    {
      symbol: '600000',
      name: '浦发银行',
      board_count: 2,
      score: 4,
      reason: '封板较早，净流入为正',
    },
  ],
}

beforeEach(() => {
  fetchLimitUpPromote.mockReset()
  refreshLimitUpPromote.mockReset()
  fetchLimitUpPromoteStatus.mockReset()
  fetchLimitUpPromoteHistory.mockReset()
  fetchLimitUpPromoteHistoryDay.mockReset()
  fetchLimitUpPromote.mockResolvedValue(sample)
  fetchLimitUpPromoteHistory.mockResolvedValue({
    count: 1,
    items: [
      {
        trade_date: '2026-07-30',
        status: 'ready',
        summary: '昨日样本',
        candidate_count: 10,
        pick_count: 2,
      },
    ],
  })
})

it('renders archived picks on enter', async () => {
  render(
    <MemoryRouter>
      <LimitUpPromotePage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('浦发银行')).toBeInTheDocument()
  expect(screen.getByText('600000')).toBeInTheDocument()
  expect(screen.getByTestId('promote-summary')).toHaveTextContent(/高连板/)
  expect(screen.getByTestId('promote-theme')).toHaveTextContent(/今日资讯\/政策/)
  expect(fetchLimitUpPromote).toHaveBeenCalled()
  expect(await screen.findByTestId('promote-history')).toHaveTextContent(
    '2026-07-30',
  )
})

it('shows missing DeepSeek key guidance', async () => {
  fetchLimitUpPromote.mockRejectedValueOnce(
    new Error('请先在模型配置中填写 API Key'),
  )
  render(
    <MemoryRouter>
      <LimitUpPromotePage />
    </MemoryRouter>,
  )

  expect(
    await screen.findByText('请先在模型配置中填写 API Key'),
  ).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '模型配置' })).toHaveAttribute(
    'href',
    '/agent/settings',
  )
})

it('refresh starts background job and polls status', async () => {
  const user = userEvent.setup()
  refreshLimitUpPromote.mockResolvedValue({
    ...sample,
    status: 'running',
    progress: { phase: 'model', message: '正在研判 12 只封板摘要…' },
    picks: [],
  })
  fetchLimitUpPromoteStatus
    .mockResolvedValueOnce({
      ...sample,
      status: 'running',
      progress: { phase: 'model', message: '正在研判 12 只封板摘要…' },
      picks: [],
    })
    .mockResolvedValue({ ...sample, status: 'ready' })

  render(
    <MemoryRouter>
      <LimitUpPromotePage />
    </MemoryRouter>,
  )
  await screen.findByText('浦发银行')
  await user.click(screen.getByRole('button', { name: '刷新研判' }))
  expect(await screen.findByTestId('promote-progress')).toHaveTextContent(
    /后台|研判/,
  )
  expect(refreshLimitUpPromote).toHaveBeenCalled()
  await waitFor(
    () => {
      expect(fetchLimitUpPromoteStatus).toHaveBeenCalled()
    },
    { timeout: 4000 },
  )
})

it('history day shows accuracy with broken badge', async () => {
  const user = userEvent.setup()
  fetchLimitUpPromoteHistoryDay.mockResolvedValue({
    doc: {
      ...sample,
      date: '2026-07-30',
      trade_date: '2026-07-30',
      picks: [
        {
          symbol: '600000',
          name: '浦发银行',
          board_count: 2,
          score: 4,
          reason: '观察',
        },
      ],
    },
    accuracy: {
      trade_date: '2026-07-30',
      t1_date: '2026-07-31',
      ok: true,
      pick_count: 1,
      hit_count: 1,
      sealed_hit_count: 0,
      broken_hit_count: 1,
      miss_count: 0,
      hit_rate: 1,
      hits: [
        {
          symbol: '600000',
          name: '浦发银行',
          board_count: 2,
          score: 4,
          reason: '观察',
          hit: true,
          t1_status: 'broken',
          broken: true,
        },
      ],
      broken_hits: [
        {
          symbol: '600000',
          name: '浦发银行',
          board_count: 2,
          score: 4,
          reason: '观察',
          hit: true,
          t1_status: 'broken',
          broken: true,
        },
      ],
      misses: [],
    },
  })

  render(
    <MemoryRouter>
      <LimitUpPromotePage />
    </MemoryRouter>,
  )
  await screen.findByText('浦发银行')
  await user.click(screen.getByRole('button', { name: '2026-07-30' }))
  expect(await screen.findByTestId('promote-accuracy')).toHaveTextContent(
    /成功率/,
  )
  expect(await screen.findByTestId('promote-t1-600000')).toHaveTextContent(
    '炸板',
  )
})
