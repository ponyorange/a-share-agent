import { describe, expect, it } from 'vitest'
import { formatNav, formatNavPct, normalizeFundSymbol } from './fundApi'

describe('fundApi helpers', () => {
  it('normalizes symbol digits', () => {
    expect(normalizeFundSymbol('025857（前端）')).toBe('025857')
    expect(normalizeFundSymbol('25857')).toBe('25857')
  })

  it('formats nav and pct', () => {
    expect(formatNav(1.1087)).toBe('1.1087')
    expect(formatNavPct(-3.37)).toBe('-3.37%')
    expect(formatNavPct(4.69)).toBe('+4.69%')
    expect(formatNav(null)).toBe('—')
  })
})
