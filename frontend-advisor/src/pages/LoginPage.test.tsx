// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import LoginPage from './LoginPage'

vi.mock('../auth', () => ({
  clearSession: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
  setSession: vi.fn(),
  sendPasswordResetCode: vi.fn(),
  confirmPasswordReset: vi.fn(),
}))

it('登录页提供忘记密码入口', async () => {
  const user = userEvent.setup()
  render(<LoginPage onAuthed={vi.fn()} />)
  await user.click(screen.getByRole('button', { name: '忘记密码' }))
  expect(screen.getByRole('button', { name: '发送验证码' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '重置密码' })).toBeInTheDocument()
})
