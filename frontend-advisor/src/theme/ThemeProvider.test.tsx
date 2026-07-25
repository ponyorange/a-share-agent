// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { THEME_TEMPLATES, type ThemeSettings } from './theme'
import { ThemeProvider, useTheme } from './ThemeProvider'

const fetchThemeSettings = vi.hoisted(() => vi.fn())
const saveThemeSettings = vi.hoisted(() => vi.fn())

vi.mock('./themeApi', () => ({ fetchThemeSettings, saveThemeSettings }))

function Harness() {
  const theme = useTheme()

  return (
    <>
      <span>{theme.settings.active_template}</span>
      <span>{theme.loading ? 'loading' : 'idle'}</span>
      <span>{theme.error ?? 'no-error'}</span>
      <button
        onClick={() => {
          void theme.save(THEME_TEMPLATES.classic_market).catch(() => undefined)
        }}
      >
        保存
      </button>
    </>
  )
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('style')
  delete document.documentElement.dataset.themeTemplate
  fetchThemeSettings.mockReset()
  saveThemeSettings.mockReset()
})

afterEach(() => {
  cleanup()
})

it('先用缓存，再由服务端覆盖并写回缓存', async () => {
  localStorage.setItem('advisor_theme:u1', JSON.stringify(THEME_TEMPLATES.classic_market))
  fetchThemeSettings.mockResolvedValue(THEME_TEMPLATES.modern_data)

  render(
    <ThemeProvider userId="u1">
      <Harness />
    </ThemeProvider>,
  )

  expect(screen.getByText('classic_market')).toBeInTheDocument()
  await screen.findByText('modern_data')
  expect(document.documentElement.style.getPropertyValue('--color-brand')).toBe('#6673D9')
  expect(localStorage.getItem('advisor_theme:u1')).toBe(JSON.stringify(THEME_TEMPLATES.modern_data))
})

it('保存失败时保持原已应用主题', async () => {
  const user = userEvent.setup()
  fetchThemeSettings.mockResolvedValue(THEME_TEMPLATES.modern_data)
  saveThemeSettings.mockRejectedValue(new Error('网络错误'))

  render(
    <ThemeProvider userId="u1">
      <Harness />
    </ThemeProvider>,
  )

  await screen.findByText('modern_data')
  await user.click(screen.getByRole('button', { name: '保存' }))

  await waitFor(() => expect(screen.getByText('modern_data')).toBeInTheDocument())
  expect(document.documentElement.style.getPropertyValue('--color-brand')).toBe('#6673D9')
  expect(localStorage.getItem('advisor_theme:u1')).toBe(JSON.stringify(THEME_TEMPLATES.modern_data))
})

it('保存请求完成前不提前应用草稿主题', async () => {
  const user = userEvent.setup()
  let resolveSave: (settings: ThemeSettings) => void = () => undefined
  fetchThemeSettings.mockResolvedValue(THEME_TEMPLATES.modern_data)
  saveThemeSettings.mockImplementation(
    () =>
      new Promise<ThemeSettings>((resolve) => {
        resolveSave = resolve
      }),
  )

  render(
    <ThemeProvider userId="u1">
      <Harness />
    </ThemeProvider>,
  )

  await screen.findByText('modern_data')
  await user.click(screen.getByRole('button', { name: '保存' }))
  expect(document.documentElement.dataset.themeTemplate).toBe('modern_data')

  await act(async () => {
    resolveSave(THEME_TEMPLATES.classic_market)
  })

  await screen.findByText('classic_market')
  expect(document.documentElement.dataset.themeTemplate).toBe('classic_market')
})

it('退出账号后恢复现代数据默认主题且不请求后端', async () => {
  fetchThemeSettings.mockResolvedValue(THEME_TEMPLATES.classic_market)
  const view = render(
    <ThemeProvider userId="u1">
      <Harness />
    </ThemeProvider>,
  )
  await screen.findByText('classic_market')
  fetchThemeSettings.mockClear()

  view.rerender(
    <ThemeProvider userId={null}>
      <Harness />
    </ThemeProvider>,
  )

  expect(await screen.findByText('modern_data')).toBeInTheDocument()
  expect(fetchThemeSettings).not.toHaveBeenCalled()
  expect(document.documentElement.dataset.themeTemplate).toBe('modern_data')
})

it('忽略旧用户迟到的主题响应', async () => {
  const resolvers: Array<(settings: ThemeSettings) => void> = []
  fetchThemeSettings.mockImplementation(
    () =>
      new Promise<ThemeSettings>((resolve) => {
        resolvers.push(resolve)
      }),
  )

  const view = render(
    <ThemeProvider userId="u1">
      <Harness />
    </ThemeProvider>,
  )
  view.rerender(
    <ThemeProvider userId="u2">
      <Harness />
    </ThemeProvider>,
  )

  await act(async () => {
    resolvers[1](THEME_TEMPLATES.classic_market)
  })
  await screen.findByText('classic_market')

  await act(async () => {
    resolvers[0](THEME_TEMPLATES.modern_data)
  })

  await waitFor(() => expect(screen.getByText('classic_market')).toBeInTheDocument())
  expect(localStorage.getItem('advisor_theme:u1')).toBeNull()
})

it('忽略保存后迟到的旧加载响应', async () => {
  const user = userEvent.setup()
  let resolveFetch: (settings: ThemeSettings) => void = () => undefined
  fetchThemeSettings.mockImplementation(
    () =>
      new Promise<ThemeSettings>((resolve) => {
        resolveFetch = resolve
      }),
  )
  saveThemeSettings.mockResolvedValue(THEME_TEMPLATES.classic_market)

  render(
    <ThemeProvider userId="u1">
      <Harness />
    </ThemeProvider>,
  )

  await user.click(screen.getByRole('button', { name: '保存' }))
  await screen.findByText('classic_market')

  await act(async () => {
    resolveFetch(THEME_TEMPLATES.modern_data)
  })

  await waitFor(() => expect(screen.getByText('classic_market')).toBeInTheDocument())
  expect(document.documentElement.dataset.themeTemplate).toBe('classic_market')
  expect(localStorage.getItem('advisor_theme:u1')).toBe(JSON.stringify(THEME_TEMPLATES.classic_market))
})

it('远端非法主题时回退缓存或默认模板', async () => {
  localStorage.setItem('advisor_theme:u1', JSON.stringify(THEME_TEMPLATES.classic_market))
  fetchThemeSettings.mockResolvedValue({ active_template: 'modern_data', colors: { brand: '#123456' } })

  render(
    <ThemeProvider userId="u1">
      <Harness />
    </ThemeProvider>,
  )

  expect(screen.getByText('classic_market')).toBeInTheDocument()
  expect(await screen.findByText(/主题设置无效/)).toBeInTheDocument()
  expect(document.documentElement.dataset.themeTemplate).toBe('classic_market')
  expect(localStorage.getItem('advisor_theme:u1')).toBe(JSON.stringify(THEME_TEMPLATES.classic_market))
})
