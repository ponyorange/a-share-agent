import '@testing-library/jest-dom/vitest'
import { type ReactNode } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import type { SubagentProgress } from '../agentApi'
import AgentChatPage, {
  ChatBubble,
  SubagentProgressPanel,
  mergeSubagentProgress,
} from './AgentChatPage'

const api = vi.hoisted(() => ({
  createAgentSession: vi.fn(),
  deleteAgentSession: vi.fn(),
  fetchAgentMessages: vi.fn(),
  fetchLlmSettings: vi.fn(),
  listAgentSessions: vi.fn(),
  streamAgentChat: vi.fn(),
}))

vi.mock('../agentApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../agentApi')>()
  return {
    ...actual,
    ...api,
  }
})

vi.mock('react-virtuoso', () => ({
  Virtuoso: ({
    data,
    itemContent,
  }: {
    data: unknown[]
    itemContent: (index: number, item: unknown) => ReactNode
  }) => (
    <div data-testid="agent-virtuoso">
      {data.map((item, index) => (
        <div key={index}>{itemContent(index, item)}</div>
      ))}
    </div>
  ),
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

type CapturedStream = {
  handlers: {
    onMeta?: (meta: { session_id: string; context_messages?: number }) => void
    onTool?: (row: { tool: string; content: string }) => void
    onSubagentProgress?: (progress: SubagentProgress) => void
    onToken?: (delta: string) => void
    onDone?: (data: { session_id?: string; reply: string; tool_trace?: { tool: string; content: string }[] }) => void
    onError?: (detail: string) => void
  }
  signal?: AbortSignal
  resolve: () => void
}

const completedFetchProgress: SubagentProgress = {
  phase: 'data_agent',
  step: 'fetch',
  status: 'completed',
  message: '已获取 53 行数据',
  source: 'akshare',
  interface: 'stock_zh_index_daily_tx',
  rows: 53,
  truncated: false,
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset())
  api.fetchLlmSettings.mockResolvedValue({ configured: true })
  api.listAgentSessions.mockResolvedValue({ sessions: [] })
  api.createAgentSession.mockResolvedValue({ session_id: 's-new' })
  api.fetchAgentMessages.mockResolvedValue({ session_id: 's-new', messages: [] })
  api.deleteAgentSession.mockResolvedValue({ ok: true })
  api.streamAgentChat.mockResolvedValue(undefined)
})

it('助手消息底部可复制正文', async () => {
  const user = userEvent.setup()
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })

  render(
    <ChatBubble
      m={{ role: 'assistant', content: '今日关注：银行板块。' }}
    />,
  )

  await user.click(screen.getByRole('button', { name: '复制' }))
  expect(writeText).toHaveBeenCalledWith('今日关注：银行板块。')
  expect(await screen.findByRole('button', { name: '已复制' })).toBeInTheDocument()
})

it('流式输出中不显示复制按钮', () => {
  render(
    <ChatBubble
      m={{ role: 'assistant', content: '半截回复', streaming: true }}
    />,
  )
  expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument()
})

it('合并相同进度条目并按新阶段追加', () => {
  const started: SubagentProgress = {
    ...completedFetchProgress,
    status: 'started',
    message: '开始获取',
    rows: undefined,
  }
  const completed: SubagentProgress = {
    ...started,
    status: 'completed',
    message: '已获取 53 行数据',
    rows: 53,
  }
  const sandbox: SubagentProgress = {
    ...completedFetchProgress,
    step: 'sandbox',
    status: 'started',
    message: '开始清洗',
    rows: undefined,
  }

  expect(mergeSubagentProgress([], started)).toEqual([started])
  expect(mergeSubagentProgress([started], completed)).toEqual([completed])
  expect(mergeSubagentProgress([completed], sandbox)).toEqual([completed, sandbox])
})

it('进度面板安全展示白名单字段和数据摘要', () => {
  render(<SubagentProgressPanel items={[completedFetchProgress]} collapsed={false} />)

  expect(screen.getByText('数据子 Agent')).toBeInTheDocument()
  expect(screen.getByText(/akshare/)).toBeInTheDocument()
  expect(screen.getByText(/stock_zh_index_daily_tx/)).toBeInTheDocument()
  expect(screen.getAllByText(/53 行/).length).toBeGreaterThan(0)
  expect(screen.queryByText(/secret/)).not.toBeInTheDocument()
})

it('首个回答 token 后折叠进度，用户可展开且新对话会清空', async () => {
  const user = userEvent.setup()
  const stream = deferred<void>()
  let emitToken: ((delta: string) => void) | null = null
  api.streamAgentChat.mockImplementation(async (_message, _sessionId, handlers) => {
    emitToken = handlers.onToken
    handlers.onSubagentProgress(completedFetchProgress)
    handlers.onToken('回答')
    await stream.promise
  })

  render(
    <MemoryRouter>
      <AgentChatPage />
    </MemoryRouter>,
  )

  await user.type(await screen.findByPlaceholderText(/问投研助手/), '查数据')
  await user.click(screen.getByRole('button', { name: '发送' }))

  expect(await screen.findByRole('button', { name: /展开/ })).toBeInTheDocument()
  expect(screen.queryByText(/stock_zh_index_daily_tx/)).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /展开/ }))
  expect(screen.getByText(/stock_zh_index_daily_tx/)).toBeInTheDocument()

  act(() => emitToken?.('继续'))
  expect(screen.getByText(/stock_zh_index_daily_tx/)).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '新对话' }))
  expect(screen.queryByText(/stock_zh_index_daily_tx/)).not.toBeInTheDocument()

  stream.resolve()
})

