// @vitest-environment jsdom

import type { ReactNode } from 'react'
import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import * as auth from './auth'
import App from './App'

const authState = vi.hoisted(() => ({ token: null as string | null }))
const themeProviderUserIds = vi.hoisted(() => [] as Array<string | null>)
const bootstrapTheme = vi.hoisted(() => vi.fn())

vi.mock('./auth', () => ({
  AUTH_CHANGED_EVENT: 'advisor-auth-changed',
  getToken: () => authState.token,
  getUser: () => ({ id: 'u1', username: 'tester' }),
  fetchMe: vi.fn(),
  clearSession: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  setSession: vi.fn(),
}))

vi.mock('./theme/ThemeProvider', () => ({
  ThemeProvider: ({ userId, children }: { userId: string | null; children: ReactNode }) => {
    themeProviderUserIds.push(userId)
    return children
  },
}))

vi.mock('./theme/themeStorage', () => ({
  bootstrapTheme,
}))

vi.mock('./committee/CommitteePage', () => ({
  default: () => <h1>投委会实时工作台</h1>,
}))

vi.mock('./pages/AgentChatPage', () => ({
  default: () => <h1>投研助手</h1>,
}))

vi.mock('./pages/KnowledgePage', () => ({
  default: () => <h1>Agent 配置</h1>,
}))

vi.mock('./pages/SettingsPage', () => ({
  default: () => <h1>配色设置</h1>,
}))

vi.mock('./pages/HomePage', () => ({
  default: () => <h1>市场首页</h1>,
}))

vi.mock('./pages/RecommendationsPage', () => ({
  default: () => <h1>今日关注页</h1>,
}))

beforeEach(() => {
  localStorage.clear()
  authState.token = null
  themeProviderUserIds.length = 0
  bootstrapTheme.mockClear()
  vi.mocked(auth.fetchMe).mockResolvedValue({
    user: { id: 'u1', username: 'tester' },
  })
})

it('收到统一认证变更事件后立即返回登录页', async () => {
  render(
    <MemoryRouter initialEntries={['/agent/committee']}>
      <App />
    </MemoryRouter>,
  )
  window.dispatchEvent(new Event('advisor-auth-changed'))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument(),
  )
  expect(screen.queryByRole('link', { name: '投委会' })).not.toBeInTheDocument()
})

it('Agent 导航包含 Agent 配置且不含投委会；直链投委会仍可用', () => {
  const { container } = render(
    <MemoryRouter initialEntries={['/agent/committee']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.queryByRole('link', { name: '投委会' })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Agent 配置' })).toHaveAttribute(
    'href',
    '/agent/config',
  )
  expect(screen.getByRole('heading', { name: '投委会实时工作台' })).toBeInTheDocument()
  expect(container.querySelector('.app-shell')).toHaveClass('app-shell--agent-chat')
})

it('Agent 配置路由渲染配置页且非 chat shell', () => {
  const { container } = render(
    <MemoryRouter initialEntries={['/agent/config']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: 'Agent 配置' })).toBeInTheDocument()
  expect(container.querySelector('.app-shell')).toHaveClass('app-shell--agent')
  expect(container.querySelector('.app-shell')).not.toHaveClass('app-shell--agent-chat')
})

it('旧知识库路径重定向到 Agent 配置', () => {
  render(
    <MemoryRouter initialEntries={['/agent/knowledge']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: 'Agent 配置' })).toBeInTheDocument()
})

it('认证后的基础页与 Agent chat 保留导航和面板切换结构', () => {
  const baseView = render(
    <MemoryRouter initialEntries={['/']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('link', { name: '模拟盘' })).toHaveAttribute('href', '/paper')
  expect(screen.queryByRole('link', { name: '交易员' })).not.toBeInTheDocument()
  expect(baseView.container.querySelector('.topbar-nav-wrap .nav')).toBeInTheDocument()
  baseView.unmount()

  const agentView = render(
    <MemoryRouter initialEntries={['/agent']}>
      <App />
    </MemoryRouter>,
  )
  expect(agentView.container.querySelector('.app-shell')).toHaveClass('app-shell--agent-chat')
  expect(agentView.container.querySelector('.app-shell')).toHaveClass(
    'app-shell--agent-chat-page',
  )
  expect(agentView.container.querySelector('.topbar-nav-wrap .nav')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: '基础' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Agent' })).toBeInTheDocument()
})

it('Agent 聊天页提供更多菜单可切换基础面板与 Agent 标签', async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/agent']}>
      <App />
    </MemoryRouter>,
  )

  await user.click(screen.getByRole('button', { name: '更多' }))
  expect(screen.getByRole('menu', { name: '页面与面板切换' })).toBeInTheDocument()
  expect(screen.getByRole('menuitem', { name: '切换到基础' })).toBeInTheDocument()
  expect(screen.getByRole('menuitem', { name: 'Agent 配置' })).toHaveAttribute(
    'href',
    '/agent/config',
  )
  expect(screen.getByRole('menuitem', { name: '策略副驾' })).toHaveAttribute(
    'href',
    '/agent/strategy',
  )
  expect(screen.getByRole('menuitem', { name: 'DeepSeek 配置' })).toHaveAttribute(
    'href',
    '/agent/settings',
  )

  await user.click(screen.getByRole('menuitem', { name: 'Agent 配置' }))
  expect(await screen.findByRole('heading', { name: 'Agent 配置' })).toBeInTheDocument()
})

it('Agent 聊天页更多菜单可切回基础面板', async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/agent']}>
      <App />
    </MemoryRouter>,
  )

  await user.click(screen.getByRole('button', { name: '更多' }))
  await user.click(screen.getByRole('menuitem', { name: '切换到基础' }))
  expect(await screen.findByRole('heading', { name: '市场首页' })).toBeInTheDocument()
  expect(await screen.findByRole('link', { name: '今日关注' })).toHaveAttribute(
    'href',
    '/recommendations',
  )
  expect(screen.queryByRole('button', { name: '更多' })).not.toBeInTheDocument()
})

it('基础导航提供设置入口并渲染设置路由', () => {
  render(
    <MemoryRouter initialEntries={['/settings']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('link', { name: '设置' })).toHaveAttribute('href', '/settings')
  expect(screen.getByRole('heading', { name: '配色设置' })).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: 'Agent 导航' })).not.toBeInTheDocument()
})

it('顶栏用户名进入个人资料页', async () => {
  authState.token = 't'
  render(
    <MemoryRouter initialEntries={['/']}>
      <App />
    </MemoryRouter>,
  )
  expect(await screen.findByRole('link', { name: 'tester' })).toHaveAttribute(
    'href',
    '/account',
  )
})

it('ThemeProvider 包裹登录态和退出后的应用树', async () => {
  render(
    <MemoryRouter initialEntries={['/']}>
      <App />
    </MemoryRouter>,
  )

  expect(themeProviderUserIds).toContain('u1')

  window.dispatchEvent(new Event('advisor-auth-changed'))

  await waitFor(() => expect(themeProviderUserIds).toContain(null))
  expect(screen.getByRole('button', { name: '登录' })).toBeInTheDocument()
})

it('入口初始化主题时使用当前账号 id', async () => {
  vi.resetModules()
  vi.doMock('react-dom/client', () => ({
    createRoot: () => ({ render: vi.fn() }),
  }))
  document.body.innerHTML = '<div id="root"></div>'

  const main = await import('./main')
  bootstrapTheme.mockClear()
  main.initializeTheme()

  expect(bootstrapTheme).toHaveBeenCalledWith('u1')
})
