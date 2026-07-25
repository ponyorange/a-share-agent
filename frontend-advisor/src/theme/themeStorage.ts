import { THEME_TEMPLATES, applyTheme, isThemeSettings, type ThemeSettings } from './theme'

const key = (userId: string) => `advisor_theme:${userId}`

export function readCachedTheme(userId: string): ThemeSettings | null {
  try {
    const raw = localStorage.getItem(key(userId))
    const parsed: unknown = raw ? JSON.parse(raw) : null
    return isThemeSettings(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function writeCachedTheme(userId: string, settings: ThemeSettings): void {
  localStorage.setItem(key(userId), JSON.stringify(settings))
}

export function bootstrapTheme(userId?: string): void {
  applyTheme((userId ? readCachedTheme(userId) : null) ?? THEME_TEMPLATES.modern_data)
}