it('失效旧流的 token、done、finally 不污染新会话状态', async () => {
  const user = userEvent.setup()
  const streams: CapturedStream[] = []
  api.createAgentSession
    .mockResolvedValueOnce({ session_id: 's-initial' })
    .mockResolvedValueOnce({ session_id: 's-new' })
  api.streamAgentChat.mockImplementation(async (_message, _sessionId, handlers, signal) => {
    const stream = deferred<void>()
    streams.push({ handlers, signal, resolve: stream.resolve })
    await stream.promise
  })

  render(
    <MemoryRouter>
      <AgentChatPage />
    </MemoryRouter>,
  )

  await user.type(await screen.findByPlaceholderText(/问投研助手/), '旧问题')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(streams).toHaveLength(1))
  expect(screen.getByText('旧问题')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '新对话' }))
  expect(streams[0].signal?.aborted).toBe(true)
  expect(screen.queryByText('旧问题')).not.toBeInTheDocument()
  expect(screen.getByPlaceholderText(/问投研助手/)).not.toBeDisabled()

  await user.type(screen.getByPlaceholderText(/问投研助手/), '新问题')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(streams).toHaveLength(2))
  expect(screen.getByText('新问题')).toBeInTheDocument()

  await act(async () => {
    streams[0].handlers.onToken?.('旧 token')
    streams[0].handlers.onDone?.({
      session_id: 'old-session',
      reply: '旧完成',
      tool_trace: [{ tool: 'old-tool', content: 'old-content' }],
    })
    streams[0].resolve()
    await Promise.resolve()
  })

  expect(screen.queryByText(/旧 token|旧完成|old-tool/)).not.toBeInTheDocument()
  expect(screen.getByText('新问题')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '生成中…' })).toBeDisabled()

  await act(async () => {
    streams[1].resolve()
    await Promise.resolve()
  })
})

it('新会话创建未完成时阻止使用旧 sessionId 发送', async () => {
  const user = userEvent.setup()
  const nextSession = deferred<{ session_id: string }>()
  api.createAgentSession
    .mockResolvedValueOnce({ session_id: 's-initial' })
    .mockReturnValueOnce(nextSession.promise)

  render(
    <MemoryRouter>
      <AgentChatPage />
    </MemoryRouter>,
  )

  const input = await screen.findByPlaceholderText(/问投研助手/)
  await user.type(input, '过渡期问题')
  await user.click(screen.getByRole('button', { name: '新对话' }))

  expect(screen.getByPlaceholderText(/问投研助手/)).toBeDisabled()
  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()

  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(api.streamAgentChat).not.toHaveBeenCalled()

  await act(async () => {
    nextSession.resolve({ session_id: 's-new' })
    await Promise.resolve()
  })

  await waitFor(() => expect(screen.getByPlaceholderText(/问投研助手/)).not.toBeDisabled())
  expect(screen.getByRole('button', { name: '发送' })).not.toBeDisabled()
})

it('新建会话失败时保留旧会话并可继续发送', async () => {
  const user = userEvent.setup()
  api.listAgentSessions.mockResolvedValue({
    sessions: [{ session_id: 's-old', title: '旧会话', message_count: 1 }],
  })
  api.fetchAgentMessages.mockResolvedValue({
    session_id: 's-old',
    messages: [{ role: 'assistant', content: '旧会话消息' }],
  })
  api.createAgentSession.mockRejectedValueOnce(new Error('secret create failure'))

  render(
    <MemoryRouter>
      <AgentChatPage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('旧会话消息')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '新对话' }))

  expect(await screen.findByText('会话操作失败，请稍后重试')).toBeInTheDocument()
  expect(screen.queryByText(/secret create failure/)).not.toBeInTheDocument()
  expect(screen.getByText('旧会话消息')).toBeInTheDocument()
  expect(screen.getByPlaceholderText(/问投研助手/)).not.toBeDisabled()

  await user.type(screen.getByPlaceholderText(/问投研助手/), '还能发送')
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(api.streamAgentChat).toHaveBeenCalledWith(
    '还能发送',
    's-old',
    expect.anything(),
    expect.any(AbortSignal),
  )
})

