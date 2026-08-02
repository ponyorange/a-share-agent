import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import * as api from '../api'
import RegimePage from './RegimePage'

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    fetchRegimeCurrent: vi.fn(),
    fetchRegimeHistory: vi.fn(),
  }
})

function HomeProbe() {
  const location = useLocation()
  return <p data-testid="override">{location.search}</p>
}

beforeEach(() => {
  vi.mocked(api.fetchRegimeCurrent).mockReset()
  vi.mocked(api.fetchRegimeHistory).mockReset()
  vi.mocked(api.fetchRegimeHistory).mockResolvedValue([])
})

it('shows decision-first Chinese hero without raw English enums', async () => {
  vi.mocked(api.fetchRegimeCurrent).mockResolvedValue({
    gate_level: 'defensive',
    position_cap: 0.35,
    trend_regime: 'range',
    sentiment_cycle: 'ebb',
    data_quality: 'ok',
    evidence: [{ key: 'seal_rate', value: '0.4', note: '封板偏弱，赚钱效应一般' }],
    override_allowed: true,
  })

  render(
    <MemoryRouter initialEntries={['/regime']}>
      <RegimePage />
    </MemoryRouter>,
  )

  expect(await screen.findByRole('heading', { name: '今日闸门' })).toBeInTheDocument()
  expect(screen.getByText('先轻仓观望')).toBeInTheDocument()
  expect(screen.getByText(/建议总仓位不超过\s*35%/)).toBeInTheDocument()
  expect(screen.getAllByText('赚钱效应转弱或结构一般，先降仓位、少开新仓。').length).toBeGreaterThan(0)
  expect(screen.getByText('趋势：震荡')).toBeInTheDocument()
  expect(screen.getByText('情绪：退潮')).toBeInTheDocument()
  expect(screen.getByText('数据：可用')).toBeInTheDocument()
  expect(screen.getByText('为什么这样判')).toBeInTheDocument()
  expect(screen.getAllByText('封板偏弱，赚钱效应一般').length).toBeGreaterThan(0)
  expect(screen.queryByText(/raw gate_level/i)).not.toBeInTheDocument()
  expect(screen.queryByText('defensive')).not.toBeInTheDocument()
  expect(screen.queryByText('range')).not.toBeInTheDocument()
  expect(screen.queryByText('ebb')).not.toBeInTheDocument()
})

it('keeps metrics details collapsed by default', async () => {
  vi.mocked(api.fetchRegimeCurrent).mockResolvedValue({
    gate_level: 'normal',
    position_cap: 0.7,
    trend_regime: 'uptrend',
    sentiment_cycle: 'strengthen',
    data_quality: 'ok',
    evidence: [{ key: 'seal_rate', value: '0.4', note: '封板率尚可' }],
    metrics: { sentiment_score: 0.62 },
  })

  render(
    <MemoryRouter initialEntries={['/regime']}>
      <RegimePage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('查看指标明细')).toBeInTheDocument()
  expect(document.querySelector('details.regime-details')).not.toHaveAttribute('open')
})

it('shows risk_off and override CTA', async () => {
  vi.mocked(api.fetchRegimeCurrent).mockResolvedValue({
    gate_level: 'risk_off',
    position_cap: 0.15,
    trend_regime: 'range',
    sentiment_cycle: 'ebb',
    data_quality: 'ok',
    evidence: [{ key: 'seal_rate', value: '0.4', note: '' }],
    override_allowed: true,
  })

  render(
    <MemoryRouter initialEntries={['/regime']}>
      <Routes>
        <Route path="/regime" element={<RegimePage />} />
        <Route path="/" element={<HomeProbe />} />
      </Routes>
    </MemoryRouter>,
  )

  expect(await screen.findByText('今天先别急着买')).toBeInTheDocument()
  expect(screen.queryByText('risk_off')).not.toBeInTheDocument()
  const cta = screen.getByRole('button', { name: /仍要看今日关注/ })
  expect(cta).toBeInTheDocument()

  await userEvent.click(cta)
  expect(screen.getByTestId('override')).toHaveTextContent('?regime_override=1')
})

it('hides override CTA when risk_off but override_allowed is false', async () => {
  vi.mocked(api.fetchRegimeCurrent).mockResolvedValue({
    gate_level: 'risk_off',
    position_cap: 0.15,
    trend_regime: 'range',
    sentiment_cycle: 'ebb',
    data_quality: 'ok',
    evidence: [{ key: 'seal_rate', value: '0.4', note: '' }],
    override_allowed: false,
  })

  render(
    <MemoryRouter initialEntries={['/regime']}>
      <RegimePage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('今天先别急着买')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /仍要看今日关注/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /查看今日关注/ })).not.toBeInTheDocument()
})

it('shows recent regime history', async () => {
  vi.mocked(api.fetchRegimeCurrent).mockResolvedValue({
    gate_level: 'normal',
    position_cap: 0.7,
    trend_regime: 'uptrend',
    sentiment_cycle: 'strengthen',
    data_quality: 'ok',
    evidence: [],
  })
  vi.mocked(api.fetchRegimeHistory).mockResolvedValue([
    {
      trade_date: '2026-08-02',
      gate_level: 'normal',
      position_cap: 0.7,
      trend_regime: 'uptrend',
      sentiment_cycle: 'strengthen',
      data_quality: 'ok',
    },
    {
      trade_date: '2026-08-01',
      gate_level: 'risk_off',
      position_cap: 0.15,
      trend_regime: 'range',
      sentiment_cycle: 'ebb',
      data_quality: 'ok',
    },
  ])

  render(
    <MemoryRouter initialEntries={['/regime']}>
      <RegimePage />
    </MemoryRouter>,
  )

  expect(await screen.findByText('近 N 日周期')).toBeInTheDocument()
  expect(screen.getByText('2026-08-01')).toBeInTheDocument()
  expect(screen.getByText('风险关闭')).toBeInTheDocument()
  expect(screen.queryByText('risk_off')).not.toBeInTheDocument()
})
