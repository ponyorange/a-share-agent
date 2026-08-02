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
  }
})

function HomeProbe() {
  const location = useLocation()
  return <p data-testid="override">{location.search}</p>
}

beforeEach(() => {
  vi.mocked(api.fetchRegimeCurrent).mockReset()
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
