import { describe, expect, it } from 'vitest'
import {
  PROVIDER_META,
  SLOT_ROWS,
  clampSlotModel,
  filterProviderModels,
} from './llmSettingsUi'

describe('filterProviderModels', () => {
  const available = [
    { id: 'deepseek-v4-flash' },
    { id: 'deepseek-chat' },
    { id: 'deepseek-v4-pro' },
    { id: 'other-pro-model' },
  ]
  const enabled = ['deepseek-v4-flash', 'deepseek-v4-pro']

  it('puts enabled models first in enabled order', () => {
    expect(filterProviderModels(available, enabled, '').map((m) => m.id)).toEqual([
      'deepseek-v4-flash',
      'deepseek-v4-pro',
      'deepseek-chat',
      'other-pro-model',
    ])
  })

  it('filters case-insensitively and keeps selected matches first', () => {
    expect(filterProviderModels(available, enabled, 'PRO').map((m) => m.id)).toEqual([
      'deepseek-v4-pro',
      'other-pro-model',
    ])
  })

  it('falls back to enabled ids when available is empty', () => {
    expect(filterProviderModels([], ['kimi-k2.6'], '').map((m) => m.id)).toEqual([
      'kimi-k2.6',
    ])
  })
})

describe('clampSlotModel', () => {
  it('keeps model when it is enabled', () => {
    expect(clampSlotModel('kimi-k2.7-code', ['kimi-k2.6', 'kimi-k2.7-code'])).toBe(
      'kimi-k2.7-code',
    )
  })

  it('falls back to first enabled when stale', () => {
    expect(clampSlotModel('kimi-k2.5', ['kimi-k2.6', 'kimi-k2.7-code'])).toBe(
      'kimi-k2.6',
    )
  })
})

describe('catalog constants', () => {
  it('exports three providers and eight slots', () => {
    expect(PROVIDER_META.map((p) => p.id)).toEqual(['deepseek', 'kimi', 'qwen'])
    expect(SLOT_ROWS).toHaveLength(8)
    expect(SLOT_ROWS[0]).toEqual({ id: 'agent', label: '主 Agent 对话' })
  })
})
