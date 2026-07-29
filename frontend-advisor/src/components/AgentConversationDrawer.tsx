import { useEffect, useRef, type RefObject } from 'react'
import type { AgentSession } from '../agentApi'
import { AgentSessionList } from './AgentSessionList'

export type AgentConversationDrawerProps = {
  open: boolean
  sessions: AgentSession[]
  activeSessionId: string | null
  disabled: boolean
  hasMore: boolean
  loadingMore?: boolean
  triggerRef: RefObject<HTMLElement | null>
  onClose: () => void
  onNew: () => void
  onOpen: (sessionId: string) => void
  onDelete: (sessionId: string) => void
  onLoadMore?: () => void
}

export function AgentConversationDrawer({
  open,
  sessions,
  activeSessionId,
  disabled,
  hasMore,
  loadingMore = false,
  triggerRef,
  onClose,
  onNew,
  onOpen,
  onDelete,
  onLoadMore,
}: AgentConversationDrawerProps) {
  const closeRef = useRef<HTMLButtonElement | null>(null)
  const panelRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return

    closeRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
        return
      }
      if (event.key !== 'Tab') return

      const focusable = Array.from(
        panelRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      )
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!first || !last) return

      const active = document.activeElement
      if (event.shiftKey && (active === first || !panelRef.current?.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !panelRef.current?.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      triggerRef.current?.focus()
    }
  }, [onClose, open, triggerRef])

  if (!open) return null

  return (
    <div className="agent-conversation-drawer">
      <button
        type="button"
        className="agent-drawer-backdrop"
        data-testid="agent-drawer-backdrop"
        aria-label="关闭对话记录遮罩"
        tabIndex={-1}
        onClick={onClose}
      />
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="对话记录"
        className="agent-drawer-panel"
      >
        <header className="agent-drawer-head">
          <h2>对话记录</h2>
          <button ref={closeRef} type="button" className="btn" onClick={onClose}>
            关闭对话记录
          </button>
        </header>

        <button type="button" className="btn" disabled={disabled} onClick={onNew}>
          新对话
        </button>

        <AgentSessionList
          sessions={sessions}
          activeSessionId={activeSessionId}
          disabled={disabled}
          hasMore={hasMore}
          loadingMore={loadingMore}
          onLoadMore={onLoadMore}
          onOpen={onOpen}
          onDelete={onDelete}
        />
      </aside>
    </div>
  )
}
