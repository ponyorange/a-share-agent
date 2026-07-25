import { expect, it, vi } from 'vitest'
import { streamAgentChat } from './agentApi'

it('解析 subagent_progress SSE', async () => {
  const onSubagentProgress = vi.fn()
  const body = [
    'event: subagent_progress',
    'data: {"phase":"data_agent","step":"fetch","status":"completed","message":"已获取 53 行数据","source":"akshare","interface":"daily","rows":53,"truncated":false}',
    '',
    '',
  ].join('\n')
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(body))
          controller.close()
        },
      }), { status: 200 }),
    ),
  )

  await streamAgentChat('query', 's', { onSubagentProgress })
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
