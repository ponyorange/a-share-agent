import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  approveCommitteeRun,
  CommitteeApiError,
  createCommitteeRun,
  createSseParser,
  deleteCommitteeRun,
  streamCommitteeEvents,
  waitForRetry,
} from './committeeApi'

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function eventStream(chunks: string[]) {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)))
        controller.close()
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream' } },
  )
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('advisor_token', 'secret')
  vi.restoreAllMocks()
})

describe('committee API', () => {
  it('删除会议使用 DELETE、认证头和编码后的 run id', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ run_id: 'run/1', deleted: true }),
    )

    await deleteCommitteeRun('run/1')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/advisor/committee/runs/run%2F1')
    expect(init?.method).toBe('DELETE')
    expect(new Headers(init?.headers).get('Authorization')).toBe(
      'Bearer secret',
    )
  })

  it('创建会议时携带认证和稳定幂等键', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ run_id: 'run-1', status: 'queued' }, 202),
    )

    await createCommitteeRun(
      {
        symbols: ['510300'],
        boards: [],
        horizon: 'next_day',
        strategy_version: 'v1',
      },
      'create-key',
    )

    const [, init] = fetchMock.mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer secret')
    expect(headers.get('Idempotency-Key')).toBe('create-key')
  })

  it('审批提交预览绑定值并携带幂等键', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ approval: { mutation_id: 'm1' }, replayed: false }),
    )
    await approveCommitteeRun(
      'run-1',
      {
        preview_id: 'preview-1',
        decision_hash: 'd'.repeat(64),
        proposal_hash: 'p'.repeat(64),
        account_version: 4,
        confirm: true,
      },
      'approve-key',
    )
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/advisor/committee/runs/run-1/approve')
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('approve-key')
  })

  it('审批409抛出带状态的CommitteeApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ detail: '审批正在执行' }, 409),
    )
    await expect(
      approveCommitteeRun(
        'run-1',
        {
          preview_id: 'pv1',
          decision_hash: 'd'.repeat(64),
          proposal_hash: 'p'.repeat(64),
          account_version: 1,
          confirm: true,
        },
        'same-key',
      ),
    ).rejects.toEqual(
      expect.objectContaining<Partial<CommitteeApiError>>({
        name: 'CommitteeApiError',
        status: 409,
        message: '审批正在执行',
      }),
    )
  })

  it('普通committee API遇到401清除会话并统一通知App', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ detail: 'expired' }, 401),
    )
    const authChanged = vi.fn()
    window.addEventListener('advisor-auth-changed', authChanged)
    await expect(
      createCommitteeRun(
        {
          symbols: ['510300'],
          boards: [],
          horizon: 'next_day',
          strategy_version: 'v1',
        },
        'key',
      ),
    ).rejects.toMatchObject({ status: 401 })
    expect(localStorage.getItem('advisor_token')).toBeNull()
    expect(authChanged).toHaveBeenCalledTimes(1)
    window.removeEventListener('advisor-auth-changed', authChanged)
  })
})

describe('SSE parser', () => {
  it('解析分片、CRLF、多行 data 并保留 id', () => {
    const events: unknown[] = []
    const parser = createSseParser((event) => events.push(event))
    parser.push('id: 7-0\r\nevent: node_')
    parser.push('completed\r\ndata: {"node":"bull",\r\ndata: "round":1}\r\n\r\n')
    parser.finish()
    expect(events).toEqual([
      {
        id: '7-0',
        event: 'node_completed',
        data: { node: 'bull', round: 1 },
      },
    ])
  })

  it('忽略注释并把无 event 字段视为 message', () => {
    const events: unknown[] = []
    const parser = createSseParser((event) => events.push(event))
    parser.push(': heartbeat\n\ndata: {"ok":true}\n\n')
    expect(events).toEqual([{ id: undefined, event: 'message', data: { ok: true } }])
  })

  it('支持仅使用 CR 的事件与行分隔', () => {
    const events: unknown[] = []
    const parser = createSseParser((event) => events.push(event))
    parser.push('id: 12-0\revent: completed\rdata: {"ok":true}\r\r')
    expect(events).toEqual([
      { id: '12-0', event: 'completed', data: { ok: true } },
    ])
  })
})

