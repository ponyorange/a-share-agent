import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { StarToggle } from './StarToggle'

it('点击触发 onToggle 取反', async () => {
  const user = userEvent.setup()
  const onToggle = vi.fn()
  render(<StarToggle symbol="510300" starred={false} onToggle={onToggle} />)
  await user.click(screen.getByRole('button', { name: '收藏 510300' }))
  expect(onToggle).toHaveBeenCalledWith(true)
})

it('已收藏时文案为取消收藏', () => {
  render(<StarToggle symbol="510300" starred onToggle={() => undefined} />)
  expect(screen.getByRole('button', { name: '取消收藏 510300' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
})
