import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it } from 'vitest'
import type { AdviceItem } from '../api'
import { RecommendationCard } from './RecommendationCard'

const item: AdviceItem = {
  symbol: '159518',
  name: '标普油气ETF嘉实',
  close: 1.234,
  day_chg_pct: 0.0123,
  score: 0.876,
  action: 'buy',
  action_label: '买入关注',
  has_position: false,
  factors: [],
  hit_rate: 0.654,
  rationale: '测试推荐理由',
}

it('展示推荐关键字段并提供诊断、K 线与收藏入口', () => {
  render(
    <MemoryRouter>
      <RecommendationCard
        item={item}
        starred={false}
        onToggleStar={() => undefined}
      />
    </MemoryRouter>,
  )

  expect(screen.getByText('标普油气ETF嘉实')).toBeInTheDocument()
  expect(screen.getByText('159518')).toBeInTheDocument()
  expect(screen.getByText('1.234')).toBeInTheDocument()
  expect(screen.getByText('1.23%')).toBeInTheDocument()
  expect(screen.getByText('0.88')).toBeInTheDocument()
  expect(screen.getByText('买入关注')).toBeInTheDocument()
  expect(screen.getByText('65.4%')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '收藏 159518' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '诊断' })).toHaveAttribute(
    'href',
    '/advice?symbol=159518',
  )
  expect(screen.getByRole('link', { name: '查看 K 线' })).toHaveAttribute(
    'href',
    'http://127.0.0.1:5173/akshare/kline?symbol=159518&range=daily',
  )
  expect(screen.queryByRole('button', { name: '买入' })).not.toBeInTheDocument()
})
