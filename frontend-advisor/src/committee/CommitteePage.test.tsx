import '@testing-library/jest-dom/vitest'
import { useState, type ReactNode } from 'react'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CommitteePage from './CommitteePage'
import CommitteeChat from './components/CommitteeChat'
import CommitteeDetail from './components/CommitteeDetail'
import CommitteeDetailDrawer from './components/CommitteeDetailDrawer'
import EvidenceBadge from './components/EvidenceBadge'

const api = vi.hoisted(() => ({
  listCommitteeRuns: vi.fn(),
  getCommitteeRun: vi.fn(),
  createCommitteeRun: vi.fn(),
  cancelCommitteeRun: vi.fn(),
  retryCommitteeRun: vi.fn(),
  deleteCommitteeRun: vi.fn(),
  getCommitteeOrderPreview: vi.fn(),
  bindCommitteeOrderPreview: vi.fn(),
  approveCommitteeRun: vi.fn(),
  streamCommitteeEvents: vi.fn(),
}))

vi.mock('./committeeApi', () => api)
vi.mock('react-virtuoso', () => ({
  Virtuoso: ({
    data,
    itemContent,
  }: {
    data: unknown[]
    itemContent: (index: number, item: unknown) => ReactNode
  }) => (
    <div data-testid="committee-virtuoso">
      {data.map((item, index) => (
        <div key={index}>{itemContent(index, item)}</div>
      ))}
    </div>
  ),
}))

const completedRun = {
  run_id: 'run-1',
  user_id: 'u1',
  status: 'completed',
  version: 3,
  strategy_version: 'strategy-v2',
  horizon: 'next_day',
  universe: ['510300'],
  as_of: '2026-07-22T01:00:00Z',
  created_at: '2026-07-22T01:00:00Z',
  updated_at: '2026-07-22T01:02:00Z',
  started_at: '2026-07-22T01:00:10Z',
  completed_at: '2026-07-22T01:02:00Z',
  attempt: 1,
  parent_run_id: null,
}

const runningRun = {
  ...completedRun,
  run_id: 'run-live',
  status: 'running',
  version: 1,
  completed_at: null,
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset())
  sessionStorage.clear()
  api.listCommitteeRuns.mockResolvedValue({ runs: [completedRun] })
  api.getCommitteeRun.mockResolvedValue({
    run: completedRun,
    events: [
      {
        event_id: '1-0',
        sequence: 1,
        event_type: 'node_completed',
        payload: { node: 'fundamental' },
      },
    ],
    artifacts: [],
  })
  api.deleteCommitteeRun.mockResolvedValue({
    run_id: 'run-1',
    deleted: true,
  })
  api.streamCommitteeEvents.mockResolvedValue(undefined)
})

describe('evidence badges', () => {
  it.each([
    ['fact', '事实'],
    ['judgement', '模型判断'],
    ['hard_rule', '硬规则'],
    ['degraded', '降级'],
  ] as const)('把 %s 显示为独立语义 badge', (kind, text) => {
    render(<EvidenceBadge kind={kind} />)
    expect(screen.getByText(text)).toHaveAttribute('data-kind', kind)
  })
})

describe('committee chat cards', () => {
  it('数据卡仅显示白名单摘要并过滤敏感字段与长文本', () => {
    const longRationale = `安全摘要${'很长'.repeat(120)}`
    render(
      <CommitteeChat
        messages={[{
          message_id: 'm-card',
          role: 'trader',
          node: 'trader',
          content: '交易建议',
          status: 'completed',
          sequence: 1,
          generation: 1,
          card_kind: 'trade_proposal',
          card_ref: { attempt: 1, node: 'trader', kind: 'trade_proposal' },
          nextOffset: 4,
        }]}
        artifacts={[{
          artifact_id: 'card',
          kind: 'trade_proposal',
          attempt: 1,
          node: 'trader',
          payload: {
            symbol: '510300',
            direction: 'buy',
            rationale: longRationale,
            prompt: '不得显示的提示词',
            secret: '不得显示的密钥',
            token: '不得显示的令牌',
            authorization: 'Bearer private',
            arbitrary_note: '任意字段不得显示',
          },
        }]}
        loading={false}
        streamState="历史完成态"
      />,
    )

    expect(screen.getByText('510300')).toBeInTheDocument()
    expect(screen.getByText('buy')).toBeInTheDocument()
    expect(screen.getByText(/^安全摘要.+…$/)).toHaveTextContent(/^.{1,201}…$/)
    expect(screen.queryByText(longRationale)).not.toBeInTheDocument()
    for (const secret of [
      '不得显示的提示词',
      '不得显示的密钥',
      '不得显示的令牌',
      'Bearer private',
      '任意字段不得显示',
    ]) {
      expect(screen.queryByText(secret)).not.toBeInTheDocument()
    }
  })
})

describe('committee detail drawer', () => {
  it('打开后聚焦关闭按钮，Esc 关闭并还原触发器焦点', async () => {
    function DrawerHarness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>查看详情</button>
          <CommitteeDetailDrawer open={open} onClose={() => setOpen(false)}>
            <button type="button">抽屉操作</button>
          </CommitteeDetailDrawer>
        </>
      )
    }
    render(<DrawerHarness />)
    const trigger = screen.getByRole('button', { name: '查看详情' })
    await userEvent.click(trigger)
    expect(screen.getByRole('button', { name: '关闭会议详情' })).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: '会议详情' })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('点击遮罩关闭并还原触发器焦点', async () => {
    function DrawerHarness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>查看详情</button>
          <CommitteeDetailDrawer open={open} onClose={() => setOpen(false)}>
            <button type="button">抽屉操作</button>
          </CommitteeDetailDrawer>
        </>
      )
    }
    render(<DrawerHarness />)
    const trigger = screen.getByRole('button', { name: '查看详情' })
    await userEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: '会议详情' })
    await userEvent.click(dialog.parentElement as HTMLElement)
    expect(screen.queryByRole('dialog', { name: '会议详情' })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})

