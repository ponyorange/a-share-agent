import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { AdviceItem } from '../api'
import { AdviceCard } from './AdviceCard'

const item: AdviceItem = {
  symbol: '600519',
  name: '贵州茅台',
  as_of: '2026-08-13',
  close: 1600,
  score: 0.72,
  action: 'watch',
  action_label: '观望',
  has_position: false,
  factors: [],
  hit_rate: 0.5,
  rationale: '量价一般',
  graph_signal: {
    action: 'SELL',
    scores: { BUY: 0.1, HOLD: 0.4, SELL: 1.1 },
    market_regime: 'bear',
    patterns: ['momentum_down'],
    horizon_days: 5,
  },
}

it('诊断卡并列展示多因子建议与图学习信号', () => {
  render(<AdviceCard item={item} />)
  expect(screen.getByText('观望')).toBeInTheDocument()
  expect(screen.getByText('图卖出')).toBeInTheDocument()
  expect(screen.getByText(/图学习（5日）/)).toBeInTheDocument()
  expect(screen.getByText(/bear/)).toBeInTheDocument()
  expect(screen.getByText(/momentum_down/)).toBeInTheDocument()
})
