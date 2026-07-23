import { describe, expect, it } from 'vitest'
import {
  MAX_TIMELINE_EVENTS,
  initialTimelineState,
  timelineReducer,
} from './timeline'

describe('timeline reducer', () => {
  it('合并历史与实时事件时按 sequence 排序并按 event_id 去重', () => {
    const events = [
      { event_id: 'e2', sequence: 2, event_type: 'analyst', payload: { role: 'quant' } },
      { event_id: 'e1', sequence: 1, event_type: 'snapshot', payload: {} },
      { event_id: 'e2', sequence: 2, event_type: 'analyst', payload: { role: 'quant' } },
    ]
    const state = timelineReducer(initialTimelineState, { type: 'hydrate', events })
    expect(state.events.map((event) => event.event_id)).toEqual(['e1', 'e2'])
  })

  it('续接事件替换同 id 的较新数据且保持稳定排序', () => {
    const hydrated = timelineReducer(initialTimelineState, {
      type: 'hydrate',
      events: [{ event_id: 'e1', sequence: 1, event_type: 'running', payload: {} }],
    })
    const state = timelineReducer(hydrated, {
      type: 'event',
      event: {
        event_id: 'e1',
        sequence: 1,
        event_type: 'running',
        payload: { status: 'degraded' },
      },
    })
    expect(state.events).toHaveLength(1)
    expect(state.events[0].payload).toEqual({ status: 'degraded' })
  })

  it('增量插入乱序事件并只保留最新事件上限', () => {
    let state = initialTimelineState
    for (let sequence = MAX_TIMELINE_EVENTS + 10; sequence >= 1; sequence -= 1) {
      state = timelineReducer(state, {
        type: 'event',
        event: {
          event_id: `e${sequence}`,
          sequence,
          event_type: 'running',
          payload: {},
        },
      })
    }
    expect(state.events).toHaveLength(MAX_TIMELINE_EVENTS)
    expect(state.events[0].sequence).toBe(11)
    expect(state.events.at(-1)?.sequence).toBe(MAX_TIMELINE_EVENTS + 10)
  })
})
