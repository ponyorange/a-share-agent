import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  linkifyPlainText,
  normalizeLinkedSymbol,
} from './linkifyStockSymbols'

describe('normalizeLinkedSymbol', () => {
  it('strips market prefix', () => {
    expect(normalizeLinkedSymbol('SH510300')).toBe('510300')
    expect(normalizeLinkedSymbol('sz159915')).toBe('159915')
  })
})

describe('linkifyPlainText', () => {
  it('links A-share and ETF codes', () => {
    const nodes = linkifyPlainText('关注 510300 与 159915，以及 600519。')
    render(<p>{nodes}</p>)
    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(3)
    expect(links[0]).toHaveTextContent('510300')
    expect(links[0]).toHaveAttribute('href', expect.stringContaining('symbol=510300'))
    expect(links[1]).toHaveAttribute('href', expect.stringContaining('symbol=159915'))
    expect(links[2]).toHaveAttribute('href', expect.stringContaining('symbol=600519'))
  })

  it('skips year-month-like numbers', () => {
    const nodes = linkifyPlainText('截至 202401 的数据')
    render(<p>{nodes}</p>)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('does not link digits inside words or decimals', () => {
    const nodes = linkifyPlainText('x510300x 与 510300.5')
    render(<p>{nodes}</p>)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
