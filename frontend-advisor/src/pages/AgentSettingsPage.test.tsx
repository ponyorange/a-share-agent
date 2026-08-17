// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
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
    saveLlmProvider: vi.fn(),
    refreshLlmProviderModels: vi.fn(),
    clearLlmProvider: vi.fn(),
  }
})

function configuredFixture(over: Partial<api.LlmSettings> = {}): api.LlmSettings {
  const deepseek = {
    configured: true,
    key_hint: 'sk-t…est1',
    available_models: [{ id: 'deepseek-v4-flash' }, { id: 'deepseek-v4-pro' }],
    enabled_models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
    default_model: 'deepseek-v4-flash',
    last_validated_at: null,
    models_synced_at: '2026-08-17T00:00:00Z',
  }
  const empty = {
    configured: false,
    key_hint: null,
    available_models: [] as { id: string }[],
    enabled_models: [] as string[],
    default_model: 'kimi-k2.6',
    last_validated_at: null,
    models_synced_at: null,
  }
  const slot = { provider: 'deepseek' as const, model: 'deepseek-v4-flash' }
  return {
    configured: true,
    providers: {
      deepseek,
      kimi: { ...empty, default_model: 'kimi-k2.6' },
      qwen: { ...empty, default_model: 'qwen3.7-plus' },
    },
    slots: {
      agent: slot,
      paper: slot,
      home: slot,
      monitor: slot,
      policy: slot,
      limitup: slot,
      committee_quick: slot,
      committee_deep: slot,
    },
    web_research_enabled: true,
    tavily_enabled: false,
    tavily_configured: false,
    ...over,
  }
}

describe('AgentSettingsPage web search', () => {
  beforeEach(() => {
    vi.mocked(api.fetchLlmSettings).mockResolvedValue(configuredFixture())
    vi.mocked(api.saveLlmSettings).mockResolvedValue(configuredFixture())
    vi.mocked(api.clearTavilySettings).mockResolvedValue(configuredFixture())
  })

  it('shows provider cards and slot rows', async () => {
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Kimi' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '千问' })).toBeInTheDocument()
    expect(screen.getByText('主 Agent 对话')).toBeInTheDocument()
  })

  it('hides unconfigured provider from slot dropdown', async () => {
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    const select = await screen.findByLabelText('主 Agent 对话 提供方')
    expect(within(select).getByRole('option', { name: 'DeepSeek' })).toBeInTheDocument()
    expect(within(select).queryByRole('option', { name: 'Kimi' })).not.toBeInTheDocument()
  })

  it('switching provider sets default model', async () => {
    const user = userEvent.setup()
    const kimi = {
      configured: true,
      key_hint: 'sk-k…imi1',
      available_models: [{ id: 'kimi-k2.6' }, { id: 'kimi-k3' }],
      enabled_models: ['kimi-k2.6', 'kimi-k3'],
      default_model: 'kimi-k2.6',
      last_validated_at: null,
      models_synced_at: '2026-08-17T00:00:00Z',
    }
    const base = configuredFixture()
    vi.mocked(api.fetchLlmSettings).mockResolvedValue(
      configuredFixture({
        providers: { ...base.providers, kimi },
      }),
    )
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    await user.selectOptions(
      await screen.findByLabelText('主 Agent 对话 提供方'),
      'kimi',
    )
    expect(screen.getByLabelText('主 Agent 对话 模型')).toHaveValue('kimi-k2.6')
  })

  it('disables web research when agent is not deepseek', async () => {
    const kimi = {
      configured: true,
      key_hint: 'sk-k…imi1',
      available_models: [{ id: 'kimi-k2.6' }],
      enabled_models: ['kimi-k2.6'],
      default_model: 'kimi-k2.6',
      last_validated_at: null,
      models_synced_at: '2026-08-17T00:00:00Z',
    }
    const base = configuredFixture()
    const kimiSlot = { provider: 'kimi' as const, model: 'kimi-k2.6' }
    vi.mocked(api.fetchLlmSettings).mockResolvedValue(
      configuredFixture({
        providers: { ...base.providers, kimi },
        slots: { ...base.slots, agent: kimiSlot },
      }),
    )
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    const research = await screen.findByRole('checkbox', {
      name: /DeepSeek 联网综述/,
    })
    expect(research).toBeDisabled()
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
    vi.mocked(api.fetchLlmSettings).mockResolvedValue(
      configuredFixture({
        tavily_enabled: true,
        tavily_configured: true,
        tavily_key_hint: 'tvly…xxxx',
      }),
    )
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
