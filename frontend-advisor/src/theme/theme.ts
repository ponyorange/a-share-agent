export type ThemeId = 'modern_data' | 'classic_market' | 'deep_navy'

export type ThemeColors = {
  page_bg: string
  surface: string
  text_primary: string
  text_muted: string
  border: string
  brand: string
  market_up: string
  market_down: string
  success: string
  error: string
}

export type ThemeSettings = {
  active_template: ThemeId
  colors: ThemeColors
  updated_at?: string | null
}

export type ContrastWarning = {
  field: keyof ThemeColors
  against: keyof ThemeColors
  ratio: number
  minimum: number
}

const COLOR_KEYS = [
  'page_bg',
  'surface',
  'text_primary',
  'text_muted',
  'border',
  'brand',
  'market_up',
  'market_down',
  'success',
  'error',
] as const satisfies readonly (keyof ThemeColors)[]

const SOFT_COLOR_KEYS = ['brand', 'market_up', 'market_down', 'success', 'error'] as const satisfies readonly (keyof ThemeColors)[]
const HEX_RE = /^#[0-9A-Fa-f]{6}$/
const SOFT_ALPHA = 0.14

const freezeTemplate = (settings: ThemeSettings): ThemeSettings =>
  Object.freeze({
    ...settings,
    colors: Object.freeze({ ...settings.colors }),
  })

export const THEME_TEMPLATES = Object.freeze({
  modern_data: freezeTemplate({
    active_template: 'modern_data',
    colors: {
      page_bg: '#F6F7FB',
      surface: '#FFFFFF',
      text_primary: '#273247',
      text_muted: '#778195',
      border: '#E5E8F1',
      brand: '#6673D9',
      market_up: '#3568B8',
      market_down: '#A96918',
      success: '#377659',
      error: '#A84C5B',
    },
    updated_at: null,
  }),
  classic_market: freezeTemplate({
    active_template: 'classic_market',
    colors: {
      page_bg: '#F7F8FA',
      surface: '#FFFFFF',
      text_primary: '#2A3140',
      text_muted: '#6F7A8C',
      border: '#E4E7ED',
      brand: '#526FC1',
      market_up: '#C24B5A',
      market_down: '#328268',
      success: '#2F7A5B',
      error: '#B54759',
    },
    updated_at: null,
  }),
  deep_navy: freezeTemplate({
    active_template: 'deep_navy',
    colors: {
      page_bg: '#101724',
      surface: '#192335',
      text_primary: '#F2F5FA',
      text_muted: '#99A7BB',
      border: '#303E55',
      brand: '#8793FF',
      market_up: '#70A9F8',
      market_down: '#F1B85B',
      success: '#61C28F',
      error: '#F17C8E',
    },
    updated_at: null,
  }),
} satisfies Record<ThemeId, ThemeSettings>)

export function normalizeHex(value: string): string | null {
  return HEX_RE.test(value) ? value.toUpperCase() : null
}

function cssVarName(key: keyof ThemeColors): string {
  return `--color-${key.replaceAll('_', '-')}`
}

function hexToRgb(hex: string): [number, number, number] {
  return [
    Number.parseInt(hex.slice(1, 3), 16),
    Number.parseInt(hex.slice(3, 5), 16),
    Number.parseInt(hex.slice(5, 7), 16),
  ]
}

function hexToRgba(hex: string, alpha: number): string {
  const [red, green, blue] = hexToRgb(normalizeHex(hex) ?? hex)
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`
}

function mixHex(a: string, b: string, weightTowardB: number): string {
  const [ar, ag, ab] = hexToRgb(normalizeHex(a) ?? a)
  const [br, bg, bb] = hexToRgb(normalizeHex(b) ?? b)
  const mix = (from: number, to: number) => Math.round(from * (1 - weightTowardB) + to * weightTowardB)

  return `rgb(${mix(ar, br)}, ${mix(ag, bg)}, ${mix(ab, bb)})`
}

function surfaceMuted(colors: ThemeColors): string {
  if (luminance(colors.page_bg) >= 0.5) {
    return mixHex(colors.page_bg, colors.text_primary, 0.04)
  }

  return mixHex(colors.page_bg, colors.surface, 0.55)
}

export function applyTheme(settings: ThemeSettings): void {
  const root = document.documentElement

  for (const key of COLOR_KEYS) {
    const color = normalizeHex(settings.colors[key]) ?? settings.colors[key]
    root.style.setProperty(cssVarName(key), color)
  }

  for (const key of SOFT_COLOR_KEYS) {
    root.style.setProperty(`${cssVarName(key)}-soft`, hexToRgba(settings.colors[key], SOFT_ALPHA))
  }

  root.style.setProperty('--color-surface-muted', surfaceMuted(settings.colors))
  root.dataset.themeTemplate = settings.active_template
}

function linear(channel: number): number {
  const value = channel / 255
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const [red, green, blue] = hexToRgb(normalizeHex(hex) ?? hex)
  return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)
}

function contrast(a: string, b: string): number {
  const aLuminance = luminance(a)
  const bLuminance = luminance(b)
  return (Math.max(aLuminance, bLuminance) + 0.05) / (Math.min(aLuminance, bLuminance) + 0.05)
}

function warningIfLow(
  colors: ThemeColors,
  field: keyof ThemeColors,
  against: keyof ThemeColors,
  minimum: number,
): ContrastWarning | null {
  const ratio = contrast(colors[field], colors[against])
  return ratio < minimum ? { field, against, ratio, minimum } : null
}

export function getContrastWarnings(colors: ThemeColors): ContrastWarning[] {
  const checks: Array<[keyof ThemeColors, keyof ThemeColors, number]> = [
    ['text_primary', 'page_bg', 4.5],
    ['text_primary', 'surface', 4.5],
    ['text_muted', 'page_bg', 4.5],
    ['text_muted', 'surface', 4.5],
    ['brand', 'surface', 3],
    ['market_up', 'surface', 3],
    ['market_down', 'surface', 3],
    ['success', 'surface', 3],
    ['error', 'surface', 3],
  ]

  return checks.flatMap(([field, against, minimum]) => {
    const warning = warningIfLow(colors, field, against, minimum)
    return warning ? [warning] : []
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isThemeId(value: unknown): value is ThemeId {
  return value === 'modern_data' || value === 'classic_market' || value === 'deep_navy'
}

function isThemeColors(value: unknown): value is ThemeColors {
  if (!isRecord(value)) {
    return false
  }

  const keys = Object.keys(value)
  if (keys.length !== COLOR_KEYS.length || keys.some((key) => !COLOR_KEYS.includes(key as keyof ThemeColors))) {
    return false
  }

  return COLOR_KEYS.every((key) => typeof value[key] === 'string' && normalizeHex(value[key]) !== null)
}

export function isThemeSettings(value: unknown): value is ThemeSettings {
  if (!isRecord(value) || !isThemeId(value.active_template) || !isThemeColors(value.colors)) {
    return false
  }

  return value.updated_at === undefined || typeof value.updated_at === 'string' || value.updated_at === null
}
