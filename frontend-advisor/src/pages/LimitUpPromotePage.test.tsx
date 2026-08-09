import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import LimitUpPromotePage from './LimitUpPromotePage'

const streamLimitUpPromote = vi.hoisted(() => vi.fn())

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    streamLimitUpPromote,
  }
})

const sample = {
  date: '2026-07-31',
  as_of: '2026-07-31T14:30:00+08:00',
  summary: '高连板封单相对更稳，优先观察龙头。',
  candidate_count: 12,
  from_cache: false,
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
  streamLimitUpPromote.mockReset()
  streamLimitUpPromote.mockImplementation(
    async (
      _force: boolean,
      handlers: {
        onProgress?: (row: { message?: string }) => void
        onThinking?: (delta: string) => void
        onDone?: (data: typeof sample) => void
      },
    ) => {
      handlers.onProgress?.({ message: '正在获取当日封板池…' })
      handlers.onThinking?.('先看连板高度')
      handlers.onDone?.(sample)
    },
  )
})

it('renders picks and thinking from stream', async () => {
  render(
    <MemoryRouter>
      <LimitUpPromotePage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('浦发银行')).toBeInTheDocument()
  expect(screen.getByText('600000')).toBeInTheDocument()
  expect(screen.getByTestId('promote-thinking')).toHaveTextContent('先看连板高度')
  expect(screen.getByTestId('promote-summary')).toHaveTextContent(/高连板/)
  expect(screen.getByTestId('promote-theme')).toHaveTextContent(
    /今日资讯\/政策/,
  )
  expect(streamLimitUpPromote).toHaveBeenCalledWith(
    false,
    expect.any(Object),
    expect.any(AbortSignal),
  )
})

it('shows missing DeepSeek key guidance', async () => {
  streamLimitUpPromote.mockImplementationOnce(
    async (
      _force: boolean,
      handlers: { onError?: (detail: string) => void },
    ) => {
      handlers.onError?.('请先配置 DeepSeek API Key')
    },
  )
  render(
    <MemoryRouter>
      <LimitUpPromotePage />
    </MemoryRouter>,
  )

  expect(
    await screen.findByText('请先配置 DeepSeek API Key'),
  ).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'DeepSeek 配置' })).toHaveAttribute(
    'href',
    '/agent/settings',
  )
})

it('refresh button forces reload and shows progress', async () => {
  const user = userEvent.setup()
  let finish: (() => void) | null = null
  streamLimitUpPromote
    .mockImplementationOnce(async (_f, handlers) => {
      handlers.onDone?.(sample)
    })
    .mockImplementationOnce(async (force, handlers) => {
      expect(force).toBe(true)
      handlers.onProgress?.({ message: '正在研判 12 只封板摘要…' })
      await new Promise<void>((resolve) => {
        finish = resolve
      })
      handlers.onDone?.(sample)
    })

  render(
    <MemoryRouter>
      <LimitUpPromotePage />
    </MemoryRouter>,
  )
  await screen.findByText('浦发银行')
  await user.click(screen.getByRole('button', { name: '刷新研判' }))
  expect(await screen.findByTestId('promote-progress')).toHaveTextContent(
    /正在研判/,
  )
  finish?.()
  await waitFor(() => {
    expect(screen.getByRole('button', { name: '刷新研判' })).toBeEnabled()
  })
})