describe('committee event stream', () => {
  it('携带 Last-Event-ID 并在终态事件后停止', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      eventStream([
        'id: 9-0\nevent: node_completed\ndata: {"node":"chair"}\n\n',
        'id: 10-0\nevent: completed\ndata: {"status":"completed"}\n\nid: 11-0\nevent: running\ndata: {}\n\n',
      ]),
    )
    const seen: string[] = []
    await streamCommitteeEvents(
      'run-1',
      { onEvent: (event) => seen.push(event.event) },
      { lastEventId: '8-0' },
    )
    expect(seen).toEqual(['node_completed', 'completed'])
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('Last-Event-ID')).toBe('8')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('断流后指数退避并从最后事件续传', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(eventStream(['id: 3-0\nevent: running\ndata: {}\n\n']))
      .mockResolvedValueOnce(
        response({
          run: {
            user_id: 'u',
            run_id: 'run-1',
            status: 'running',
            version: 1,
            strategy_version: 'v1',
            horizon: 'next_day',
            universe: ['510300'],
            as_of: '2026-07-23T00:00:00Z',
            created_at: '2026-07-23T00:00:00Z',
            updated_at: '2026-07-23T00:00:00Z',
            attempt: 1,
          },
          artifacts: [],
          events: [],
        }),
      )
      .mockResolvedValueOnce(eventStream(['id: 4-0\nevent: completed\ndata: {}\n\n']))
    const delays: number[] = []
    await streamCommitteeEvents(
      'run-1',
      { onEvent: () => undefined },
      {
        reconnect: { initialDelayMs: 10, maxDelayMs: 100, maxAttempts: 2 },
        sleep: async (delay) => {
          delays.push(delay)
        },
      },
    )
    expect(delays).toEqual([10])
    expect(new Headers(fetchMock.mock.calls[2][1]?.headers).get('Last-Event-ID')).toBe('3')
  })

  it('AbortSignal 中止后不重连', async () => {
    const controller = new AbortController()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      controller.abort()
      throw init?.signal?.reason
    })
    await expect(
      streamCommitteeEvents(
        'run-1',
        { onEvent: () => undefined },
        { signal: controller.signal },
      ),
    ).resolves.toBeUndefined()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('401 清除会话并停止重连', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ detail: 'expired' }, 401),
    )
    const errors: Error[] = []
    await streamCommitteeEvents(
      'run-1',
      { onEvent: () => undefined, onError: (error) => errors.push(error) },
      {
        reconnect: { initialDelayMs: 1, maxAttempts: 3 },
        sleep: async () => undefined,
      },
    )
    expect(localStorage.getItem('advisor_token')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(errors[0]).toMatchObject({ status: 401 })
  })

  it.each([400, 403])('%s 客户端错误停止重连', async (status) => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response({ detail: 'forbidden' }, status),
    )
    await streamCommitteeEvents(
      'run-1',
      { onEvent: () => undefined },
      {
        reconnect: { initialDelayMs: 1, maxAttempts: 2 },
        sleep: async () => undefined,
      },
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('429 按 Retry-After 退避后续传', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'slow down' }), {
          status: 429,
          headers: { 'Retry-After': '3', 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        eventStream(['id: 1-0\nevent: completed\ndata: {}\n\n']),
      )
    const delays: number[] = []
    await streamCommitteeEvents(
      'run-1',
      { onEvent: () => undefined },
      { sleep: async (delay) => { delays.push(delay) } },
    )
    expect(delays).toEqual([3000])
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('流结束无终态事件时查询详情并停止重连', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(eventStream(['id: 1-0\nevent: queued\ndata: {}\n\n']))
      .mockResolvedValueOnce(
        response({
          run: {
            user_id: 'u',
            run_id: 'run-1',
            status: 'failed',
            version: 1,
            strategy_version: 'v1',
            horizon: 'next_day',
            universe: ['510300'],
            as_of: '2026-07-23T00:00:00Z',
            created_at: '2026-07-23T00:00:00Z',
            updated_at: '2026-07-23T00:00:00Z',
            attempt: 1,
            error_code: 'ValidationError',
            error_message: 'extra inputs are not permitted',
          },
          artifacts: [],
          events: [],
        }),
      )
    const seen: string[] = []
    await streamCommitteeEvents(
      'run-1',
      { onEvent: (event) => seen.push(event.event) },
      {
        reconnect: { initialDelayMs: 1, maxAttempts: 3 },
        sleep: async () => undefined,
      },
    )
    expect(seen).toEqual(['queued', 'failed'])
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('退避完成后移除AbortSignal监听器', async () => {
    vi.useFakeTimers()
    const controller = new AbortController()
    const add = vi.spyOn(controller.signal, 'addEventListener')
    const remove = vi.spyOn(controller.signal, 'removeEventListener')
    const pending = waitForRetry(100, controller.signal)
    await vi.advanceTimersByTimeAsync(100)
    await pending
    expect(add).toHaveBeenCalledWith('abort', expect.any(Function), { once: true })
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function))
    vi.useRealTimers()
  })
})
