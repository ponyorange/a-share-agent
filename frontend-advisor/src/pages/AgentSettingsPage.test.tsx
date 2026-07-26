// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../agentApi'
import AgentSettingsPage from './AgentSettingsPage'

vi.mock('../agentApi', async () => {
  const actual = await vi.importActual<typeof api>('../agentApi')
  return {
    ...actual,
    fetchLlmSettings: vi.fn(),
    saveLlmSettings: vi.fn(),
    clearLlmSettings: vi.fn(),
    clearTavilySettings: vi.fn(),
  }
})

describe('AgentSettingsPage web search', () => {
  beforeEach(() => {
    vi.mocked(api.fetchLlmSettings).mockResolvedValue({
      configured: true,
      model: 'deepseek-v4-flash',
      key_hint: 'sk-t…est1',
      web_research_enabled: true,
      tavily_enabled: false,
      tavily_configured: false,
    })
    vi.mocked(api.saveLlmSettings).mockResolvedValue({
      configured: true,
      model: 'deepseek-v4-flash',
      web_research_enabled: true,
      tavily_enabled: false,
      tavily_configured: false,
    })
    vi.mocked(api.clearTavilySettings).mockResolvedValue({
      configured: true,
      web_research_enabled: true,
      tavily_enabled: false,
      tavily_configured: false,
    })
  })

  it('shows web search section with research default on', async () => {
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    expect(await screen.findByText('联网搜索')).toBeInTheDocument()
    const research = await screen.findByRole('checkbox', {
      name: /DeepSeek 联网综述/,
    })
    expect(research).toBeChecked()
  })

  it('shows Tavily key input only when enabled', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    expect(screen.queryByLabelText(/Tavily API Key/)).not.toBeInTheDocument()
    const tavily = await screen.findByRole('checkbox', {
      name: /Tavily 搜索/,
    })
    await user.click(tavily)
    expect(await screen.findByPlaceholderText('tvly-…')).toBeInTheDocument()
  })

  it('blocks enabling Tavily without key', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    const tavily = await screen.findByRole('checkbox', {
      name: /Tavily 搜索/,
    })
    await user.click(tavily)
    await user.click(screen.getByRole('button', { name: '保存' }))
    expect(
      await screen.findByText('开启 Tavily 前请先填写有效的 API Key'),
    ).toBeInTheDocument()
    expect(api.saveLlmSettings).not.toHaveBeenCalled()
  })

  it('clears Tavily key', async () => {
    vi.mocked(api.fetchLlmSettings).mockResolvedValue({
      configured: true,
      web_research_enabled: true,
      tavily_enabled: true,
      tavily_configured: true,
      tavily_key_hint: 'tvly…xxxx',
    })
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    await user.click(await screen.findByRole('button', { name: '清除 Tavily Key' }))
    await waitFor(() => {
      expect(api.clearTavilySettings).toHaveBeenCalled()
    })
  })
})
