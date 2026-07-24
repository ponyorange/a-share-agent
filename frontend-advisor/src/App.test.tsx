import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import App from './App'

const authState = vi.hoisted(() => ({ token: null as string | null }))

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

vi.mock('./committee/CommitteePage', () => ({
  default: () => <h1>投委会实时工作台</h1>,
}))

vi.mock('./pages/KnowledgePage', () => ({
  default: () => <h1>知识库</h1>,
}))

beforeEach(() => {
  localStorage.clear()
  authState.token = null
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

it('Agent 导航包含知识库且不含投委会；直链投委会仍可用', () => {
  const { container } = render(
    <MemoryRouter initialEntries={['/agent/committee']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.queryByRole('link', { name: '投委会' })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: '知识库' })).toHaveAttribute(
    'href',
    '/agent/knowledge',
  )
  expect(screen.getByRole('heading', { name: '投委会实时工作台' })).toBeInTheDocument()
  expect(container.querySelector('.app-shell')).toHaveClass('app-shell--agent-chat')
})

it('知识库路由渲染知识库页且非 chat shell', () => {
  const { container } = render(
    <MemoryRouter initialEntries={['/agent/knowledge']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('heading', { name: '知识库' })).toBeInTheDocument()
  expect(container.querySelector('.app-shell')).toHaveClass('app-shell--agent')
  expect(container.querySelector('.app-shell')).not.toHaveClass('app-shell--agent-chat')
})