it('打开会话失败时保留旧会话并显示安全错误', async () => {
  const user = userEvent.setup()
  api.listAgentSessions.mockResolvedValue({
    sessions: [
      { session_id: 's-old', title: '旧会话', message_count: 1 },
      { session_id: 's-bad', title: '坏会话', message_count: 1 },
    ],
  })
  api.fetchAgentMessages.mockImplementation((id) => {
    if (id === 's-old') {
      return Promise.resolve({
        session_id: 's-old',
        messages: [{ role: 'assistant', content: '旧会话消息' }],
      })
    }
    return Promise.reject(new Error('secret load failure'))
  })

  render(
    <MemoryRouter>
      <AgentChatPage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('旧会话消息')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /坏会话/ }))

  expect(await screen.findByText('会话操作失败，请稍后重试')).toBeInTheDocument()
  expect(screen.queryByText(/secret load failure/)).not.toBeInTheDocument()
  expect(screen.getByText('旧会话消息')).toBeInTheDocument()

  await user.type(screen.getByPlaceholderText(/问投研助手/), '仍在旧会话')
  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(api.streamAgentChat).toHaveBeenCalledWith(
    '仍在旧会话',
    's-old',
    expect.anything(),
    expect.any(AbortSignal),
  )
})

it('删除当前会话后后继加载失败时保持无会话且禁止发送', async () => {
  const user = userEvent.setup()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  api.listAgentSessions
    .mockResolvedValueOnce({
      sessions: [
        { session_id: 's-current', title: '当前会话', message_count: 1 },
        { session_id: 's-next', title: '后继会话', message_count: 1 },
      ],
    })
    .mockResolvedValueOnce({
      sessions: [{ session_id: 's-next', title: '后继会话', message_count: 1 }],
    })
  api.fetchAgentMessages.mockImplementation((id) => {
    if (id === 's-current') {
      return Promise.resolve({
        session_id: 's-current',
        messages: [{ role: 'assistant', content: '当前会话消息' }],
      })
    }
    return Promise.reject(new Error('secret next failure'))
  })
  api.deleteAgentSession.mockResolvedValue({ ok: true })

  render(
    <MemoryRouter>
      <AgentChatPage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('当前会话消息')).toBeInTheDocument()
  await user.type(screen.getByPlaceholderText(/问投研助手/), '不能发送')
  await user.click(screen.getAllByTitle('删除')[0])

  expect(await screen.findByText('会话操作失败，请稍后重试')).toBeInTheDocument()
  expect(screen.queryByText('当前会话消息')).not.toBeInTheDocument()
  expect(screen.getByPlaceholderText(/问投研助手/)).toBeDisabled()
  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '新对话' })).not.toBeDisabled()

  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(api.streamAgentChat).not.toHaveBeenCalled()
})

it('新建会话失败时移除空的 streaming 助手尾泡', async () => {
  const user = userEvent.setup()
  const stream = deferred<void>()
  api.createAgentSession
    .mockResolvedValueOnce({ session_id: 's-initial' })
    .mockRejectedValueOnce(new Error('secret create failure'))
  api.streamAgentChat.mockImplementation(async () => {
    await stream.promise
  })

  render(
    <MemoryRouter>
      <AgentChatPage />
    </MemoryRouter>,
  )

  await user.type(await screen.findByPlaceholderText(/问投研助手/), '旧问题')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(api.streamAgentChat).toHaveBeenCalledTimes(1))
  expect(screen.getByText('…')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '新对话' }))

  expect(await screen.findByText('会话操作失败，请稍后重试')).toBeInTheDocument()
  expect(screen.queryByText('…')).not.toBeInTheDocument()
  expect(screen.getByText('旧问题')).toBeInTheDocument()

  await act(async () => {
    stream.resolve()
    await Promise.resolve()
  })
})

it('打开会话失败时结束已有内容的 streaming 助手尾泡', async () => {
  const user = userEvent.setup()
  const streams: CapturedStream[] = []
  api.listAgentSessions.mockResolvedValue({
    sessions: [
      { session_id: 's-old', title: '旧会话', message_count: 0 },
      { session_id: 's-bad', title: '坏会话', message_count: 1 },
    ],
  })
  api.fetchAgentMessages.mockImplementation((id) => {
    if (id === 's-old') {
      return Promise.resolve({ session_id: 's-old', messages: [] })
    }
    return Promise.reject(new Error('secret load failure'))
  })
  api.streamAgentChat.mockImplementation(async (_message, _sessionId, handlers, signal) => {
    const stream = deferred<void>()
    streams.push({ handlers, signal, resolve: stream.resolve })
    await stream.promise
  })

  render(
    <MemoryRouter>
      <AgentChatPage />
    </MemoryRouter>,
  )

  await user.type(await screen.findByPlaceholderText(/问投研助手/), '旧问题')
  await user.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(streams).toHaveLength(1))
  act(() => streams[0].handlers.onToken?.('半截回复'))
  expect(screen.getByText('半截回复')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: /坏会话/ }))

  expect(await screen.findByText('会话操作失败，请稍后重试')).toBeInTheDocument()
  expect(screen.getByText('半截回复')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '复制' })).toBeInTheDocument()

  await act(async () => {
    streams[0].resolve()
    await Promise.resolve()
  })
})
