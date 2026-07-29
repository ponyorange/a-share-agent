import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import MonitorJobsPage, {
  formatCountdown,
  shouldShowCountdown,
} from './MonitorJobsPage'
import type { MonitorJob } from '../api'

const fetchMonitorJobs = vi.hoisted(() => vi.fn())
const fetchMonitorJobLogs = vi.hoisted(() => vi.fn())
const pauseMonitorJob = vi.hoisted(() => vi.fn())
const resumeMonitorJob = vi.hoisted(() => vi.fn())
const deleteMonitorJob = vi.hoisted(() => vi.fn())

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchMonitorJobs,
    fetchMonitorJobLogs,
    pauseMonitorJob,
    resumeMonitorJob,
    deleteMonitorJob,
  }
})

const baseJob: MonitorJob = {
  id: 'j1',
  title: '明天盯盘',
  status: 'scheduled',
  scope: 'watchlist',
  rules: [{ type: 'price_below', value: 4 }],
  kind: 'watch',
  repeat: 'once',
  calendar: 'trading_days',
  next_run_at: '2099-01-01T01:15:00+00:00',
  llm_enabled: false,
}

beforeEach(() => {
  fetchMonitorJobs.mockReset()
  fetchMonitorJobLogs.mockReset()
  pauseMonitorJob.mockReset()
  resumeMonitorJob.mockReset()
  deleteMonitorJob.mockReset()
  fetchMonitorJobs.mockResolvedValue({ jobs: [baseJob], count: 1 })
  fetchMonitorJobLogs.mockResolvedValue({
    logs: [
      {
        id: 'l1',
        ts: '2026-07-29T12:00:00+00:00',
        level: 'info',
        event: 'created',
        message: '已创建',
      },
    ],
    count: 1,
  })
})

it('shouldShowCountdown hides for running watch', () => {
  expect(
    shouldShowCountdown({ ...baseJob, status: 'running', kind: 'watch' }),
  ).toBe(false)
  expect(shouldShowCountdown(baseJob)).toBe(true)
})

it('formatCountdown shows remaining time', () => {
  const now = Date.parse('2099-01-01T01:00:00+00:00')
  expect(formatCountdown('2099-01-01T01:15:00+00:00', now)).toMatch(/15分/)
})

it('renders countdown and opens log drawer with poll', async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <MonitorJobsPage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('明天盯盘')).toBeInTheDocument()
  expect(screen.getByText(/盯盘 · 一次 · 交易日/)).toBeInTheDocument()
  expect(screen.getByText(/分/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '日志' }))
  expect(await screen.findByTestId('monitor-log-console')).toBeInTheDocument()
  expect(screen.getByText('已创建')).toBeInTheDocument()
  expect(fetchMonitorJobLogs).toHaveBeenCalled()
})
