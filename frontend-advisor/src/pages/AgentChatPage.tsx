import { memo, useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Navigate } from 'react-router-dom'
import { Virtuoso, type VirtuosoHandle } from 'react-virtuoso'
import {
  createAgentSession,
  deleteAgentSession,
  fetchAgentMessages,
  fetchLlmSettings,
  listAgentSessions,
  streamAgentChat,
  type AgentSession,
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

const ChatBubble = memo(function ChatBubble({ m }: { m: Msg }) {
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
    </div>
  )
})

export default function AgentChatPage() {
  const [ready, setReady] = useState<boolean | null>(null)
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [liveTools, setLiveTools] = useState<{ tool: string; content: string }[]>([])
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const virtuosoRef = useRef<VirtuosoHandle | null>(null)
  const stickToBottomRef = useRef(true)

  const refreshSessions = useCallback(async () => {
    const res = await listAgentSessions()
    setSessions(res.sessions || [])
    return res.sessions || []
  }, [])

  useEffect(() => {
    fetchLlmSettings()
      .then(async (s) => {
        setReady(Boolean(s.configured))
        if (!s.configured) return
        const list = await refreshSessions()
        if (list.length) {
          await openSession(list[0].session_id)
        } else {
          const created = await createAgentSession()
          setSessionId(created.session_id)
          await refreshSessions()
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
  }, [messages, sending, liveTools])

  async function openSession(id: string) {
    setSessionId(id)
    setError(null)
    stickToBottomRef.current = true
    const res = await fetchAgentMessages(id)
    setMessages(
      (res.messages || [])
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({
          role: m.role as 'user' | 'assistant',
          content: m.content,
          trace: m.tool_trace,
        })),
    )
  }

  async function handleNewChat() {
    const created = await createAgentSession()
    setSessionId(created.session_id)
    setMessages([])
    stickToBottomRef.current = true
    await refreshSessions()
  }

  async function handleDelete(id: string) {
    if (!window.confirm('删除该对话？')) return
    await deleteAgentSession(id)
    const list = await refreshSessions()
    if (sessionId === id) {
      if (list[0]) await openSession(list[0].session_id)
      else await handleNewChat()
    }
  }

  async function send(raw?: string) {
    const text = (raw ?? input).trim()
    if (!text || sending) return
    setInput('')
    setError(null)
    setLiveTools([])
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
            if (meta.session_id && meta.session_id !== sessionId) {
              setSessionId(meta.session_id)
            }
          },
          onTool: (row) => {
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
          onToken: (delta) => {
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
          onError: (detail) => {
            setError(detail)
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
      if ((err as Error).name !== 'AbortError') {
        setError(err instanceof Error ? err.message : String(err))
      }
    } finally {
      setSending(false)
      setLiveTools([])
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    await send()
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

  return (
    <section className="page agent-layout">
      <aside className="agent-sidebar">
        <button type="button" className="btn" onClick={handleNewChat}>
          新对话
        </button>
        <ul className="agent-session-list">
          {sessions.map((s) => (
            <li key={s.session_id}>
              <button
                type="button"
                className={`agent-session-item${sessionId === s.session_id ? ' active' : ''}`}
                onClick={() => void openSession(s.session_id)}
              >
                <span className="agent-session-title">{s.title || '对话'}</span>
                <span className="agent-session-meta">{s.message_count ?? 0} 条</span>
              </button>
              <button
                type="button"
                className="agent-session-del"
                title="删除"
                onClick={() => void handleDelete(s.session_id)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="agent-main">
        {messages.length === 0 ? (
          <div className="agent-chat agent-chat-empty-wrap">
            <div className="agent-empty-block">
              <p className="agent-chat-empty">
                我是投研助手。点下方快捷提问，或直接输入问题。
              </p>
              <div className="agent-quick-prompts">
                {QUICK_PROMPTS.map((q) => (
                  <button
                    key={q.label}
                    type="button"
                    className="agent-quick-chip"
                    disabled={sending}
                    onClick={() => void send(q.message)}
                  >
                    {q.label}
                  </button>
                ))}
              </div>
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
                {sending && index === messages.length - 1 && liveTools.length > 0 ? (
                  <p className="meta-line">
                    工具：{liveTools.map((t) => t.tool).join(' → ')}
                  </p>
                ) : null}
              </div>
            )}
          />
        )}

        {error ? <p className="status error">{error}</p> : null}

        <div className="agent-quick-bar">
          {QUICK_PROMPTS.map((q) => (
            <button
              key={q.label}
              type="button"
              className="agent-quick-chip"
              disabled={sending}
              onClick={() => void send(q.message)}
            >
              {q.label}
            </button>
          ))}
        </div>

        <form className="agent-composer" onSubmit={onSubmit}>
          <textarea
            className="input"
            rows={2}
            placeholder="问投研助手…（Enter 发送，Shift+Enter 换行）"
            value={input}
            disabled={sending}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
          />
          <button className="btn" type="submit" disabled={sending || !input.trim()}>
            {sending ? '生成中…' : '发送'}
          </button>
        </form>
      </div>
    </section>
  )
}
