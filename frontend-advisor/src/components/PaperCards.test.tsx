import '@testing-library/jest-dom/vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import {
  PaperPerformanceCard,
  PaperPositionCard,
  PaperTradeCard,
} from './PaperCards'
import type { PaperAccount } from '../api'

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  formatPct: (v: number | null | undefined, digits = 1) =>
    v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(digits)}%`,
}))

it('持仓卡片展示持仓字段并触发卖出和删除回调', async () => {
  const user = userEvent.setup()
  const onSell = vi.fn()
  const onDelete = vi.fn()
  const position: PaperAccount['positions'][number] = {
    symbol: '159518',
    name: '标普油气ETF嘉实',
    qty: 200,
    cost: 1.23,
    last: 1.35,
    market_value: 270,
    pnl: 24,
    pnl_pct: 0.09756,
  }

  render(<PaperPositionCard position={position} onSell={onSell} onDelete={onDelete} />)

  const card = screen.getByRole('article', { name: '标普油气ETF嘉实 159518' })
  expect(card).toHaveClass('paper-card--position')
  expect(within(card).getByText('标普油气ETF嘉实')).toBeInTheDocument()
  expect(within(card).getByText('159518')).toBeInTheDocument()
  expect(within(card).getByText('200')).toBeInTheDocument()
  expect(within(card).getByText('1.23')).toBeInTheDocument()
  expect(within(card).getByText('1.35')).toBeInTheDocument()
  expect(within(card).getByText('270.00')).toBeInTheDocument()
  expect(within(card).getByText('9.76%')).toBeInTheDocument()
  expect(within(card).getByRole('link', { name: '查看K线' })).toHaveAttribute(
    'href',
    expect.stringContaining('symbol=159518'),
  )

  await user.click(within(card).getByRole('button', { name: '卖出' }))
  await user.click(within(card).getByRole('button', { name: '删除' }))

  expect(onSell).toHaveBeenCalledWith(position)
  expect(onDelete).toHaveBeenCalledWith(position)
})

it('跟踪卡片使用当前跟踪表字段键并复用百分比格式', () => {
  render(
    <PaperPerformanceCard
      row={{
        symbol: '600519',
        name: '贵州茅台',
        rec_date: '2026-07-24',
        buy_price: 1510.5,
        last: 1525.2,
        unrealized_pnl_pct: 0.00973,
      }}
    />,
  )

  const card = screen.getByRole('article', { name: '贵州茅台 600519' })
  expect(card).toHaveClass('paper-card--performance')
  expect(within(card).getByText('600519')).toBeInTheDocument()
  expect(within(card).getByText('贵州茅台')).toBeInTheDocument()
  expect(within(card).getByText('2026-07-24')).toBeInTheDocument()
  expect(within(card).getByText('1510.5')).toBeInTheDocument()
  expect(within(card).getByText('1525.2')).toBeInTheDocument()
  expect(within(card).getByText('0.97%')).toBeInTheDocument()
})

it('成交卡片使用当前成交表字段键且不自行计算交易结果', () => {
  render(
    <PaperTradeCard
      trade={{
        created_at: '2026-07-24T10:12:13',
        side: 'sell',
        symbol: '510300',
        name: '沪深300ETF',
        qty: 100,
        price: 4.12,
        source: 'manual',
      }}
    />,
  )

  const card = screen.getByRole('article', { name: '沪深300ETF 510300' })
  expect(card).toHaveClass('paper-card--trade')
  expect(within(card).getByText('2026-07-24 10:12:13')).toBeInTheDocument()
  expect(within(card).getByText('卖出')).toBeInTheDocument()
  expect(within(card).getByText('510300')).toBeInTheDocument()
  expect(within(card).getByText('沪深300ETF')).toBeInTheDocument()
  expect(within(card).getByText('100')).toBeInTheDocument()
  expect(within(card).getByText('4.12')).toBeInTheDocument()
  expect(within(card).getByText('manual')).toBeInTheDocument()
  expect(within(card).queryByText('412')).not.toBeInTheDocument()
})
