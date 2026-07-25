import { useEffect, useRef } from 'react'

export const AGENT_CHAT_MESSAGE_LIMIT = 8000

export type AgentComposerProps = {
  value: string
  disabled: boolean
  sending: boolean
  error?: string | null
  maxLength?: number
  onChange: (value: string) => void
  onSend: () => void
}

export function AgentComposer({
  value,
  disabled,
  sending,
  error,
  maxLength = AGENT_CHAT_MESSAGE_LIMIT,
  onChange,
  onSend,
}: AgentComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const overLimit = value.length > maxLength
  const blocked = disabled || !value.trim() || overLimit

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 120)}px`
  }, [value])

  const submit = () => {
    if (blocked) return
    onSend()
  }

  return (
    <form
      className="agent-composer"
      aria-label="投研助手输入框"
      onSubmit={(event) => {
        event.preventDefault()
        submit()
      }}
    >
      <div className="agent-composer-field">
        <textarea
          ref={textareaRef}
          className="input"
          rows={2}
          aria-label="给投研助手发送消息"
          placeholder="问投研助手…（Enter 发送，Shift+Enter 换行）"
          value={value}
          maxLength={maxLength}
          disabled={disabled}
          style={{ maxHeight: 120, overflowY: 'auto', resize: 'none' }}
          onChange={(event) => onChange(event.currentTarget.value.slice(0, maxLength))}
          onKeyDown={(event) => {
            const nativeEvent = event.nativeEvent
            if (nativeEvent.isComposing || nativeEvent.keyCode === 229) return
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
        />
        <span
          className={`agent-composer-count${overLimit ? ' is-error' : ''}`}
          aria-live="polite"
        >
          {value.length}/{maxLength}
        </span>
      </div>
      {error ? (
        <p className="status error" role="alert">
          {error}
        </p>
      ) : null}
      <button className="btn" type="submit" disabled={blocked}>
        {sending ? '生成中…' : '发送'}
      </button>
    </form>
  )
}
