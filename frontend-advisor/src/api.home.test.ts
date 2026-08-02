import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as auth from './auth'

vi.mock('./auth', async () => {
  const actual = await vi.importActual<typeof import('./auth')>('./auth')
  return { ...actual, authFetch: vi.fn(), getToken: () => 't' }
})

describe('home api helpers', () => {
  beforeEach(() => {
    vi.mocked(auth.authFetch).mockReset()
  })

  it('fetchHomeSectors hits advisor market/sectors', async () => {
    vi.mocked(auth.authFetch).mockResolvedValue({
      trade_date: '2026-08-01',
      ok: true,
      source: 't',
      items: [],
    })
    const { fetchHomeSectors } = await import('./api')
    await fetchHomeSectors(5)
    expect(auth.authFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/advisor/market/sectors?top=5'),
    )
  })

  it('fetchRegimeSummary hits regime/summary', async () => {
    vi.mocked(auth.authFetch).mockResolvedValue({ gate_level: 'normal' })
    const { fetchRegimeSummary } = await import('./api')
    await fetchRegimeSummary()
    expect(auth.authFetch).toHaveBeenCalledWith('/api/advisor/regime/summary')
  })
})
