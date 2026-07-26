// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import * as auth from '../auth'
import AccountPage from './AccountPage'

vi.mock('../auth', async () => {
  const actual = await vi.importActual<typeof import('../auth')>('../auth')
  return {
    ...actual,
    getToken: () => 'token',
    getUser: () => ({ id: 'u1', username: 'tester', email: null, email_verified: false }),
    fetchMe: vi.fn(),
    setSession: vi.fn(),
    sendEmailBindCode: vi.fn(),
    verifyEmailBind: vi.fn(),
    changePassword: vi.fn(),
  }
})

beforeEach(() => {
  vi.mocked(auth.fetchMe).mockResolvedValue({
    user: { id: 'u1', username: 'tester', email: null, email_verified: false },
  })
})

it('渲染资料页邮箱与改密区块', async () => {
  render(
    <MemoryRouter>
      <AccountPage />
    </MemoryRouter>,
  )
  expect(await screen.findByRole('heading', { name: '账号' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '邮箱' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '修改密码' })).toBeInTheDocument()
  await waitFor(() => expect(auth.fetchMe).toHaveBeenCalled())
})
