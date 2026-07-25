// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { THEME_TEMPLATES, type ThemeSettings } from '../theme/theme'
import SettingsPage from './SettingsPage'

const save = vi.hoisted(() => vi.fn())
let currentSettings: ThemeSettings

vi.mock('../theme/ThemeProvider', () => ({
  useTheme: () => ({
    settings: currentSettings,
    loading: false,
    error: null,
    save,
  }),
}))

beforeEach(() => {
  currentSettings = THEME_TEMPLATES.modern_data
  document.documentElement.removeAttribute('style')
  save.mockReset()
  save.mockImplementation(async (draft) => draft)
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
})

it('编辑色值后只在保存时提交完整主题', async () => {
  const user = userEvent.setup()
  render(<SettingsPage />)
  const brand = screen.getByLabelText('品牌主色（十六进制）')

  await user.clear(brand)
  await user.type(brand, '#123456')

  expect(save).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '保存并应用' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      active_template: 'modern_data',
      colors: expect.objectContaining({ brand: '#123456' }),
    }),
  )
})

it('低对比度警告不阻止保存', async () => {
  const user = userEvent.setup()
  render(<SettingsPage />)
  const text = screen.getByLabelText('主文字（十六进制）')

  await user.clear(text)
  await user.type(text, '#F6F7FB')

  expect(screen.getByText(/对比度提醒/)).toBeInTheDocument()
  expect(screen.getAllByText(/与 卡片背景 对比度/).length).toBeGreaterThan(0)
  await user.click(screen.getByRole('button', { name: '保存并应用' }))
  expect(save).toHaveBeenCalled()
})

it('非法十六进制值阻止保存，恢复按钮恢复当前模板', async () => {
  const user = userEvent.setup()
  render(<SettingsPage />)
  const brand = screen.getByLabelText('品牌主色（十六进制）')

  await user.clear(brand)
  await user.type(brand, 'blue')

  expect(screen.getByRole('alert')).toHaveTextContent('请输入 #RRGGBB 格式')
  expect(screen.getByRole('button', { name: '保存并应用' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: '恢复模板默认值' }))
  expect(brand).toHaveValue('#6673D9')
})

it('切换模板会确认并覆盖草稿', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<SettingsPage />)
  const brand = screen.getByLabelText('品牌主色（十六进制）')

  await user.clear(brand)
  await user.type(brand, '#123456')
  await user.click(screen.getByRole('radio', { name: '经典行情' }))

  expect(confirm).toHaveBeenCalledWith('切换模板会覆盖当前未保存的配色，继续吗？')
  expect(screen.getByLabelText('品牌主色（十六进制）')).toHaveValue('#526FC1')
})

it('可选择深海蓝并恢复其默认色', async () => {
  const user = userEvent.setup()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<SettingsPage />)

  await user.click(screen.getByRole('radio', { name: '深海蓝' }))

  expect(screen.getByLabelText('页面背景（十六进制）')).toHaveValue('#101724')
  expect(screen.getByTestId('theme-preview')).toHaveStyle({
    '--color-page-bg': '#101724',
    '--color-surface': '#192335',
    '--color-brand': '#8793FF',
  })

  await user.clear(screen.getByLabelText('品牌主色（十六进制）'))
  await user.type(screen.getByLabelText('品牌主色（十六进制）'), '#123456')
  await user.click(screen.getByRole('button', { name: '恢复模板默认值' }))

  expect(screen.getByLabelText('品牌主色（十六进制）')).toHaveValue('#8793FF')
  await user.click(screen.getByRole('button', { name: '保存并应用' }))
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({
      active_template: 'deep_navy',
      colors: THEME_TEMPLATES.deep_navy.colors,
    }),
  )
})

it('切换模板后的未保存草稿不会被迟到的 provider 设置覆盖', async () => {
  const user = userEvent.setup()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const { rerender } = render(<SettingsPage />)

  await user.click(screen.getByRole('radio', { name: '深海蓝' }))
  expect(screen.getByRole('radio', { name: '深海蓝' })).toBeChecked()
  expect(screen.getByLabelText('页面背景（十六进制）')).toHaveValue('#101724')

  currentSettings = THEME_TEMPLATES.classic_market
  rerender(<SettingsPage />)

  expect(screen.getByRole('radio', { name: '深海蓝' })).toBeChecked()
  expect(screen.getByLabelText('页面背景（十六进制）')).toHaveValue('#101724')
})

it('脏草稿取消切换模板时保留当前编辑', async () => {
  const user = userEvent.setup()
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  render(<SettingsPage />)
  const brand = screen.getByLabelText('品牌主色（十六进制）')

  await user.clear(brand)
  await user.type(brand, '#123456')
  await user.click(screen.getByRole('radio', { name: '经典行情' }))

  expect(screen.getByRole('radio', { name: '现代数据' })).toBeChecked()
  expect(brand).toHaveValue('#123456')
})

it('预览使用局部变量且不提前写入根主题变量', async () => {
  const user = userEvent.setup()
  render(<SettingsPage />)
  const brand = screen.getByLabelText('品牌主色（十六进制）')

  await user.clear(brand)
  await user.type(brand, '#123456')

  expect(screen.getByTestId('theme-preview')).toHaveStyle({ '--color-brand': '#123456' })
  expect(document.documentElement.style.getPropertyValue('--color-brand')).toBe('')
})

it('预览展示涨跌与成功错误状态', () => {
  render(<SettingsPage />)
  const preview = screen.getByTestId('theme-preview')
  expect(preview.querySelector('.up')).toHaveTextContent('+3.8%')
  expect(preview.querySelector('.down')).toHaveTextContent('−1.6%')
  expect(preview.querySelector('.status.ok')).toHaveTextContent('保存成功')
  expect(preview.querySelector('.status.error')).toHaveTextContent('数据异常')
})

it('深海蓝模板卡片展示品牌/涨/跌色块', () => {
  render(<SettingsPage />)
  const colors = THEME_TEMPLATES.deep_navy.colors
  const brand = screen.getByLabelText('深海蓝品牌色')
  const up = screen.getByLabelText('深海蓝上涨色')
  const down = screen.getByLabelText('深海蓝下跌色')

  expect(brand).toHaveStyle({ backgroundColor: colors.brand })
  expect(up).toHaveStyle({ backgroundColor: colors.market_up })
  expect(down).toHaveStyle({ backgroundColor: colors.market_down })
})
