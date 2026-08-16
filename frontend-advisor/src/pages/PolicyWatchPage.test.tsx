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
      {
        id: 'gov_zhengce',
        name: '中国政府网 · 最新政策',
        list_url: 'https://www.gov.cn/zhengce/zuixin/',
      },
      {
        id: 'scio_news',
        name: '国新办 · 新闻发布',
        list_url: 'https://www.gov.cn/lianbo/fabu/',
      },
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
  const gov = screen.getByRole('link', { name: '中国政府网 · 最新政策' })
  expect(gov).toHaveAttribute('href', 'https://www.gov.cn/zhengce/zuixin/')
  expect(gov).toHaveAttribute('target', '_blank')
  const scio = screen.getByRole('link', { name: '国新办 · 新闻发布' })
  expect(scio).toHaveAttribute('href', 'https://www.gov.cn/lianbo/fabu/')
  expect(scio).toHaveAttribute('target', '_blank')
})

it('收件箱原文仅 http(s) 可打开，股票代码链到 K 线', async () => {
  fetchPolicyWatchItems.mockResolvedValue({
    items: [
      {
        id: '1',
        article_id: 'a1',
        title: '国务院印发指导意见',
        source_label: '中国政府网 · 最新政策',
        url: 'https://www.gov.cn/zhengce/content/2026-08/13/c_1.htm',
        created_at: '2026-08-13T02:00:00+00:00',
        summary: '关注新能源产业链。',
        direction: 'up',
        impact_score: 0.8,
        symbols: [{ symbol: '300750', name: '宁德时代', verified: true }],
        notify_status: 'sent',
      },
      {
        id: '2',
        article_id: 'a2',
        title: '新闻联播摘要',
        source_label: '新闻联播',
        url: 'policy://cctv/20260813/title',
        created_at: '2026-08-13T03:00:00+00:00',
        notify_status: 'skipped',
      },
    ],
  })
  render(
    <MemoryRouter>
      <PolicyWatchPage />
    </MemoryRouter>,
  )
  const original = await screen.findByRole('link', { name: '打开原文' })
  expect(original).toHaveAttribute(
    'href',
    'https://www.gov.cn/zhengce/content/2026-08/13/c_1.htm',
  )
  expect(original).toHaveAttribute('target', '_blank')
  expect(screen.getAllByRole('link', { name: '打开原文' })).toHaveLength(1)
  const stock = screen.getByRole('link', { name: /300750/ })
  expect(stock).toHaveAttribute(
    'href',
    'http://127.0.0.1:5173/akshare/kline?symbol=300750&range=daily',
  )
  expect(stock).toHaveAttribute('target', '_blank')
})

it('收件箱可翻到下一页', async () => {
  const user = userEvent.setup()
  fetchPolicyWatchItems.mockImplementation(async (opts?: { page?: number }) => {
    if ((opts?.page || 1) >= 2) {
      return {
        items: [
          {
            id: '2',
            article_id: 'a2',
            title: '第二页文章',
            source_label: '新闻联播',
            url: '',
            created_at: '2026-08-13T01:00:00+00:00',
            notify_status: 'skipped',
          },
        ],
        page: 2,
        page_size: 10,
        total: 11,
        next_cursor: null,
      }
    }
    return {
      items: [
        {
          id: '1',
          article_id: 'a1',
          title: '第一页文章',
          source_label: '中国政府网 · 最新政策',
          url: 'https://www.gov.cn/zhengce/content/2026-08/13/c_1.htm',
          created_at: '2026-08-13T02:00:00+00:00',
          notify_status: 'sent',
        },
      ],
      page: 1,
      page_size: 10,
      total: 11,
      next_cursor: '2026-08-13T02:00:00+00:00',
    }
  })
  render(
    <MemoryRouter>
      <PolicyWatchPage />
    </MemoryRouter>,
  )
  expect(await screen.findByText('第一页文章')).toBeInTheDocument()
  expect(screen.getByText('1 / 2')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '下一页' }))
  expect(await screen.findByText('第二页文章')).toBeInTheDocument()
  expect(fetchPolicyWatchItems).toHaveBeenCalledWith(
    expect.objectContaining({ page: 2, limit: 10 }),
  )
})
