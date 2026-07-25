import { expect, it, vi } from 'vitest'
import { streamAgentChat } from './agentApi'

it('解析 subagent_progress SSE，并以 POST body 发送消息', async () => {
  const onSubagentProgress = vi.fn()
  const body = [
    'event: subagent_progress',
    'data: {"phase":"data_agent","step":"fetch","status":"completed","message":"已获取 53 行数据","source":"akshare","interface":"daily","rows":53,"truncated":false}',
    '',
    '',
  ].join('\n')
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(body))
          controller.close()
        },
      }),
      { status: 200 },
    ),
  )
  vi.stubGlobal('fetch', fetchMock)

  const longMessage = '测'.repeat(2500)
  await streamAgentChat(longMessage, 's', { onSubagentProgress })

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/advisor/agent/chat/stream',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ message: longMessage, session_id: 's' }),
    }),
  )
  expect(onSubagentProgress).toHaveBeenCalledWith({
    phase: 'data_agent',
    step: 'fetch',
    status: 'completed',
    message: '已获取 53 行数据',
    source: 'akshare',
    interface: 'daily',
    rows: 53,
    truncated: false,
  })
})
