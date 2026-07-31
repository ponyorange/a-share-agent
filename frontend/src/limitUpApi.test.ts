import { describe, expect, it } from 'vitest'
import {
  formatDayChgPct,
  shouldShowTodayTable,
  statusLabel,
} from './limitUpApi'

describe('limitUpApi helpers', () => {
  it('formats decimal ratio as percent', () => {
    expect(formatDayChgPct(0.1)).toBe('+10.00%')
    expect(formatDayChgPct(-0.05)).toBe('-5.00%')
  })

  it('shows today table only while trading', () => {
    expect(shouldShowTodayTable({ is_trading: true })).toBe(true)
    expect(shouldShowTodayTable({ is_trading: false })).toBe(false)
  })

  it('labels sealed vs broken', () => {
    expect(statusLabel('sealed')).toBe('当前涨停')
    expect(statusLabel('broken')).toBe('曾涨停')
  })
})
