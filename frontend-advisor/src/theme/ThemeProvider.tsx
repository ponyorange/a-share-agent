import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { THEME_TEMPLATES, applyTheme, isThemeSettings, type ThemeSettings } from './theme'
import { readCachedTheme, writeCachedTheme } from './themeStorage'
import { fetchThemeSettings, saveThemeSettings } from './themeApi'

type ThemeContextValue = {
  settings: ThemeSettings
  loading: boolean
  error: string | null
  save: (draft: ThemeSettings) => Promise<ThemeSettings>
}

type ThemeProviderProps = {
  userId: string | null
  children: ReactNode
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function initialTheme(userId: string | null): ThemeSettings {
  return (userId ? readCachedTheme(userId) : null) ?? THEME_TEMPLATES.modern_data
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '主题设置加载失败'
}

function coerceRemoteTheme(remote: unknown, fallback: ThemeSettings): ThemeSettings {
  return isThemeSettings(remote)
    ? {
        active_template: remote.active_template,
        colors: { ...remote.colors },
        updated_at: remote.updated_at ?? null,
      }
    : fallback
}

export function ThemeProvider({ userId, children }: ThemeProviderProps) {
  const requestTokenRef = useRef(0)
  const [settings, setSettings] = useState<ThemeSettings>(() => initialTheme(userId))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = ++requestTokenRef.current

    if (!userId) {
      const fallback = THEME_TEMPLATES.modern_data
      setSettings(fallback)
      applyTheme(fallback)
      setLoading(false)
      setError(null)
      return
    }

    const cached = initialTheme(userId)
    setSettings(cached)
    applyTheme(cached)
    setLoading(true)
    setError(null)

    void fetchThemeSettings()
      .then((remote) => {
        if (requestTokenRef.current !== token) {
          return
        }
        const next = coerceRemoteTheme(remote, cached)
        setSettings(next)
        applyTheme(next)
        writeCachedTheme(userId, next)
        setLoading(false)
        if (!isThemeSettings(remote)) {
          setError('主题设置无效，已回退本地缓存或默认模板')
        }
      })
      .catch((fetchError: unknown) => {
        if (requestTokenRef.current !== token) {
          return
        }
        setError(errorMessage(fetchError))
        setLoading(false)
      })

    return () => {
      requestTokenRef.current += 1
    }
  }, [userId])

  const save = useCallback(
    async (draft: ThemeSettings) => {
      // Bump the shared operation sequence so a late GET cannot overwrite this save.
      const token = ++requestTokenRef.current
      const activeUserId = userId
      const saved = await saveThemeSettings(draft)

      if (requestTokenRef.current !== token) {
        return saved
      }

      const next = coerceRemoteTheme(saved, draft)
      setSettings(next)
      applyTheme(next)
      if (activeUserId) {
        writeCachedTheme(activeUserId, next)
      }
      setError(isThemeSettings(saved) ? null : '主题设置无效，已保留当前草稿色值')
      return next
    },
    [userId],
  )

  const value = useMemo<ThemeContextValue>(() => ({ settings, loading, error, save }), [settings, loading, error, save])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme must be used within ThemeProvider')
  }
  return context
}
