import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { AgentComposer } from './AgentComposer'

function renderComposer(props?: Partial<Parameters<typeof AgentComposer>[0]>) {
  const onChange = vi.fn()
  const onSend = vi.fn()
  const view = render(
    <AgentComposer
      value="请分析组合"
      disabled={false}
      sending={false}
      error={null}
      onChange={onChange}
      onSend={onSend}
      {...props}
    />,
  )
  return { ...view, onChange, onSend }
}

it('输入变化调用 onChange', async () => {
  const user = userEvent.setup()
  const { onChange } = renderComposer({ value: '' })

  await user.type(screen.getByRole('textbox', { name: '给投研助手发送消息' }), '你好')

  expect(onChange).toHaveBeenLastCalledWith('好')
})

it('表单提交和 Enter 使用同一发送限制', async () => {
  const user = userEvent.setup()
  const { onSend } = renderComposer()
  const textbox = screen.getByRole('textbox', { name: '给投研助手发送消息' })

  await user.click(screen.getByRole('button', { name: '发送' }))
  expect(onSend).toHaveBeenCalledTimes(1)

  const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
  textbox.dispatchEvent(enter)

  expect(enter.defaultPrevented).toBe(true)
  expect(onSend).toHaveBeenCalledTimes(2)
})

it('Shift+Enter 不发送也不阻止换行', () => {
  const { onSend } = renderComposer()
  const textbox = screen.getByRole('textbox', { name: '给投研助手发送消息' })

  const enter = new KeyboardEvent('keydown', {
    key: 'Enter',
    shiftKey: true,
    bubbles: true,
    cancelable: true,
  })
  textbox.dispatchEvent(enter)

  expect(enter.defaultPrevented).toBe(false)
  expect(onSend).not.toHaveBeenCalled()
})

it('中文 IME 组合输入期间按 Enter 不发送', () => {
  const { onSend } = renderComposer()
  const textbox = screen.getByRole('textbox', { name: '给投研助手发送消息' })

  fireEvent.keyDown(textbox, {
    key: 'Enter',
    keyCode: 13,
    isComposing: true,
  })

  expect(onSend).not.toHaveBeenCalled()
})

it('keyCode 229 的兼容组合输入按 Enter 不发送', () => {
  const { onSend } = renderComposer()
  const textbox = screen.getByRole('textbox', { name: '给投研助手发送消息' })

  fireEvent.keyDown(textbox, {
    key: 'Enter',
    keyCode: 229,
    isComposing: false,
  })

  expect(onSend).not.toHaveBeenCalled()
})

it('空内容或 disabled 时禁止发送', () => {
  const { rerender, onSend } = renderComposer({ value: '   ' })

  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
  fireEvent.submit(screen.getByRole('form', { name: '投研助手输入框' }))
  expect(onSend).not.toHaveBeenCalled()

  rerender(
    <AgentComposer
      value="可以发送"
      disabled
      sending={false}
      error={null}
      onChange={vi.fn()}
      onSend={onSend}
    />,
  )

  expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
  fireEvent.keyDown(screen.getByRole('textbox', { name: '给投研助手发送消息' }), { key: 'Enter' })
  expect(onSend).not.toHaveBeenCalled()
})

it('sending 时显示生成中并在有错误时用 alert 暴露', () => {
  const { container } = renderComposer({ sending: true, error: '网络失败' })

  const btn = screen.getByRole('button', { name: '生成中…' })
  expect(btn).toBeInTheDocument()
  expect(btn).toHaveClass('is-generating')
  expect(btn).toHaveAttribute('aria-busy', 'true')
  expect(container.querySelector('.agent-composer')).toHaveClass('is-generating')
  expect(container.querySelector('.agent-composer-field')).toHaveClass('is-generating')
  expect(screen.getByRole('textbox', { name: '给投研助手发送消息' })).toHaveAttribute(
    'placeholder',
    '正在生成回复…',
  )
  expect(screen.getByRole('alert')).toHaveTextContent('网络失败')
})

it('输入框限制 8000 字并在右下角展示字数', () => {
  const { container } = renderComposer({ value: '测'.repeat(12) })
  const textbox = screen.getByRole('textbox', { name: '给投研助手发送消息' })
  expect(textbox).toHaveAttribute('maxLength', '8000')
  const count = screen.getByText('12/8000')
  expect(count).toHaveClass('agent-composer-count')
  expect(container.querySelector('.agent-composer-field')).toContainElement(count)
})

it('value 变化时自动增高且最高 120px', () => {
  const { rerender } = renderComposer({ value: '短内容' })
  const textbox = screen.getByRole('textbox', { name: '给投研助手发送消息' })
  Object.defineProperty(textbox, 'scrollHeight', {
    configurable: true,
    value: 180,
  })

  rerender(
    <AgentComposer
      value={'第一行\n第二行\n第三行'}
      disabled={false}
      sending={false}
      error={null}
      onChange={vi.fn()}
      onSend={vi.fn()}
    />,
  )

  expect(textbox).toHaveStyle({ height: '120px' })
})
