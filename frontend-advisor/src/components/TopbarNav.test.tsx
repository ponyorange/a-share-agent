import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it } from 'vitest'
import TopbarNav, { BASE_NAV_LINKS } from './TopbarNav'

it('横向展示模块，并可用全部菜单切换', async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/portfolio']}>
      <TopbarNav links={BASE_NAV_LINKS} ariaLabel="基础导航" />
    </MemoryRouter>,
  )

  expect(screen.getByRole('navigation', { name: '基础导航' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '我的收藏' })).toHaveAttribute(
    'href',
    '/watchlist',
  )

  await user.click(screen.getByRole('button', { name: '全部' }))
  const menu = screen.getByRole('menu', { name: '全部功能模块' })
  expect(menu).toBeInTheDocument()
  expect(
    screen.getByRole('menuitem', { name: '我的持仓' }),
  ).toHaveAttribute('href', '/portfolio')
})
