// @vitest-environment jsdom

import { beforeEach, expect, it } from 'vitest'
import {
  THEME_TEMPLATES,
  applyTheme,
  getContrastWarnings,
  isThemeSettings,
  normalizeHex,
} from './theme'
import { bootstrapTheme, readCachedTheme, writeCachedTheme } from './themeStorage'

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('style')
  delete document.documentElement.dataset.themeTemplate
})

it('三套模板都有且仅有十个基础语义色', () => {
  const expected = [
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
  ].sort()
  for (const id of ['modern_data', 'classic_market', 'deep_navy'] as const) {
    expect(Object.keys(THEME_TEMPLATES[id].colors).sort()).toEqual(expected)
  }
})

it('规范化颜色并拒绝非法值', () => {
  expect(normalizeHex('#abcdef')).toBe('#ABCDEF')
  expect(normalizeHex('abcdef')).toBeNull()
  expect(normalizeHex('#abcd')).toBeNull()
})

it('应用基础色和派生柔和色', () => {
  applyTheme(THEME_TEMPLATES.modern_data)
  const root = document.documentElement.style
  expect(root.getPropertyValue('--color-market-up')).toBe('#3568B8')
  expect(root.getPropertyValue('--color-market-up-soft')).toBe('rgba(53, 104, 184, 0.14)')
  expect(root.getPropertyValue('--color-surface-muted')).toBe('rgb(238, 239, 244)')
  expect(document.documentElement.dataset.themeTemplate).toBe('modern_data')
})

it('应用深海蓝时写入 surface-muted 派生色', () => {
  applyTheme(THEME_TEMPLATES.deep_navy)
  const root = document.documentElement.style
  expect(root.getPropertyValue('--color-page-bg')).toBe('#101724')
  expect(root.getPropertyValue('--color-surface-muted')).toBe('rgb(21, 30, 45)')
  expect(document.documentElement.dataset.themeTemplate).toBe('deep_navy')
})

it('低对比度只产生警告', () => {
  const colors = {
    ...THEME_TEMPLATES.modern_data.colors,
    text_primary: '#F6F7FB',
  }
  expect(getContrastWarnings(colors).some((item) => item.field === 'text_primary')).toBe(true)
})

it('缓存按用户隔离', () => {
  writeCachedTheme('u1', THEME_TEMPLATES.modern_data)
  writeCachedTheme('u2', THEME_TEMPLATES.classic_market)
  expect(readCachedTheme('u1')?.active_template).toBe('modern_data')
  expect(readCachedTheme('u2')?.active_template).toBe('classic_market')
})

it('拒绝非法缓存结构', () => {
  localStorage.setItem(
    'advisor_theme:u1',
    JSON.stringify({
      active_template: 'modern_data',
      colors: { ...THEME_TEMPLATES.modern_data.colors, extra: '#FFFFFF' },
    }),
  )
  expect(readCachedTheme('u1')).toBeNull()
})

it('验证主题设置结构和颜色格式', () => {
  expect(isThemeSettings(THEME_TEMPLATES.modern_data)).toBe(true)
  expect(
    isThemeSettings({
      active_template: 'modern_data',
      colors: { ...THEME_TEMPLATES.modern_data.colors, brand: 'red' },
    }),
  ).toBe(false)
})

it('启动时优先应用用户缓存，否则使用现代模板', () => {
  writeCachedTheme('u1', THEME_TEMPLATES.classic_market)
  bootstrapTheme('u1')
  expect(document.documentElement.dataset.themeTemplate).toBe('classic_market')

  bootstrapTheme()
  expect(document.documentElement.dataset.themeTemplate).toBe('modern_data')
})
