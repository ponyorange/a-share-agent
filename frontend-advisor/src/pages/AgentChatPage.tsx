import { memo, useCallback, useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Navigate } from 'react-router-dom'
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso'
import { AgentComposer } from '../components/AgentComposer'
import { AgentConversationDrawer } from '../components/AgentConversationDrawer'
import { useMediaQuery } from '../components/ResponsiveDataView'
import {
  createAgentSession,
  deleteAgentSession,
  fetchAgentMessages,
  fetchLlmSettings,
  listAgentSessions,
  streamAgentChat,
  type AgentSession,
  type SubagentProgress,
} from '../agentApi'

type Msg = {
  role: 'user' | 'assistant'
  content: string
  trace?: { tool: string; content: string }[]
  streaming?: boolean
}

const QUICK_PROMPTS: { label: string; message: string }[] = [
  {
    label: '今日关注',
    message:
      '请给出今日关注推荐：先读取今日推荐归档，再结合联播/宏观要点，按板块列出值得关注的标的；说明综合分，并点到 tech/flow/sector/value/market 子分要点，不要只讲动量。若暂无归档，请提示我去基础面板刷新候选池。',
  },
  {
    label: '持仓诊断',
    message: '分析我的真实持仓：哪些该卖、持有或加仓？给出评分与简要理由。',
  },
  {
    label: '联播宏观',
    message: '今天联播和宏观有哪些值得关注的信号？简要点评对市场风险偏好的影响。',
  },
]

const SAFE_SESSION_ERROR = '会话操作失败，请稍后重试'
const SAFE_CHAT_ERROR = '消息发送失败，请稍后重试'

const STEP_LABELS: Record<SubagentProgress['step'], string> = {
  delegate: '委派',
  list_sources: '列数据源',
  search: '搜索接口',
  describe: '查看说明',
  fetch: '获取数据',
  sandbox: '清洗数据',
  submit: '提交结果',
}

const STATUS_LABELS: Record<SubagentProgress['status'], string> = {
  started: '运行中',
  completed: '已完成',
  failed: '失败',
}

function sameProgressItem(a: SubagentProgress, b: SubagentProgress) {
  return (
    a.phase === b.phase &&
    a.step === b.step &&
    a.source === b.source &&
    a.interface === b.interface
  )
}

export function mergeSubagentProgress(
  current: SubagentProgress[],
  next: SubagentProgress,
): SubagentProgress[] {
  const index = current.findIndex((item) => sameProgressItem(item, next))
  if (index === -1) return [...current, next]
  const copy = [...current]
  copy[index] = next
  return copy
}

