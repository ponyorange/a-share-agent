import { beforeEach, expect, it, vi } from 'vitest'
import { THEME_TEMPLATES } from './theme'
import { fetchThemeSettings, saveThemeSettings } from './themeApi'

const authFetch = vi.hoisted(() => vi.fn())

vi.mock('../auth', () => ({ authFetch }))

beforeEach(() => {
  authFetch.mockReset()
})

it('获取用户主题设置', async () => {
  authFetch.mockResolvedValue(THEME_TEMPLATES.modern_data)

  await expect(fetchThemeSettings()).resolves.toBe(THEME_TEMPLATES.modern_data)

  expect(authFetch).toHaveBeenCalledWith('/api/advisor/ui/settings')
})

it('保存用户主题设置时只提交模板和颜色', async () => {
  const settings = { ...THEME_TEMPLATES.classic_market, updated_at: '2026-07-25T00:00:00Z' }
  authFetch.mockResolvedValue(settings)

  await expect(saveThemeSettings(settings)).resolves.toBe(settings)

  expect(authFetch).toHaveBeenCalledWith('/api/advisor/ui/settings', {
    method: 'PUT',
    body: JSON.stringify({
      active_template: settings.active_template,
      colors: settings.colors,
    }),
  })
})
