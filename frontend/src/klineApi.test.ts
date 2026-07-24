import { describe, expect, it } from 'vitest'
import {
  isValidAvgPrice,
  shouldShowAvgPrice,
  type KlineBar,
} from './klineApi'

describe('avg price helpers', () => {
  it('accepts finite positive prices', () => {
    expect(isValidAvgPrice(10.5)).toBe(true)
    expect(isValidAvgPrice(0)).toBe(false)
    expect(isValidAvgPrice(-1)).toBe(false)
    expect(isValidAvgPrice(Number.NaN)).toBe(false)
    expect(isValidAvgPrice(undefined)).toBe(false)
  })

  it('shows avg only for realtime with valid points', () => {
    const bars: KlineBar[] = [
      {
        time: '2026-07-25 09:31',
        open: 1,
        high: 1,
        low: 1,
        close: 1,
        avg_price: 1.1,
      },
    ]
    expect(shouldShowAvgPrice('realtime', bars)).toBe(true)
    expect(shouldShowAvgPrice('daily', bars)).toBe(false)
    expect(
      shouldShowAvgPrice('realtime', [
        { ...bars[0], avg_price: undefined },
      ]),
    ).toBe(false)
  })
})
