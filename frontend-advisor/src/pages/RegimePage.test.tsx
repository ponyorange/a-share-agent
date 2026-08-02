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

  expect(await screen.findByText(/risk_off|风险/i)).toBeInTheDocument()
  const cta = screen.getByRole('button', { name: /仍要看今日关注/ })
  expect(cta).toBeInTheDocument()

  await userEvent.click(cta)
  expect(screen.getByTestId('override')).toHaveTextContent('?regime_override=1')
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
  expect(screen.getByText('risk_off')).toBeInTheDocument()
})
