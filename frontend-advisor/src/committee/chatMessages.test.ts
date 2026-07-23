import { describe, expect, it } from 'vitest'
import type {
  CommitteeArtifact,
  CommitteeEventRecord,
  ParsedSseEvent,
} from './committeeApi'
import {
  ROLE_META,
  applyChatSseEvent,
  chatMessagesReducer,
  initialChatMessagesState,
  messagesFromArtifacts,
  messagesFromEvents,
} from './chatMessages'

function sse(
  event: string,
  data: Record<string, unknown>,
  id?: string,
): ParsedSseEvent {
  return { event, data, id }
}

function reduceEvents(events: ParsedSseEvent[]) {
  return events.reduce(applyChatSseEvent, initialChatMessagesState)
}

describe('chat message reducer', () => {
  it('更高 generation 的 started 清空同 message_id 的旧临时文本', () => {
    const state = reduceEvents([
      sse('message_started', {
        message_id: 'm1',
        role: 'technical',
        node: 'technical',
        generation: 1,
      }, '8-live-1'),
      sse('message_delta', {
        message_id: 'm1',
        delta: '旧',
        offset: 0,
        generation: 1,
      }, '8-live-2'),
      sse('message_started', {
        message_id: 'm1',
        role: 'technical',
        node: 'technical',
        generation: 2,
      }, '9-live-1'),
      sse('message_delta', {
        message_id: 'm1',
        delta: '新',
        offset: 0,
        generation: 2,
      }, '9-live-2'),
    ])

    expect(state.byId.m1.content).toBe('新')
    expect(state.byId.m1.generation).toBe(2)
    expect(state.byId.m1.status).toBe('streaming')
  })

  it('completed 始终覆盖临时文本、状态和临时 sequence', () => {
    const streaming = reduceEvents([
      sse('message_started', {
        message_id: 'm1',
        role: 'technical',
        node: 'technical',
        generation: 1,
      }, '12-live-1'),
      sse('message_delta', {
        message_id: 'm1',
        delta: '临时',
        offset: 0,
        generation: 1,
      }, '12-live-2'),
    ])
    const completed = chatMessagesReducer(streaming, {
      type: 'merge',
      events: [{
        event_id: 'mongo-1',
        sequence: 41,
        event_type: 'message_completed',
        payload: {
          message_id: 'm1',
          role: 'technical',
          node: 'technical',
          content: '权威完成文本',
          status: 'degraded',
          sequence: 0,
          generation: 1,
        },
      }],
    })

    expect(completed.byId.m1.content).toBe('权威完成文本')
    expect(completed.byId.m1.status).toBe('degraded')
    expect(completed.byId.m1.sequence).toBe(41)
  })

  it('仅追加 offset 等于当前内容长度且 generation 匹配的 delta', () => {
    const state = reduceEvents([
      sse('message_started', {
        message_id: 'm1',
        role: 'quant',
        node: 'quant',
        generation: 2,
      }),
      sse('message_delta', {
        message_id: 'm1',
        delta: 'AB',
        offset: 0,
        generation: 2,
      }),
      sse('message_delta', {
        message_id: 'm1',
        delta: '重复',
        offset: 0,
        generation: 2,
      }),
      sse('message_delta', {
        message_id: 'm1',
        delta: '缺口',
        offset: 5,
        generation: 2,
      }),
      sse('message_delta', {
        message_id: 'm1',
        delta: '旧代',
        offset: 2,
        generation: 1,
      }),
    ])

    expect(state.byId.m1.content).toBe('AB')
    expect(state.byId.m1.nextOffset).toBe(2)
  })

  it('offset 和 nextOffset 使用 Unicode 码点长度', () => {
    const state = reduceEvents([
      sse('message_started', {
        message_id: 'emoji',
        role: 'quant',
        node: 'quant',
        generation: 1,
      }),
      sse('message_delta', {
        message_id: 'emoji',
        delta: '😀',
        offset: 0,
        generation: 1,
      }),
      sse('message_delta', {
        message_id: 'emoji',
        delta: '缺口',
        offset: 2,
        generation: 1,
      }),
      sse('message_delta', {
        message_id: 'emoji',
        delta: 'A',
        offset: 1,
        generation: 1,
      }),
    ])

    expect(state.byId.emoji.content).toBe('😀A')
    expect(state.byId.emoji.nextOffset).toBe(2)
  })

  it('SSE id 首段整数作为临时 sequence', () => {
    const state = reduceEvents([
      sse('message_started', {
        message_id: 'm12',
        role: 'bull',
        node: 'bull',
        generation: 1,
      }, '12-0'),
      sse('message_started', {
        message_id: 'm7',
        role: 'bear',
        node: 'bear',
        generation: 1,
      }, '7-live-3'),
    ])

    expect(state.byId.m12.sequence).toBe(12)
    expect(state.byId.m7.sequence).toBe(7)
    expect(state.order).toEqual(['m7', 'm12'])
  })

  it('live message_completed 用 SSE id 覆盖 started 的临时 sequence', () => {
    const state = reduceEvents([
      sse('message_started', {
        message_id: 'm1',
        role: 'chair',
        node: 'chair',
        generation: 1,
      }, '99-live-1'),
      sse('message_completed', {
        message_id: 'm1',
        role: 'chair',
        node: 'chair',
        content: '最终裁决',
        status: 'completed',
        sequence: 0,
        generation: 1,
      }, '12-0'),
    ])

    expect(state.byId.m1.sequence).toBe(12)
    expect(state.byId.m1.content).toBe('最终裁决')
  })

  it('可按 Unicode 码点逐步展示无 delta 的完成消息', () => {
    const completed = reduceEvents([
      sse('message_completed', {
        message_id: 'm-reveal',
        role: 'chair',
        node: 'chair',
        content: 'A😀B',
        status: 'completed',
        generation: 1,
      }, '12-0'),
    ]).byId['m-reveal']

    const partial = chatMessagesReducer(initialChatMessagesState, {
      type: 'revealCompleted',
      message: completed,
      visibleCodePoints: 2,
    })
    expect(partial.byId['m-reveal']).toMatchObject({
      content: 'A😀',
      status: 'streaming',
      nextOffset: 2,
      revealing: true,
    })

    const finished = chatMessagesReducer(partial, {
      type: 'revealCompleted',
      message: completed,
      visibleCodePoints: 3,
    })
    expect(finished.byId['m-reveal']).toMatchObject({
      content: 'A😀B',
      status: 'completed',
      nextOffset: 3,
      revealing: false,
    })
  })

  it('merge 不覆盖尚未结束的本地逐字展示', () => {
    const completed = reduceEvents([
      sse('message_completed', {
        message_id: 'm-reveal',
        role: 'chair',
        node: 'chair',
        content: '完整内容',
        status: 'completed',
        generation: 1,
      }),
    ]).byId['m-reveal']
    const partial = chatMessagesReducer(initialChatMessagesState, {
      type: 'revealCompleted',
      message: completed,
      visibleCodePoints: 1,
    })

    const merged = chatMessagesReducer(partial, {
      type: 'merge',
      messages: [completed],
    })

    expect(merged.byId['m-reveal'].content).toBe('完')
    expect(merged.byId['m-reveal'].revealing).toBe(true)
  })

  it('interruptStreaming 将所有仍在输出的消息收尾为 failed', () => {
    const streaming = reduceEvents([
      sse('message_started', {
        message_id: 'm-streaming',
        role: 'technical',
        node: 'technical',
        generation: 1,
      }),
      sse('message_delta', {
        message_id: 'm-streaming',
        delta: '未完成',
        offset: 0,
        generation: 1,
      }),
      sse('message_completed', {
        message_id: 'm-done',
        role: 'chair',
        node: 'chair',
        content: '已完成',
        status: 'completed',
        generation: 1,
      }),
    ])

    const interrupted = chatMessagesReducer(streaming, {
      type: 'interruptStreaming',
    })

    expect(interrupted.byId['m-streaming'].status).toBe('failed')
    expect(interrupted.byId['m-done'].status).toBe('completed')
  })
})

