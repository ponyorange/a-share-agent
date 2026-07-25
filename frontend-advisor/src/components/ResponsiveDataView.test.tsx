import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ResponsiveDataView } from './ResponsiveDataView'

function setViewport(mobile: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: mobile,
      media: '(max-width: 768px)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

it('移动端默认卡片并保存表格偏好', async () => {
  setViewport(true)
  const user = userEvent.setup()
  const view = render(
    <ResponsiveDataView
      storageKey="test-view"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )
  expect(screen.getByText('卡片内容')).toBeInTheDocument()
  expect(screen.queryByText('表格内容')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '表格视图' }))
  expect(screen.getByText('表格内容')).toBeInTheDocument()
  expect(localStorage.getItem('test-view')).toBe('table')
  view.unmount()
  render(
    <ResponsiveDataView
      storageKey="test-view"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )
  expect(screen.getByText('表格内容')).toBeInTheDocument()
})

it('storageKey 变化时恢复对应偏好', () => {
  setViewport(true)
  localStorage.setItem('view-a', 'table')
  localStorage.setItem('view-b', 'card')
  const { rerender } = render(
    <ResponsiveDataView
      storageKey="view-a"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )
  expect(screen.getByText('表格内容')).toBeInTheDocument()

  rerender(
    <ResponsiveDataView
      storageKey="view-b"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )
  expect(screen.getByText('卡片内容')).toBeInTheDocument()
  expect(screen.queryByText('表格内容')).not.toBeInTheDocument()
})

it('桌面端忽略移动偏好并保持表格', () => {
  localStorage.setItem('test-view', 'card')
  setViewport(false)
  render(
    <ResponsiveDataView
      storageKey="test-view"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )
  expect(screen.getByText('表格内容')).toBeInTheDocument()
  expect(screen.queryByRole('group', { name: '候选视图' })).not.toBeInTheDocument()
})

it('localStorage 读取抛错时回退卡片且仍可切换', async () => {
  setViewport(true)
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('storage unavailable')
  })
  const user = userEvent.setup()

  render(
    <ResponsiveDataView
      storageKey="test-view"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )

  expect(screen.getByText('卡片内容')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '表格视图' }))
  expect(screen.getByText('表格内容')).toBeInTheDocument()
})

it('localStorage 写入抛错时仍可切换视图', async () => {
  setViewport(true)
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('storage unavailable')
  })
  const user = userEvent.setup()

  render(
    <ResponsiveDataView
      storageKey="test-view"
      label="候选"
      cards={<div>卡片内容</div>}
      table={<div>表格内容</div>}
    />,
  )

  await user.click(screen.getByRole('button', { name: '表格视图' }))
  expect(screen.getByText('表格内容')).toBeInTheDocument()
})
