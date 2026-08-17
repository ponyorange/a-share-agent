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

  it('shows provider tabs and slot table', async () => {
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('tab', { name: 'DeepSeek' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Kimi 未配置' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '千问 未配置' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '模块' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '提供方' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '模型' })).toBeInTheDocument()
    expect(screen.getByLabelText('主 Agent 对话 提供方')).toBeInTheDocument()
  })

  it('shows only the active provider panel', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    expect(await screen.findByLabelText('搜索 DeepSeek 模型')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '千问 未配置' }))
    expect(screen.queryByLabelText('搜索 DeepSeek 模型')).not.toBeInTheDocument()
    expect(screen.getByRole('tabpanel')).toHaveTextContent('千问')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('未配置')
  })

  it('filters models and keeps selected matches first', async () => {
    const user = userEvent.setup()
    const available = [
      { id: 'deepseek-v4-flash' },
      { id: 'deepseek-chat' },
      { id: 'deepseek-reasoner' },
      { id: 'deepseek-v4-pro' },
      { id: 'alpha-pro' },
      { id: 'beta-lite' },
      { id: 'gamma-max' },
      { id: 'delta-coder' },
      { id: 'echo-mini' },
      { id: 'foxtrot-pro' },
      { id: 'golf-base' },
      { id: 'hotel-plus' },
    ]
    const base = configuredFixture()
    vi.mocked(api.fetchLlmSettings).mockResolvedValue(
      configuredFixture({
        providers: {
          ...base.providers,
          deepseek: {
            ...base.providers.deepseek,
            available_models: available,
            enabled_models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
          },
        },
      }),
    )
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    const search = await screen.findByLabelText('搜索 DeepSeek 模型')
    expect(screen.getByText('已选 2 / 共 12')).toBeInTheDocument()
    await user.type(search, 'pro')
    const list = screen.getByTestId('llm-settings-model-list')
    expect(list).toHaveTextContent('deepseek-v4-pro')
    expect(list).toHaveTextContent('alpha-pro')
    expect(list).toHaveTextContent('foxtrot-pro')
    expect(list).not.toHaveTextContent('deepseek-v4-flash')
    const labels = within(list)
      .getAllByRole('checkbox')
      .map((el) => el.closest('label')?.textContent?.trim())
    expect(labels[0]).toBe('deepseek-v4-pro')
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

  it('apply-all switches every slot to that provider default', async () => {
    const user = userEvent.setup()
    const qwen = {
      configured: true,
      key_hint: 'sk-q…wen1',
      available_models: [{ id: 'qwen3.7-plus' }, { id: 'qwen3-max' }],
      enabled_models: ['qwen3.7-plus', 'qwen3-max'],
      default_model: 'qwen3.7-plus',
      last_validated_at: null,
      models_synced_at: '2026-08-17T00:00:00Z',
    }
    const base = configuredFixture()
    vi.mocked(api.fetchLlmSettings).mockResolvedValue(
      configuredFixture({
        providers: { ...base.providers, qwen },
      }),
    )
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    await user.click(await screen.findByRole('button', { name: '全部使用千问' }))
    expect(screen.getByLabelText('主 Agent 对话 提供方')).toHaveValue('qwen')
    expect(screen.getByLabelText('主 Agent 对话 模型')).toHaveValue(
      'qwen3.7-plus',
    )
    expect(screen.getByLabelText('委员会·深度 提供方')).toHaveValue('qwen')
    expect(screen.getByLabelText('委员会·深度 模型')).toHaveValue(
      'qwen3.7-plus',
    )
    expect(
      screen.getAllByText('已将全部功能模块改为 千问，请点底部保存。').length,
    ).toBeGreaterThan(0)
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
      (await screen.findAllByText('开启 Tavily 前请先填写有效的 API Key')).length,
    ).toBeGreaterThan(0)
    expect(api.saveLlmSettings).not.toHaveBeenCalled()
  })

  it('saves enabled models only for configured providers', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <AgentSettingsPage />
      </MemoryRouter>,
    )
    await user.click(await screen.findByRole('button', { name: '保存' }))
    await waitFor(() => {
      expect(api.saveLlmSettings).toHaveBeenCalled()
    })
    const body = vi.mocked(api.saveLlmSettings).mock.calls[0][0]
    expect(body.enabled_models).toEqual({
      deepseek: ['deepseek-v4-flash', 'deepseek-v4-pro'],
    })
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