export function SubagentProgressPanel({
  items,
  collapsed,
  onToggle,
}: {
  items: SubagentProgress[]
  collapsed: boolean
  onToggle?: () => void
}) {
  const runningCount = items.filter((item) => item.status === 'started').length
  const latest = items[items.length - 1]
  return (
    <section className="subagent-progress" aria-label="子 Agent 实时进度">
      <div className="subagent-progress-header">
        <div>
          <span className="subagent-progress-title">数据子 Agent</span>
          <span className="subagent-progress-summary">
            {runningCount > 0 ? `${runningCount} 项运行中` : `${items.length} 项进度`}
          </span>
          {collapsed && latest ? (
            <span className="subagent-progress-latest">
              {STEP_LABELS[latest.step]} · {STATUS_LABELS[latest.status]}
            </span>
          ) : null}
        </div>
        {onToggle ? (
          <button
            type="button"
            className="subagent-progress-toggle"
            onClick={onToggle}
            aria-expanded={!collapsed}
          >
            {collapsed ? '展开进度' : '折叠进度'}
          </button>
        ) : null}
      </div>

      {!collapsed ? (
        <ol className="subagent-progress-list">
          {items.map((item, index) => (
            <li
              key={`${item.phase}-${item.step}-${item.source || ''}-${item.interface || ''}-${index}`}
              className={`subagent-progress-item ${item.status}`}
            >
              <div className="subagent-progress-item-head">
                <span>{STEP_LABELS[item.step]}</span>
                <span>{STATUS_LABELS[item.status]}</span>
              </div>
              <p className="subagent-progress-message">{item.message}</p>
              <div className="subagent-progress-meta">
                {item.source ? <span>来源：{item.source}</span> : null}
                {item.interface ? <span>接口：{item.interface}</span> : null}
                {item.step === 'fetch' && item.status === 'completed' && typeof item.rows === 'number' ? (
                  <span>{item.rows} 行</span>
                ) : null}
                {item.step === 'fetch' && item.truncated ? <span>已截断</span> : null}
                {item.status === 'failed' && item.error_code ? (
                  <span>错误：{item.error_code}</span>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  )
}

export const ChatBubble = memo(function ChatBubble({ m }: { m: Msg }) {
  const [copied, setCopied] = useState(false)
  const canCopy =
    m.role === 'assistant' && Boolean(m.content.trim()) && !m.streaming

  async function handleCopy() {
    if (!canCopy) return
    try {
      await navigator.clipboard.writeText(m.content)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div className={`agent-bubble ${m.role}`}>
      <div className="agent-bubble-role">{m.role === 'user' ? '你' : '投研助手'}</div>
      {m.role === 'assistant' ? (
        <div className="agent-md">
          <Markdown remarkPlugins={[remarkGfm]}>
            {m.content || (m.streaming ? '…' : '')}
          </Markdown>
        </div>
      ) : (
        <div className="agent-bubble-body">{m.content}</div>
      )}
      {m.trace && m.trace.length > 0 ? (
        <details className="agent-trace">
          <summary>工具调用 {m.trace.length}</summary>
          <ul>
            {m.trace.map((t, j) => (
              <li key={j}>
                <code>{t.tool}</code>
                <pre>{t.content.slice(0, 800)}</pre>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {canCopy ? (
        <div className="agent-bubble-actions">
          <button
            type="button"
            className="btn ghost agent-copy-btn"
            aria-label={copied ? '已复制' : '复制'}
            title={copied ? '已复制' : '复制'}
            onClick={() => void handleCopy()}
          >
            {copied ? (
              <span aria-hidden="true">✓</span>
            ) : (
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
              >
                <rect x="8" y="8" width="11" height="11" rx="2" />
                <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
              </svg>
            )}
          </button>
        </div>
      ) : null}
    </div>
  )
})

export default function AgentChatPage() {
  const isMobile = useMediaQuery('(max-width: 768px)')
  const [ready, setReady] = useState<boolean | null>(null)
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [liveTools, setLiveTools] = useState<{ tool: string; content: string }[]>([])
  const [liveSubagentProgress, setLiveSubagentProgress] = useState<SubagentProgress[]>([])
  const [progressCollapsed, setProgressCollapsed] = useState(isMobile)
  const [sessionTransitioning, setSessionTransitioning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const drawerTriggerRef = useRef<HTMLButtonElement>(null)
  const virtuosoRef = useRef<VirtuosoHandle | null>(null)
  const stickToBottomRef = useRef(true)
  const activeStreamRef = useRef(0)
  const sessionTransitionRef = useRef(0)
  const hasAnswerTokenRef = useRef(false)
  const closeDrawer = useCallback(() => setDrawerOpen(false), [])

  const refreshSessions = useCallback(async () => {
    const res = await listAgentSessions()
    setSessions(res.sessions || [])
    return res.sessions || []
  }, [])

  useEffect(() => {
    fetchLlmSettings()
      .then(async (s) => {
        if (!s.configured) {
          setReady(false)
          return
        }
        const transitionToken = beginSessionTransition()
        setReady(true)
        try {
          const list = await refreshSessions()
          if (list.length) {
            await loadSessionMessages(list[0].session_id)
          } else {
            const created = await createAgentSession()
            setSessionId(created.session_id)
            await refreshSessions()
          }
        } catch {
          setError(SAFE_SESSION_ERROR)
        } finally {
          endSessionTransition(transitionToken)
        }
      })
      .catch(() => setReady(false))
    return () => abortRef.current?.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!stickToBottomRef.current || messages.length === 0) return
    virtuosoRef.current?.scrollToIndex({
      index: messages.length - 1,
      align: 'end',
      behavior: 'auto',
    })
  }, [messages, sending, liveTools, liveSubagentProgress])

  function beginSessionTransition() {
    const token = sessionTransitionRef.current + 1
    sessionTransitionRef.current = token
    setSessionTransitioning(true)
    return token
  }

  function endSessionTransition(token: number) {
    if (sessionTransitionRef.current === token) {
      setSessionTransitioning(false)
    }
  }

  function resetLiveStreamState(invalidateStream = false) {
    if (invalidateStream) activeStreamRef.current += 1
    hasAnswerTokenRef.current = false
    setSending(false)
    setLiveTools([])
    setLiveSubagentProgress([])
    setProgressCollapsed(isMobile)
  }

  function abortActiveStream() {
    abortRef.current?.abort()
    abortRef.current = null
  }

  function normalizeStreamingTail() {
    setMessages((prev) => {
      const last = prev[prev.length - 1]
      if (last?.role !== 'assistant' || !last.streaming) return prev
      if (!last.content.trim()) return prev.slice(0, -1)
      const copy = [...prev]
      copy[copy.length - 1] = { ...last, streaming: false }
      return copy
    })
  }

  async function loadSessionMessages(id: string) {
    const res = await fetchAgentMessages(id)
    const nextMessages: Msg[] =
      (res.messages || [])
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({
          role: m.role as 'user' | 'assistant',
          content: m.content,
          trace: m.tool_trace,
        }))
    setSessionId(id)
    setError(null)
    stickToBottomRef.current = true
    setMessages(nextMessages)
  }

  async function openSession(id: string): Promise<boolean> {
    const transitionToken = beginSessionTransition()
    abortActiveStream()
    resetLiveStreamState(true)
    normalizeStreamingTail()
    try {
      await loadSessionMessages(id)
      return true
    } catch {
      setError(SAFE_SESSION_ERROR)
      return false
    } finally {
      endSessionTransition(transitionToken)
    }
  }

  async function handleNewChat(): Promise<boolean> {
    const transitionToken = beginSessionTransition()
    abortActiveStream()
    resetLiveStreamState(true)
    normalizeStreamingTail()
    try {
      const created = await createAgentSession()
      setSessionId(created.session_id)
      setError(null)
      setMessages([])
      stickToBottomRef.current = true
      await refreshSessions()
      return true
    } catch {
      setError(SAFE_SESSION_ERROR)
      return false
    } finally {
      endSessionTransition(transitionToken)
    }
  }

  async function handleDelete(id: string): Promise<boolean> {
    if (!window.confirm('删除该对话？')) return false
    const deletingCurrent = sessionId === id
    if (!deletingCurrent) {
      try {
        await deleteAgentSession(id)
        await refreshSessions()
        return true
      } catch {
        setError(SAFE_SESSION_ERROR)
        return false
      }
    }

    const transitionToken = beginSessionTransition()
    abortActiveStream()
    resetLiveStreamState(true)
    normalizeStreamingTail()
    try {
      await deleteAgentSession(id)
      setSessionId(null)
      setMessages([])
      const list = await refreshSessions()
      if (list[0]) {
        await loadSessionMessages(list[0].session_id)
      } else {
        const created = await createAgentSession()
        setSessionId(created.session_id)
        stickToBottomRef.current = true
        await refreshSessions()
      }
      return true
    } catch {
      setError(SAFE_SESSION_ERROR)
      return false
    } finally {
      endSessionTransition(transitionToken)
    }
  }

  async function send(raw?: string) {
    const text = (raw ?? input).trim()
    if (!text || sending || sessionTransitioning || !sessionId) return
    setInput('')
    setError(null)
    setLiveTools([])
    const streamId = activeStreamRef.current + 1
    activeStreamRef.current = streamId
    hasAnswerTokenRef.current = false
    setLiveSubagentProgress([])
    setProgressCollapsed(isMobile)
    stickToBottomRef.current = true
    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setMessages((prev) => [...prev, { role: 'assistant', content: '', streaming: true }])
    setSending(true)
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    try {
      await streamAgentChat(
        text,
        sessionId,
        {
          onMeta: (meta) => {
            if (activeStreamRef.current !== streamId) return
            if (meta.session_id && meta.session_id !== sessionId) {
              setSessionId(meta.session_id)
            }
          },
          onTool: (row) => {
            if (activeStreamRef.current !== streamId) return
            setLiveTools((prev) => [...prev, row])
            // 工具执行后清空气泡，后续 token 才是最终回答的打字机效果
            setMessages((prev) => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last?.role === 'assistant' && last.streaming) {
                copy[copy.length - 1] = { ...last, content: '' }
              }
              return copy
            })
          },
          onSubagentProgress: (progress) => {
            if (activeStreamRef.current !== streamId) return
            setLiveSubagentProgress((current) => mergeSubagentProgress(current, progress))
          },
          onToken: (delta) => {
            if (activeStreamRef.current !== streamId) return
            if (!hasAnswerTokenRef.current) {
              hasAnswerTokenRef.current = true
              setProgressCollapsed(true)
            }
            setMessages((prev) => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last?.role === 'assistant') {
                copy[copy.length - 1] = {
                  ...last,
                  content: last.content + delta,
                  streaming: true,
                }
              }
              return copy
            })
          },
          onDone: (data) => {
            if (activeStreamRef.current !== streamId) return
            setMessages((prev) => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last?.role === 'assistant') {
                copy[copy.length - 1] = {
                  role: 'assistant',
                  content: data.reply || last.content,
                  trace: data.tool_trace || liveTools,
                  streaming: false,
                }
              }
              return copy
            })
            if (data.session_id) setSessionId(data.session_id)
            void refreshSessions()
          },
          onError: () => {
            if (activeStreamRef.current !== streamId) return
            setError(SAFE_CHAT_ERROR)
            setMessages((prev) => {
              const copy = [...prev]
              const last = copy[copy.length - 1]
              if (last?.role === 'assistant' && !last.content) copy.pop()
              else if (last?.streaming) {
                copy[copy.length - 1] = { ...last, streaming: false }
              }
              return copy
            })
          },
        },
        ac.signal,
      )
    } catch (err) {
      if (activeStreamRef.current === streamId && (err as Error).name !== 'AbortError') {
        setError(SAFE_CHAT_ERROR)
      }
    } finally {
      if (abortRef.current === ac) abortRef.current = null
      if (activeStreamRef.current === streamId) {
        setSending(false)
        setLiveTools([])
      }
    }
  }

  if (ready === null) {
    return (
      <section className="page">
        <p className="status">检查 DeepSeek 配置…</p>
      </section>
    )
  }
  if (!ready) {
    return <Navigate to="/agent/settings" replace />
  }
  const composingDisabled = sending || sessionTransitioning || !sessionId
  const currentSession = sessions.find((session) => session.session_id === sessionId)

  return (
    <section className="page agent-layout">
      <aside className="agent-sidebar">
        <button
          type="button"
          className="btn"
          disabled={sessionTransitioning}
          onClick={handleNewChat}
        >
          新对话
        </button>
        <ul className="agent-session-list">
          {sessions.map((s) => (
            <li key={s.session_id}>
              <button
                type="button"
                className={`agent-session-item${sessionId === s.session_id ? ' active' : ''}`}
                disabled={sessionTransitioning}
                onClick={() => void openSession(s.session_id)}
              >
                <span className="agent-session-title">{s.title || '对话'}</span>
                <span className="agent-session-meta">{s.message_count ?? 0} 条</span>
              </button>
              <button
                type="button"
                className="agent-session-del"
                title="删除"
                disabled={sessionTransitioning}
                onClick={() => void handleDelete(s.session_id)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="agent-main">
        <header className="agent-mobile-header">
          <button
            ref={drawerTriggerRef}
            type="button"
            className="btn agent-mobile-menu"
            aria-label="打开对话记录"
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen(true)}
          >
            ☰
          </button>
          <span className="agent-mobile-title">{currentSession?.title || '新对话'}</span>
          <button
            type="button"
            className="btn agent-mobile-new"
            aria-label="移动端新对话"
            disabled={sessionTransitioning}
            onClick={() => {
              void handleNewChat().then((succeeded) => {
                if (succeeded) closeDrawer()
              })
            }}
          >
            ＋
          </button>
        </header>

        <AgentConversationDrawer
          open={drawerOpen}
          sessions={sessions}
          activeSessionId={sessionId}
          disabled={sessionTransitioning}
          triggerRef={drawerTriggerRef}
          onClose={closeDrawer}
          onNew={() => {
            void handleNewChat().then((succeeded) => {
              if (succeeded) closeDrawer()
            })
          }}
          onOpen={(id) => {
            void openSession(id).then((succeeded) => {
              if (succeeded) closeDrawer()
            })
          }}
          onDelete={(id) => {
            void handleDelete(id).then((succeeded) => {
              if (succeeded) closeDrawer()
            })
          }}
        />

        {messages.length === 0 ? (
          <div className="agent-chat agent-chat-empty-wrap">
            <div className="agent-empty-block">
              <p className="agent-chat-empty">
                我是投研助手。点下方快捷提问，或直接输入问题。
              </p>
            </div>
          </div>
        ) : (
          <Virtuoso
            className="agent-chat"
            ref={virtuosoRef}
            data={messages}
            increaseViewportBy={200}
            atBottomStateChange={(atBottom) => {
              stickToBottomRef.current = atBottom
            }}
            followOutput={(isAtBottom) => (isAtBottom ? 'smooth' : false)}
            itemContent={(index, m) => (
              <div className="agent-bubble-wrap">
                <ChatBubble m={m} />
                {sending && index === messages.length - 1 && liveSubagentProgress.length > 0 ? (
                  <SubagentProgressPanel
                    items={liveSubagentProgress}
                    collapsed={progressCollapsed}
                    onToggle={() => setProgressCollapsed((value) => !value)}
                  />
                ) : null}
                {sending && index === messages.length - 1 && liveTools.length > 0 ? (
                  <p className="meta-line">
                    工具：{liveTools.map((t) => t.tool).join(' → ')}
                  </p>
                ) : null}
              </div>
            )}
          />
        )}

        <div
          className={`agent-quick-bar${input.trim() ? ' is-composing' : ''}`}
          role="region"
          aria-label="快捷问题"
        >
          <div className="agent-quick-scroll">
            {QUICK_PROMPTS.map((q) => (
              <button
                key={q.label}
                type="button"
                className="agent-quick-chip"
                disabled={composingDisabled}
                onClick={() => void send(q.message)}
              >
                {q.label}
              </button>
            ))}
          </div>
          <span className="agent-quick-more" aria-hidden="true">
            更多
          </span>
        </div>

        <AgentComposer
          value={input}
          disabled={composingDisabled}
          sending={sending}
          error={error}
          onChange={setInput}
          onSend={() => void send()}
        />
      </div>
    </section>
  )
}
