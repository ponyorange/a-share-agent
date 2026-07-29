import '@testing-library/jest-dom/vitest'
import { createRef } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { AgentConversationDrawer } from './AgentConversationDrawer'
import type { AgentSession } from '../agentApi'

const sessions: AgentSession[] = [
  { session_id: 's-1', title: '旧会话', message_count: 3 },
  { session_id: 's-2', title: '', message_count: 0 },
]

function renderDrawer(props?: Partial<Parameters<typeof AgentConversationDrawer>[0]>) {
  const triggerRef = createRef<HTMLButtonElement>()
  const onClose = vi.fn()
  const onNew = vi.fn()
  const onOpen = vi.fn()
  const onDelete = vi.fn()

  const view = render(
    <>
      <button ref={triggerRef} type="button">
        打开记录
      </button>
      <AgentConversationDrawer
        open
        sessions={sessions}
        activeSessionId="s-1"
        disabled={false}
        hasMore={false}
        triggerRef={triggerRef}
        onClose={onClose}
        onNew={onNew}
        onOpen={onOpen}
        onDelete={onDelete}
        {...props}
      />
    </>,
  )

  return { ...view, triggerRef, onClose, onNew, onOpen, onDelete }
}

it('打开时聚焦关闭按钮并通过 Escape 或遮罩关闭', async () => {
  const user = userEvent.setup()
  const { onClose } = renderDrawer()

  const closeButton = screen.getByRole('button', { name: '关闭对话记录' })
  await waitFor(() => expect(closeButton).toHaveFocus())

  await user.keyboard('{Escape}')
  expect(onClose).toHaveBeenCalledTimes(1)

  await user.click(screen.getByTestId('agent-drawer-backdrop'))
  expect(onClose).toHaveBeenCalledTimes(2)
})

it('关闭卸载后把焦点还原到 triggerRef', async () => {
  const triggerRef = createRef<HTMLButtonElement>()
  const onClose = vi.fn()
  const { rerender } = render(
    <>
      <button ref={triggerRef} type="button">
        打开记录
      </button>
      <AgentConversationDrawer
        open
        sessions={sessions}
        activeSessionId="s-1"
        disabled={false}
        hasMore={false}
        triggerRef={triggerRef}
        onClose={onClose}
        onNew={vi.fn()}
        onOpen={vi.fn()}
        onDelete={vi.fn()}
      />
    </>,
  )

  await waitFor(() => expect(screen.getByRole('button', { name: '关闭对话记录' })).toHaveFocus())

  rerender(
    <>
      <button ref={triggerRef} type="button">
        打开记录
      </button>
      <AgentConversationDrawer
        open={false}
        sessions={sessions}
        activeSessionId="s-1"
        disabled={false}
        hasMore={false}
        triggerRef={triggerRef}
        onClose={onClose}
        onNew={vi.fn()}
        onOpen={vi.fn()}
        onDelete={vi.fn()}
      />
    </>,
  )

  expect(triggerRef.current).toHaveFocus()
})

it('Tab 和 Shift+Tab 在抽屉首尾焦点间循环', async () => {
  const user = userEvent.setup()
  renderDrawer()

  const closeButton = screen.getByRole('button', { name: '关闭对话记录' })
  const lastButton = screen.getByRole('button', { name: '删除 对话' })
  await waitFor(() => expect(closeButton).toHaveFocus())

  await user.keyboard('{Shift>}{Tab}{/Shift}')
  expect(lastButton).toHaveFocus()

  closeButton.focus()
  await user.keyboard('{Shift>}{Tab}{/Shift}')
  expect(lastButton).toHaveFocus()

  lastButton.focus()
  await user.keyboard('{Tab}')
  expect(closeButton).toHaveFocus()
})

it('展示会话并把打开、删除和新对话动作交给回调', async () => {
  const user = userEvent.setup()
  const { onNew, onOpen, onDelete } = renderDrawer()

  const dialog = screen.getByRole('dialog', { name: '对话记录' })
  expect(dialog).toHaveAttribute('aria-modal', 'true')
  expect(screen.getByText('旧会话')).toBeInTheDocument()
  expect(screen.getByText('3 条')).toBeInTheDocument()
  expect(screen.getByText('对话')).toBeInTheDocument()
  expect(screen.getByText('没有更早对话')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '新对话' }))
  expect(onNew).toHaveBeenCalledTimes(1)

  await user.click(screen.getByRole('button', { name: '打开 旧会话' }))
  expect(onOpen).toHaveBeenCalledWith('s-1')

  await user.click(screen.getByRole('button', { name: '删除 旧会话' }))
  expect(onDelete).toHaveBeenCalledWith('s-1')
})

it('滚近底部时请求加载更早会话', async () => {
  const onLoadMore = vi.fn()
  renderDrawer({ hasMore: true, onLoadMore })

  const scroller = document.querySelector('.agent-session-scroll') as HTMLElement
  expect(scroller).toBeTruthy()
  Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 400 })
  Object.defineProperty(scroller, 'clientHeight', { configurable: true, value: 100 })
  Object.defineProperty(scroller, 'scrollTop', { configurable: true, value: 360 })
  scroller.dispatchEvent(new Event('scroll', { bubbles: true }))
  expect(onLoadMore).toHaveBeenCalled()
})