describe('CommitteePage', () => {
  it('主区域展示群聊消息且结构化报告进入详情抽屉', async () => {
    api.getCommitteeRun.mockResolvedValue({
      run: completedRun,
      events: [{
        event_id: '1-0',
        sequence: 1,
        event_type: 'message_completed',
        payload: {
          message_id: 'm-tech',
          role: 'technical',
          node: 'technical',
          content: '技术面偏多',
          sequence: 1,
          card_ref: { attempt: 1, node: 'trader', kind: 'trade_proposal' },
        },
      }],
      artifacts: [{
        artifact_id: 'a1',
        kind: 'trade_proposal',
        attempt: 1,
        node: 'trader',
        payload: { symbol: '510300', direction: 'buy' },
      }],
    })

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )

    expect(await screen.findByText('技术面偏多')).toBeInTheDocument()
    expect(screen.getAllByText('技术分析师').length).toBeGreaterThan(0)
    expect(screen.queryByRole('dialog', { name: '会议详情' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '查看详情' }))
    const detail = await screen.findByRole('dialog', { name: '会议详情' })
    expect(within(detail).getByText(/510300/)).toBeInTheDocument()
  })

  it('实时 message_delta 按 token 追加，completed 覆盖临时文本', async () => {
    let streamHandlers: {
      onEvent: (event: {
        id?: string
        event: string
        data: Record<string, unknown>
      }) => void
    } | undefined
    api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
    api.getCommitteeRun.mockResolvedValue({
      run: runningRun,
      events: [],
      artifacts: [],
    })
    api.streamCommitteeEvents.mockImplementation(
      async (_runId: string, handlers: typeof streamHandlers) => {
        streamHandlers = handlers
      },
    )
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(streamHandlers).toBeDefined())

    act(() => {
      streamHandlers?.onEvent({
        id: '1-0',
        event: 'message_started',
        data: {
          message_id: 'm-live',
          role: 'chair',
          node: 'chair',
          generation: 1,
        },
      })
      streamHandlers?.onEvent({
        id: '2-0',
        event: 'message_delta',
        data: {
          message_id: 'm-live',
          generation: 1,
          offset: 0,
          delta: '临时',
        },
      })
      streamHandlers?.onEvent({
        id: '3-0',
        event: 'message_delta',
        data: {
          message_id: 'm-live',
          generation: 1,
          offset: 2,
          delta: '文本',
        },
      })
    })
    expect(await screen.findByText('临时文本')).toBeInTheDocument()

    act(() => {
      streamHandlers?.onEvent({
        id: '4-0',
        event: 'message_completed',
        data: {
          message_id: 'm-live',
          role: 'chair',
          node: 'chair',
          generation: 1,
          content: '最终完整文本',
          status: 'completed',
        },
      })
    })
    expect(await screen.findByText('最终完整文本')).toBeInTheDocument()
    expect(screen.queryByText('临时文本')).not.toBeInTheDocument()
  })

  it('当前连接中新到达且无 delta 的 completed 会本地逐字展示并在卸载时清理定时器', async () => {
    let streamHandlers: {
      onEvent: (event: {
        id?: string
        event: string
        data: Record<string, unknown>
      }) => void
    } | undefined
    api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
    api.getCommitteeRun.mockResolvedValue({
      run: runningRun,
      events: [],
      artifacts: [],
    })
    api.streamCommitteeEvents.mockImplementation(
      async (_runId: string, handlers: typeof streamHandlers) => {
        streamHandlers = handlers
      },
    )
    const view = render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(streamHandlers).toBeDefined())
    vi.useFakeTimers()
    try {
      act(() => {
        streamHandlers?.onEvent({
          id: '1-0',
          event: 'message_completed',
          data: {
            message_id: 'm-catchup',
            role: 'chair',
            node: 'chair',
            generation: 1,
            content: '补帧文本',
            status: 'completed',
          },
        })
      })

      expect(screen.queryByText('补帧文本')).not.toBeInTheDocument()
      act(() => vi.advanceTimersByTime(24))
      expect(screen.getByText('补')).toBeInTheDocument()
      act(() => vi.advanceTimersByTime(24 * 3))
      expect(screen.getByText('补帧文本')).toBeInTheDocument()

      act(() => {
        streamHandlers?.onEvent({
          id: '2-0',
          event: 'message_completed',
          data: {
            message_id: 'm-cleanup',
            role: 'technical',
            node: 'technical',
            generation: 1,
            content: '卸载清理',
            status: 'completed',
          },
        })
      })
      expect(vi.getTimerCount()).toBeGreaterThan(0)
      view.unmount()
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it.each(['failed', 'cancelled'] as const)(
    '实时 %s 终态将仍在输出的消息标记为失败',
    async (terminalEvent) => {
      let streamHandlers: {
        onEvent: (event: {
          id?: string
          event: string
          data: Record<string, unknown>
        }) => void
      } | undefined
      api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
      api.getCommitteeRun.mockResolvedValue({
        run: runningRun,
        events: [],
        artifacts: [],
      })
      api.streamCommitteeEvents.mockImplementation(
        async (_runId: string, handlers: typeof streamHandlers) => {
          streamHandlers = handlers
        },
      )
      render(
        <MemoryRouter>
          <CommitteePage />
        </MemoryRouter>,
      )
      await waitFor(() => expect(streamHandlers).toBeDefined())

      act(() => {
        streamHandlers?.onEvent({
          id: '1-0',
          event: 'message_started',
          data: {
            message_id: 'm-interrupted',
            role: 'technical',
            node: 'technical',
            generation: 1,
          },
        })
        streamHandlers?.onEvent({
          id: '2-0',
          event: terminalEvent,
          data: {},
        })
      })

      const bubble = screen.getAllByText('技术分析师')
        .find((element) => element.tagName === 'STRONG')
        ?.closest('article')
      expect(bubble).toBeTruthy()
      expect(within(bubble as HTMLElement).getByText('失败')).toBeInTheDocument()
      expect(within(bubble as HTMLElement).queryByText('输出中')).not.toBeInTheDocument()
    },
  )

  it('旧会议无 message_completed 时仍能从 artifacts 渲染群聊', async () => {
    api.getCommitteeRun.mockResolvedValue({
      run: completedRun,
      events: [{
        event_id: '1-0',
        sequence: 1,
        event_type: 'node_completed',
        payload: { node: 'fundamental' },
      }],
      artifacts: [
        {
          artifact_id: 'reports',
          kind: 'analyst_reports',
          payload: [{
            role: 'fundamental',
            thesis: '旧会议基本面结论',
            confidence: 0.8,
          }],
        },
        {
          artifact_id: 'decision',
          kind: 'final_decision',
          payload: {
            symbol: '510300',
            action: 'buy',
            target_weight: 0.2,
            rationale: '旧会议主席结论',
          },
        },
      ],
    })

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )

    expect((await screen.findAllByText('旧会议基本面结论')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/旧会议主席结论/).length).toBeGreaterThan(0)
  })

  it('仅终态会议显示删除记录且取消确认不发请求', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )

    const button = await screen.findByRole('button', {
      name: '删除记录',
    })
    await userEvent.click(button)

    expect(confirm).toHaveBeenCalledWith(
      '只会从历史列表隐藏此会议，不会撤销审批或订单。确认删除？',
    )
    expect(api.deleteCommitteeRun).not.toHaveBeenCalled()
  })

  it('进行中的会议不显示删除记录', async () => {
    api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
    api.getCommitteeRun.mockResolvedValue({
      run: runningRun,
      events: [],
      artifacts: [],
    })
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: '取消会议' })
    expect(
      screen.queryByRole('button', { name: '删除记录' }),
    ).not.toBeInTheDocument()
  })

  it.each(['failed', 'cancelled'] as const)(
    '%s 终态会议显示删除记录',
    async (status) => {
      const terminalRun = { ...completedRun, status }
      api.listCommitteeRuns.mockResolvedValue({ runs: [terminalRun] })
      api.getCommitteeRun.mockResolvedValue({
        run: terminalRun,
        events: [],
        artifacts: [],
      })
      render(
        <MemoryRouter>
          <CommitteePage />
        </MemoryRouter>,
      )
      expect(
        await screen.findByRole('button', { name: '删除记录' }),
      ).toBeInTheDocument()
    },
  )

  it('双击删除只发出一次请求', async () => {
    const pendingDelete = deferred<{
      run_id: string
      deleted: boolean
    }>()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.deleteCommitteeRun.mockImplementation(() => pendingDelete.promise)
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )

    await userEvent.dblClick(
      await screen.findByRole('button', { name: '删除记录' }),
    )

    expect(api.deleteCommitteeRun).toHaveBeenCalledTimes(1)
  })

  it('确认删除后刷新历史并自动选择下一条', async () => {
    const nextRun = { ...completedRun, run_id: 'run-2' }
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.listCommitteeRuns
      .mockResolvedValueOnce({ runs: [completedRun, nextRun] })
      .mockResolvedValueOnce({ runs: [nextRun] })
    api.getCommitteeRun
      .mockResolvedValueOnce({
        run: completedRun,
        events: [],
        artifacts: [],
      })
      .mockResolvedValueOnce({
        run: nextRun,
        events: [],
        artifacts: [],
      })

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(
      await screen.findByRole('button', { name: '删除记录' }),
    )

    expect(api.deleteCommitteeRun).toHaveBeenCalledWith(
      'run-1',
      expect.any(AbortSignal),
    )
    await waitFor(() =>
      expect(api.getCommitteeRun).toHaveBeenLastCalledWith(
        'run-2',
        expect.any(AbortSignal),
      ),
    )
    expect(
      await screen.findByText('run-2', {
        selector: '.committee-chat-header h2',
      }),
    ).toBeInTheDocument()
  })

  it('删除请求在途时禁止切换历史且不中止删除信号', async () => {
    const nextRun = { ...completedRun, run_id: 'run-2' }
    const deleteRequest = deferred<{ run_id: string; deleted: boolean }>()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.listCommitteeRuns
      .mockResolvedValueOnce({ runs: [completedRun, nextRun] })
      .mockResolvedValueOnce({ runs: [nextRun] })
    api.getCommitteeRun.mockImplementation((runId: string) => Promise.resolve({
      run: runId === 'run-2' ? nextRun : completedRun,
      events: [],
      artifacts: [],
    }))
    api.deleteCommitteeRun.mockImplementationOnce(
      (_runId: string, _signal?: AbortSignal) => deleteRequest.promise,
    )

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(
      await screen.findByRole('button', { name: '删除记录' }),
    )
    await waitFor(() => expect(api.deleteCommitteeRun).toHaveBeenCalledTimes(1))
    const deleteSignal = api.deleteCommitteeRun.mock.calls[0][1] as AbortSignal

    expect(screen.getByRole('button', { name: /run-2/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: '发起会议' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: /run-2/ }))
    expect(deleteSignal.aborted).toBe(false)
    expect(
      screen.getByText('run-1', { selector: '.committee-chat-header h2' }),
    ).toBeInTheDocument()

    await act(async () => {
      deleteRequest.resolve({ run_id: 'run-1', deleted: true })
      await Promise.resolve()
    })
    expect(
      await screen.findByText('run-2', { selector: '.committee-chat-header h2' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /run-1/ }),
    ).not.toBeInTheDocument()
  })

  it('删除刷新期间用户切换后旧流程不夺回选择并中止删除信号', async () => {
    const nextRun = { ...completedRun, run_id: 'run-2' }
    const refreshedFirstRun = { ...completedRun, run_id: 'run-3' }
    const refresh = deferred<{ runs: (typeof completedRun)[] }>()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.listCommitteeRuns
      .mockResolvedValueOnce({ runs: [completedRun, nextRun] })
      .mockImplementationOnce(() => refresh.promise)
    api.getCommitteeRun.mockImplementation((runId: string) => Promise.resolve({
      run: runId === 'run-1'
        ? completedRun
        : runId === 'run-2'
          ? nextRun
          : refreshedFirstRun,
      events: [],
      artifacts: [],
    }))

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(
      await screen.findByRole('button', { name: '删除记录' }),
    )
    await waitFor(() => expect(api.listCommitteeRuns).toHaveBeenCalledTimes(2))
    const deleteSignal = api.deleteCommitteeRun.mock.calls[0][1] as AbortSignal

    await userEvent.click(screen.getByRole('button', { name: /run-2/ }))
    expect(deleteSignal.aborted).toBe(true)
    expect(
      await screen.findByText('run-2', { selector: '.committee-chat-header h2' }),
    ).toBeInTheDocument()

    refresh.resolve({ runs: [refreshedFirstRun, nextRun] })
    await screen.findByRole('button', { name: /run-3/ })
    await waitFor(() =>
      expect(api.getCommitteeRun).toHaveBeenLastCalledWith(
        'run-2',
        expect.any(AbortSignal),
      ),
    )
    expect(
      screen.getByText('run-2', { selector: '.committee-chat-header h2' }),
    ).toBeInTheDocument()
  })

  it('删除前旧列表请求乱序返回时不会复活已删除记录', async () => {
    const deletedRun = { ...completedRun, run_id: 'run-live' }
    const nextRun = { ...completedRun, run_id: 'run-2' }
    const staleOtherRun = { ...completedRun, run_id: 'run-3' }
    const staleRefresh = deferred<{ runs: (typeof completedRun)[] }>()
    let streamHandlers: {
      onEvent: (event: {
        id: string
        event: string
        data: Record<string, unknown>
      }) => void
    } | undefined
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.listCommitteeRuns
      .mockResolvedValueOnce({ runs: [runningRun, nextRun] })
      .mockImplementationOnce(() => staleRefresh.promise)
      .mockResolvedValueOnce({ runs: [nextRun] })
    api.getCommitteeRun
      .mockResolvedValueOnce({
        run: runningRun,
        events: [],
        artifacts: [],
      })
      .mockImplementation((runId: string) => Promise.resolve({
        run: runId === 'run-2' ? nextRun : deletedRun,
        events: [],
        artifacts: [],
      }))
    api.streamCommitteeEvents.mockImplementation(
      async (_runId: string, handlers: typeof streamHandlers) => {
        streamHandlers = handlers
      },
    )

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(streamHandlers).toBeDefined())
    await act(async () => {
      streamHandlers?.onEvent({
        id: '2-0',
        event: 'completed',
        data: {},
      })
    })
    await waitFor(() => expect(api.listCommitteeRuns).toHaveBeenCalledTimes(2))

    await userEvent.click(
      await screen.findByRole('button', { name: '删除记录' }),
    )
    await waitFor(() => expect(api.listCommitteeRuns).toHaveBeenCalledTimes(3))
    expect(
      await screen.findByText('run-2', { selector: '.committee-chat-header h2' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /run-live/ }),
    ).not.toBeInTheDocument()

    await act(async () => {
      staleRefresh.resolve({ runs: [deletedRun, nextRun, staleOtherRun] })
      await Promise.resolve()
    })
    await screen.findByRole('button', { name: /run-3/ })
    expect(
      screen.queryByRole('button', { name: /run-live/ }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText('run-2', { selector: '.committee-chat-header h2' }),
    ).toBeInTheDocument()
  })

  it('删除成功但刷新失败时移除本地记录且不自动选择', async () => {
    const nextRun = { ...completedRun, run_id: 'run-2' }
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.listCommitteeRuns
      .mockResolvedValueOnce({ runs: [completedRun, nextRun] })
      .mockRejectedValueOnce(new Error('刷新失败'))
    api.getCommitteeRun.mockResolvedValue({
      run: completedRun,
      events: [],
      artifacts: [],
    })

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(
      await screen.findByRole('button', { name: '删除记录' }),
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('刷新失败')
    expect(
      screen.queryByRole('button', { name: /run-1/ }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run-2/ })).toBeInTheDocument()
    expect(await screen.findByText('选择或发起一次会议')).toBeInTheDocument()
    expect(api.getCommitteeRun).toHaveBeenCalledTimes(1)
  })

  it('删除最后一条后显示空状态', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.listCommitteeRuns
      .mockResolvedValueOnce({ runs: [completedRun] })
      .mockResolvedValueOnce({ runs: [] })
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(
      await screen.findByRole('button', { name: '删除记录' }),
    )
    expect(
      await screen.findByText('选择或发起一次会议'),
    ).toBeInTheDocument()
  })

  it('删除失败时保留当前详情并显示错误', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.deleteCommitteeRun.mockRejectedValue(new Error('删除失败'))
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(
      await screen.findByRole('button', { name: '删除记录' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('删除失败')
    expect(
      screen.getByText('run-1', { selector: '.committee-chat-header h2' }),
    ).toBeInTheDocument()
  })

  it('从历史列表选择会议并加载 Mongo 详情事件', async () => {
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('button', { name: /run-1/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /run-1/ }))
    await waitFor(() =>
      expect(api.getCommitteeRun).toHaveBeenCalledWith(
        'run-1',
        expect.any(AbortSignal),
      ),
    )
    await userEvent.click(screen.getByRole('button', { name: '查看详情' }))
    expect((await screen.findAllByText('基本面分析')).length).toBeGreaterThan(0)
  })

  it('校验 symbol 并防止重复提交', async () => {
    api.createCommitteeRun.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ run_id: 'run-2', status: 'queued' }), 20)),
    )
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: '发起会议' }))
    const dialog = await screen.findByRole('dialog', { name: '发起投委会' })
    const symbolInput = within(dialog).getByLabelText('证券代码')
    await userEvent.type(symbolInput, '123')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认发起' }))
    expect(screen.getByText('请输入 6 位证券代码')).toBeInTheDocument()
    await userEvent.clear(symbolInput)
    await userEvent.type(symbolInput, '510300')
    const submit = within(dialog).getByRole('button', { name: '确认发起' })
    await userEvent.dblClick(submit)
    expect(api.createCommitteeRun).toHaveBeenCalledTimes(1)
  })

  it('创建幂等键在同一弹窗重试复用，关闭重开后更新', async () => {
    api.createCommitteeRun.mockRejectedValue(new Error('网络错误'))
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: '发起会议' }))
    let dialog = await screen.findByRole('dialog', { name: '发起投委会' })
    await userEvent.type(within(dialog).getByLabelText('证券代码'), '510300')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认发起' }))
    await userEvent.click(within(dialog).getByRole('button', { name: '确认发起' }))
    const firstKey = api.createCommitteeRun.mock.calls[0][1]
    expect(api.createCommitteeRun.mock.calls[1][1]).toBe(firstKey)
    await userEvent.click(within(dialog).getByRole('button', { name: '关闭发起投委会' }))
    await userEvent.click(screen.getByRole('button', { name: '发起会议' }))
    dialog = await screen.findByRole('dialog', { name: '发起投委会' })
    await userEvent.type(within(dialog).getByLabelText('证券代码'), '510300')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认发起' }))
    expect(api.createCommitteeRun.mock.calls[2][1]).not.toBe(firstKey)
  })

  it('切换会议立即清空旧详情并忽略旧请求完成', async () => {
    const oldDetail = deferred<unknown>()
    const newDetail = deferred<unknown>()
    api.listCommitteeRuns.mockResolvedValue({
      runs: [completedRun, { ...completedRun, run_id: 'run-2' }],
    })
    let oldSignal: AbortSignal | undefined
    api.getCommitteeRun.mockImplementation((runId: string, signal: AbortSignal) => {
      if (runId === 'run-1') oldSignal = signal
      return runId === 'run-1' ? oldDetail.promise : newDetail.promise
    })
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: /run-2/ })
    await userEvent.click(screen.getByRole('button', { name: /run-2/ }))
    expect(oldSignal?.aborted).toBe(true)
    expect(screen.queryByRole('button', { name: '审批订单' })).not.toBeInTheDocument()
    expect(screen.getByText('载入会议详情与历史事件…')).toBeInTheDocument()
    oldDetail.resolve({
      run: completedRun,
      events: [],
      artifacts: [],
    })
    await Promise.resolve()
    expect(screen.queryByText('run-1', { selector: '.committee-chat-header h2' })).not.toBeInTheDocument()
    newDetail.resolve({
      run: { ...completedRun, run_id: 'run-2' },
      events: [],
      artifacts: [],
    })
    expect(await screen.findByRole('button', { name: '审批订单' })).toBeInTheDocument()
  })

  it('实时 artifact 事件触发节流详情刷新并合并工件', async () => {
    let streamHandlers: { onEvent: (event: { id: string; event: string; data: Record<string, unknown> }) => void } | undefined
    api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
    api.getCommitteeRun
      .mockResolvedValueOnce({ run: runningRun, events: [], artifacts: [] })
      .mockResolvedValueOnce({
        run: { ...runningRun, version: 2 },
        events: [],
        artifacts: [{
          artifact_id: 'report-1',
          kind: 'analyst_reports',
          payload: [{ role: 'fundamental', thesis: '实时基本面结论', confidence: 0.8 }],
        }],
      })
    api.streamCommitteeEvents.mockImplementation(
      async (_runId: string, handlers: typeof streamHandlers, options: { signal: AbortSignal }) => {
        streamHandlers = handlers
        await new Promise<void>((resolve) => options.signal.addEventListener('abort', () => resolve()))
      },
    )
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(streamHandlers).toBeDefined())
    streamHandlers?.onEvent({ id: '2-0', event: 'artifact', data: { kind: 'analyst_reports' } })
    expect((await screen.findAllByText('实时基本面结论')).length).toBeGreaterThan(0)
    expect(api.getCommitteeRun).toHaveBeenCalledTimes(2)
  })

  it('SSE初始游标显式选择最大sequence事件', async () => {
    let streamOptions: { lastEventId?: string } | undefined
    api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
    api.getCommitteeRun.mockResolvedValue({
      run: runningRun,
      artifacts: [],
      events: [
        { event_id: '12-0', sequence: 12, event_type: 'running', payload: {} },
        { event_id: '3-0', sequence: 3, event_type: 'queued', payload: {} },
      ],
    })
    api.streamCommitteeEvents.mockImplementation(
      async (_runId: string, _handlers: unknown, options: { lastEventId?: string }) => {
        streamOptions = options
      },
    )
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(streamOptions).toBeDefined())
    expect(streamOptions?.lastEventId).toBe('12-0')
  })

  it('取消使用响应并显式刷新当前详情', async () => {
    api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
    api.getCommitteeRun
      .mockResolvedValueOnce({ run: runningRun, events: [], artifacts: [] })
      .mockResolvedValueOnce({
        run: { ...runningRun, status: 'cancelled', version: 2 },
        events: [],
        artifacts: [],
      })
    api.cancelCommitteeRun.mockResolvedValue({ run_id: 'run-live', status: 'cancelled' })
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '取消会议' }))
    await waitFor(() => expect(api.getCommitteeRun).toHaveBeenCalledTimes(2))
    expect(screen.getByText(/cancelled/)).toBeInTheDocument()
  })

  it('审批必须 preview、二次确认且处理中禁止重复', async () => {
    const preview = {
      proposal_hash: 'p'.repeat(64),
      decision_hash: 'd'.repeat(64),
      account_version: 8,
      orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
    }
    api.getCommitteeOrderPreview.mockResolvedValue({ preview })
    api.bindCommitteeOrderPreview.mockResolvedValue({ preview_id: 'pv1', preview })
    api.approveCommitteeRun.mockImplementation(() => new Promise(() => undefined))

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: /run-1/ })
    await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
    expect(api.getCommitteeOrderPreview).toHaveBeenCalledWith(
      'run-1',
      expect.any(AbortSignal),
    )
    const dialog = await screen.findByRole('dialog', { name: '审批订单' })
    expect(within(dialog).getByText('账户版本 8')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
    const confirm = within(dialog).getByRole('button', { name: '确认提交模拟盘' })
    await userEvent.dblClick(confirm)
    expect(api.bindCommitteeOrderPreview).toHaveBeenCalledTimes(1)
    expect(api.approveCommitteeRun).toHaveBeenCalledTimes(1)
    expect(confirm).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: '关闭审批订单' })).toBeDisabled()
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('dialog', { name: '审批订单' })).toBeInTheDocument()
  })

  it('409 审批失败要求重新 preview', async () => {
    const preview = {
      proposal_hash: 'p'.repeat(64),
      decision_hash: 'd'.repeat(64),
      account_version: 8,
      orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
    }
    api.getCommitteeOrderPreview.mockResolvedValue({ preview })
    api.bindCommitteeOrderPreview.mockResolvedValue({ preview_id: 'pv1', preview })
    api.approveCommitteeRun.mockRejectedValue(
      Object.assign(new Error('账户版本已变化，请重新预览'), { status: 409 }),
    )
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: /run-1/ })
    await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
    const dialog = await screen.findByRole('dialog', { name: '审批订单' })
    await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
    await userEvent.click(within(dialog).getByRole('button', { name: '确认提交模拟盘' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('重新预览')
    expect(within(dialog).getByRole('button', { name: '重新预览' })).toBeInTheDocument()
    expect(within(dialog).queryByText('账户版本 8')).not.toBeInTheDocument()
    const invalidKey = api.approveCommitteeRun.mock.calls[0][2]
    await userEvent.click(within(dialog).getByRole('button', { name: '重新预览' }))
    await within(dialog).findByText('账户版本 8')
    await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
    await userEvent.click(
      await within(dialog).findByRole('button', { name: '确认提交模拟盘' }),
    )
    await waitFor(() => expect(api.approveCommitteeRun).toHaveBeenCalledTimes(2))
    expect(api.approveCommitteeRun.mock.calls[1][2]).not.toBe(invalidKey)
  })

  it.each([
    ['网络断开', new TypeError('Failed to fetch')],
    ['HTTP超时', Object.assign(new Error('Request Timeout'), { status: 408 })],
  ])('%s时复用同一绑定计划和幂等键', async (_label, cause) => {
    const preview = {
      proposal_hash: 'p'.repeat(64),
      decision_hash: 'd'.repeat(64),
      account_version: 8,
      orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
    }
    api.getCommitteeOrderPreview.mockResolvedValue({ preview })
    api.bindCommitteeOrderPreview.mockResolvedValue({ preview_id: 'pv-network', preview })
    api.approveCommitteeRun.mockRejectedValue(cause)
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
    const dialog = await screen.findByRole('dialog', { name: '审批订单' })
    await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
    const confirm = await within(dialog).findByRole('button', { name: '确认提交模拟盘' })
    await userEvent.click(confirm)
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      '状态未知/稍后用同一key重试',
    )
    await userEvent.click(confirm)
    expect(api.bindCommitteeOrderPreview).toHaveBeenCalledTimes(1)
    expect(api.approveCommitteeRun.mock.calls[1][2]).toBe(
      api.approveCommitteeRun.mock.calls[0][2],
    )
  })

  it('409正在执行保留计划并提示用同一key重试', async () => {
    const preview = {
      proposal_hash: 'p'.repeat(64),
      decision_hash: 'd'.repeat(64),
      account_version: 8,
      orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
    }
    api.getCommitteeOrderPreview.mockResolvedValue({ preview })
    api.bindCommitteeOrderPreview.mockResolvedValue({ preview_id: 'pv-running', preview })
    api.approveCommitteeRun.mockRejectedValue(
      Object.assign(new Error('审批正在执行，请使用同一幂等键重试'), { status: 409 }),
    )
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
    const dialog = await screen.findByRole('dialog', { name: '审批订单' })
    await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
    await userEvent.click(
      await within(dialog).findByRole('button', { name: '确认提交模拟盘' }),
    )
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      '状态未知/稍后用同一key重试',
    )
    expect(within(dialog).getByText('账户版本 8')).toBeInTheDocument()
    const key = api.approveCommitteeRun.mock.calls[0][2]
    await userEvent.click(within(dialog).getByRole('button', { name: /同一key重试/ }))
    expect(api.approveCommitteeRun.mock.calls[1][2]).toBe(key)
  })

  it('审批401清理pending并允许关闭', async () => {
    const preview = {
      proposal_hash: 'p'.repeat(64),
      decision_hash: 'd'.repeat(64),
      account_version: 8,
      orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
    }
    api.getCommitteeOrderPreview.mockResolvedValue({ preview })
    api.bindCommitteeOrderPreview.mockResolvedValue({ preview_id: 'pv-401', preview })
    api.approveCommitteeRun.mockRejectedValue(
      Object.assign(new Error('请先登录'), { status: 401 }),
    )
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
    const dialog = await screen.findByRole('dialog', { name: '审批订单' })
    await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
    await userEvent.click(
      await within(dialog).findByRole('button', { name: '确认提交模拟盘' }),
    )
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('登录已失效')
    expect(within(dialog).getByRole('button', { name: '关闭审批订单' })).toBeEnabled()
    expect(sessionStorage.getItem('committee:pending-approval:run-1')).toBeNull()
    expect(within(dialog).queryByText('账户版本 8')).not.toBeInTheDocument()
  })

  it.each([403, 404, 422])(
    '审批%s确定性失败保留审计提示但不锁unknown',
    async (status) => {
      const preview = {
        proposal_hash: 'p'.repeat(64),
        decision_hash: 'd'.repeat(64),
        account_version: 8,
        orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
      }
      api.getCommitteeOrderPreview.mockResolvedValue({ preview })
      api.bindCommitteeOrderPreview.mockResolvedValue({
        preview_id: `pv-${status}`,
        preview,
      })
      api.approveCommitteeRun.mockRejectedValue(
        Object.assign(new Error(`HTTP ${status}`), { status }),
      )
      render(
        <MemoryRouter>
          <CommitteePage />
        </MemoryRouter>,
      )
      await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
      const dialog = await screen.findByRole('dialog', { name: '审批订单' })
      await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
      await userEvent.click(
        await within(dialog).findByRole('button', { name: '确认提交模拟盘' }),
      )
      expect(await within(dialog).findByRole('alert')).toHaveTextContent(
        `提交失败（HTTP ${status}）`,
      )
      expect(within(dialog).getByRole('alert')).toHaveTextContent('已保留审批审计信息')
      expect(within(dialog).getByRole('button', { name: '关闭审批订单' })).toBeEnabled()
      expect(within(dialog).getByText('账户版本 8')).toBeInTheDocument()
      const pending = JSON.parse(
        sessionStorage.getItem('committee:pending-approval:run-1') || '{}',
      ) as { outcome_unknown?: boolean }
      expect(pending.outcome_unknown).toBe(false)
    },
  )

  it('结果未知时禁止Escape、遮罩和关闭按钮关闭', async () => {
    const preview = {
      proposal_hash: 'p'.repeat(64),
      decision_hash: 'd'.repeat(64),
      account_version: 8,
      orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
    }
    api.getCommitteeOrderPreview.mockResolvedValue({ preview })
    api.bindCommitteeOrderPreview.mockResolvedValue({ preview_id: 'pv-unknown', preview })
    api.approveCommitteeRun.mockRejectedValue(new TypeError('Failed to fetch'))
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
    const dialog = await screen.findByRole('dialog', { name: '审批订单' })
    await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
    await userEvent.click(
      await within(dialog).findByRole('button', { name: '确认提交模拟盘' }),
    )
    await within(dialog).findByRole('alert')
    expect(within(dialog).getByRole('button', { name: '关闭审批订单' })).toBeDisabled()
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('dialog', { name: '审批订单' })).toBeInTheDocument()
    await userEvent.click(dialog.parentElement as HTMLElement)
    expect(screen.getByRole('dialog', { name: '审批订单' })).toBeInTheDocument()
  })

  it('关闭重开同一run恢复pending approval和原key，成功后清除', async () => {
    const preview = {
      proposal_hash: 'p'.repeat(64),
      decision_hash: 'd'.repeat(64),
      account_version: 8,
      orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
    }
    api.getCommitteeOrderPreview.mockResolvedValue({ preview })
    api.bindCommitteeOrderPreview.mockResolvedValue({ preview_id: 'pv-resume', preview })
    api.approveCommitteeRun.mockResolvedValue({ approval: {}, replayed: false })
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
    let dialog = await screen.findByRole('dialog', { name: '审批订单' })
    await userEvent.click(within(dialog).getByRole('button', { name: '进入二次确认' }))
    await within(dialog).findByRole('button', { name: '确认提交模拟盘' })
    const pending = JSON.parse(
      sessionStorage.getItem('committee:pending-approval:run-1') || '{}',
    ) as { idempotency_key?: string }
    await userEvent.click(within(dialog).getByRole('button', { name: '关闭审批订单' }))
    await userEvent.click(screen.getByRole('button', { name: '审批订单' }))
    dialog = await screen.findByRole('dialog', { name: '审批订单' })
    expect(within(dialog).getByText('账户版本 8')).toBeInTheDocument()
    expect(api.bindCommitteeOrderPreview).toHaveBeenCalledTimes(1)
    await userEvent.click(within(dialog).getByRole('button', { name: '确认提交模拟盘' }))
    expect(api.approveCommitteeRun.mock.calls[0][2]).toBe(pending.idempotency_key)
    expect(await within(dialog).findByText('订单已成功提交模拟盘。')).toBeInTheDocument()
    expect(sessionStorage.getItem('committee:pending-approval:run-1')).toBeNull()
  })

  it('pending approval按run隔离', async () => {
    const run2 = { ...completedRun, run_id: 'run-2' }
    const preview = {
      proposal_hash: 'p'.repeat(64),
      decision_hash: 'd'.repeat(64),
      account_version: 8,
      orders: [{ symbol: '510300', side: 'buy', qty: 100, price: 4.1 }],
    }
    sessionStorage.setItem(
      'committee:pending-approval:run-1',
      JSON.stringify({
        run_id: 'run-1',
        preview_id: 'pv-old',
        preview,
        idempotency_key: 'old-key',
        outcome_unknown: true,
      }),
    )
    api.listCommitteeRuns.mockResolvedValue({ runs: [run2] })
    api.getCommitteeRun.mockResolvedValue({ run: run2, events: [], artifacts: [] })
    api.getCommitteeOrderPreview.mockResolvedValue({ preview })
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '审批订单' }))
    expect(api.getCommitteeOrderPreview).toHaveBeenCalledWith(
      'run-2',
      expect.any(AbortSignal),
    )
    expect(screen.queryByText('状态未知/稍后用同一key重试')).not.toBeInTheDocument()
  })

  it('重试会议立即选择新run并清空旧详情', async () => {
    const failedRun = { ...completedRun, status: 'failed', error_message: 'boom' }
    const nextDetail = deferred<unknown>()
    api.listCommitteeRuns.mockResolvedValue({ runs: [failedRun] })
    api.getCommitteeRun
      .mockResolvedValueOnce({ run: failedRun, events: [], artifacts: [] })
      .mockImplementationOnce(() => nextDetail.promise)
    api.retryCommitteeRun.mockResolvedValue({ run_id: 'run-new', status: 'queued' })
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '重试会议' }))
    expect(screen.getByText('载入会议详情与历史事件…')).toBeInTheDocument()
    expect(screen.queryByText('run-1', { selector: '.committee-chat-header h2' })).not.toBeInTheDocument()
    expect(api.getCommitteeRun).toHaveBeenLastCalledWith(
      'run-new',
      expect.any(AbortSignal),
    )
  })

  it('关闭审批弹窗会中止preview请求并恢复焦点，Escape可关闭', async () => {
    let previewSignal: AbortSignal | undefined
    api.getCommitteeOrderPreview.mockImplementation(
      (_runId: string, signal?: AbortSignal) => {
        previewSignal = signal
        return new Promise(() => undefined)
      },
    )
    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    const approvalButton = await screen.findByRole('button', { name: '审批订单' })
    await userEvent.click(approvalButton)
    const dialog = await screen.findByRole('dialog', { name: '审批订单' })
    expect(within(dialog).getByRole('button', { name: '关闭审批订单' })).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: '审批订单' })).not.toBeInTheDocument()
    expect(previewSignal?.aborted).toBe(true)
    expect(approvalButton).toHaveFocus()
  })
})

describe('CommitteeDetail node mapping', () => {
  it('把后端 prepare 节点映射为数据快照', () => {
    render(
      <CommitteeDetail
        run={completedRun as never}
        artifacts={[]}
        streamState="历史完成态"
        events={[{
          event_id: '1-0',
          sequence: 1,
          event_type: 'node_completed',
          payload: { node: 'prepare' },
        }]}
      />,
    )
    expect(screen.getByText('数据快照').closest('li')).toHaveAttribute('data-status', 'completed')
  })
})
