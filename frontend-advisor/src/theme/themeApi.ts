import { authFetch } from '../auth'
import type { ThemeSettings } from './theme'

export function fetchThemeSettings(): Promise<ThemeSettings> {
  return authFetch<ThemeSettings>('/api/advisor/ui/settings')
}

export function saveThemeSettings(settings: ThemeSettings): Promise<ThemeSettings> {
  return authFetch<ThemeSettings>('/api/advisor/ui/settings', {
    method: 'PUT',
    body: JSON.stringify({
      active_template: settings.active_template,
      colors: settings.colors,
    }),
  })
}