describe('history conversion', () => {
  it('message_completed 使用外层 CommitteeEventRecord.sequence', () => {
    const events: CommitteeEventRecord[] = [{
      event_id: 'e1',
      sequence: 27,
      event_type: 'message_completed',
      payload: {
        message_id: 'm1',
        role: 'backtest',
        node: 'backtest',
        content: '完成',
        status: 'completed',
        sequence: 0,
        generation: 1,
      },
    }]

    expect(messagesFromEvents(events)[0].sequence).toBe(27)
  })

  it('旧会议没有 message_completed 时从 artifacts 转换', () => {
    const artifacts: CommitteeArtifact[] = [
      {
        artifact_id: 'a1',
        kind: 'analyst_reports',
        payload: [{
          role: 'technical',
          thesis: '旧技术观点',
          confidence: 0.6,
        }],
      },
      {
        artifact_id: 'a2',
        kind: 'backtest_verdict',
        payload: { passed: true, score: 0.8, summary: '稳' },
      },
    ]

    const messages = messagesFromArtifacts(artifacts, {
      runId: 'old',
      attempt: 1,
    })

    expect(messages.map((message) => message.role)).toEqual([
      'technical',
      'backtest',
    ])
    expect(messages[0].content).toContain('旧技术观点')
    expect(messages[1].content).toContain('稳')
  })

  it('新会议存在 completed 时 hydrate 不混入 artifact 副本', () => {
    const state = chatMessagesReducer(initialChatMessagesState, {
      type: 'hydrate',
      events: [{
        event_id: 'e1',
        sequence: 10,
        event_type: 'message_completed',
        payload: {
          message_id: 'new-technical',
          role: 'technical',
          node: 'technical',
          content: '新消息',
          status: 'completed',
          sequence: 0,
          generation: 1,
        },
      }],
      artifacts: [{
        artifact_id: 'a1',
        kind: 'analyst_reports',
        payload: [{ role: 'technical', thesis: '旧副本', confidence: 0.6 }],
      }],
      context: { runId: 'run', attempt: 1 },
    })

    expect(state.order).toEqual(['new-technical'])
    expect(state.byId['new-technical'].content).toBe('新消息')
  })

  it('混合历史用 artifacts 补齐缺失角色且 completed 优先去重', () => {
    const state = chatMessagesReducer(initialChatMessagesState, {
      type: 'hydrate',
      events: [{
        event_id: 'e1',
        sequence: 10,
        event_type: 'message_completed',
        payload: {
          message_id: 'stable-technical',
          role: 'technical',
          node: 'technical',
          content: '权威技术消息',
          status: 'completed',
          generation: 1,
        },
      }],
      artifacts: [{
        artifact_id: 'reports',
        run_id: 'run',
        attempt: 1,
        kind: 'analyst_reports',
        payload: [
          { role: 'technical', thesis: '技术 artifact 副本', confidence: 0.6 },
          { role: 'fundamental', thesis: '缺失的基本面消息', confidence: 0.7 },
        ],
      }],
      context: { runId: 'run', attempt: 1 },
    })

    expect(state.order).toHaveLength(2)
    expect(state.byId['stable-technical'].content).toBe('权威技术消息')
    expect(Object.values(state.byId)).not.toContainEqual(
      expect.objectContaining({ content: '技术 artifact 副本' }),
    )
    expect(Object.values(state.byId)).toContainEqual(
      expect.objectContaining({
        role: 'fundamental',
        content: '缺失的基本面消息',
      }),
    )
  })

  it('ROLE_META 使用设计标签', () => {
    expect(ROLE_META.technical.label).toBe('技术分析师')
    expect(ROLE_META.backtest.label).toBe('回测员')
  })
})
