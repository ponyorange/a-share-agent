import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { ChatBubble } from './AgentChatPage'

it('助手消息底部可复制正文', async () => {
  const user = userEvent.setup()
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })

  render(
    <ChatBubble
      m={{ role: 'assistant', content: '今日关注：银行板块。' }}
    />,
  )

  await user.click(screen.getByRole('button', { name: '复制' }))
  expect(writeText).toHaveBeenCalledWith('今日关注：银行板块。')
  expect(await screen.findByRole('button', { name: '已复制' })).toBeInTheDocument()
})

it('流式输出中不显示复制按钮', () => {
  render(
    <ChatBubble
      m={{ role: 'assistant', content: '半截回复', streaming: true }}
    />,
  )
  expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument()
})
