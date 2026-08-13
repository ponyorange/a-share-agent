import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import PolicyWatchPage from './PolicyWatchPage'

const fetchPolicyWatchSettings = vi.hoisted(() => vi.fn())
const fetchPolicyWatchPresets = vi.hoisted(() => vi.fn())
const fetchPolicyWatchItems = vi.hoisted(() => vi.fn())
const savePolicyWatchSettings = vi.hoisted(() => vi.fn())

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchPolicyWatchSettings,
    fetchPolicyWatchPresets,
    fetchPolicyWatchItems,
    savePolicyWatchSettings,
  }
})

beforeEach(() => {
  fetchPolicyWatchSettings.mockReset()
  fetchPolicyWatchPresets.mockReset()
  fetchPolicyWatchItems.mockReset()
  savePolicyWatchSettings.mockReset()
  fetchPolicyWatchSettings.mockResolvedValue({
    enabled: false,
    sensitivity: 'medium',
    scan_mode: 'always',
    interval_trading_min: 15,
    interval_offhours_min: 60,
    preset_ids: ['gov_zhengce', 'scio_news'],
    custom_sources: [],
    llm_configured: false,
    email_verified: false,
  })
  fetchPolicyWatchPresets.mockResolvedValue({
    presets: [
      { id: 'gov_zhengce', name: '中国政府网 · 最新政策' },
      { id: 'scio_news', name: '国新办 · 新闻发布' },
    ],
  })
  fetchPolicyWatchItems.mockResolvedValue({ items: [] })
  savePolicyWatchSettings.mockImplementation(async (body) => ({
    enabled: Boolean(body.enabled),
    sensitivity: 'medium',
    scan_mode: 'always',
    interval_trading_min: 15,
    interval_offhours_min: 60,
    preset_ids: ['gov_zhengce', 'scio_news'],
    custom_sources: [],
  }))
})

it('渲染政策雷达空态与中等灵敏度，开启时保存', async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <PolicyWatchPage />
    </MemoryRouter>,
  )
  expect(await screen.findByRole('heading', { name: '政策雷达' })).toBeInTheDocument()
  expect(
    screen.getByText(/刚开启不会把旧闻刷进来/),
  ).toBeInTheDocument()
  expect(screen.getByLabelText('灵敏度')).toHaveValue('medium')
  await user.click(screen.getByRole('checkbox', { name: /开启雷达/ }))
  expect(savePolicyWatchSettings).toHaveBeenCalledWith({ enabled: true })
})
