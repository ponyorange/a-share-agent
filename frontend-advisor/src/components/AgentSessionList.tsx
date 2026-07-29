import { useCallback, useRef, type UIEvent } from 'react'
import type { AgentSession } from '../agentApi'

export type AgentSessionListProps = {
  sessions: AgentSession[]
  activeSessionId: string | null
  disabled?: boolean
  hasMore: boolean
  loadingMore?: boolean
  onLoadMore?: () => void
  onOpen: (sessionId: string) => void
  onDelete: (sessionId: string) => void
}

function sessionTitle(session: AgentSession) {
  return session.title || '对话'
}

export function AgentSessionList({
  sessions,
  activeSessionId,
  disabled = false,
  hasMore,
  loadingMore = false,
  onLoadMore,
  onOpen,
  onDelete,
}: AgentSessionListProps) {
  const loadingRef = useRef(false)
  loadingRef.current = loadingMore

  const handleScroll = useCallback(
    (event: UIEvent<HTMLElement>) => {
      if (!onLoadMore || !hasMore || loadingRef.current) return
      const el = event.currentTarget
      const remaining = el.scrollHeight - el.scrollTop - el.clientHeight
      if (remaining <= 48) onLoadMore()
    },
    [hasMore, onLoadMore],
  )

  return (
    <div className="agent-session-scroll" onScroll={handleScroll}>
      <ul className="agent-session-list">
        {sessions.map((session) => {
          const title = sessionTitle(session)
          return (
            <li key={session.session_id}>
              <button
                type="button"
                className={`agent-session-item${
                  activeSessionId === session.session_id ? ' active' : ''
                }`}
                aria-current={activeSessionId === session.session_id ? 'true' : undefined}
                aria-label={`打开 ${title}`}
                disabled={disabled}
                onClick={() => onOpen(session.session_id)}
              >
                <span className="agent-session-title">{title}</span>
                <span className="agent-session-meta">{session.message_count ?? 0} 条</span>
              </button>
              <button
                type="button"
                className="agent-session-del"
                aria-label={`删除 ${title}`}
                title="删除"
                disabled={disabled}
                onClick={() => onDelete(session.session_id)}
              >
                ×
              </button>
            </li>
          )
        })}
      </ul>
      {loadingMore ? <p className="agent-session-foot muted">加载更早对话…</p> : null}
      {!loadingMore && !hasMore && sessions.length > 0 ? (
        <p className="agent-session-foot muted">没有更早对话</p>
      ) : null}
    </div>
  )
}
