import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import {
  cancelCommitteeRun,
  deleteCommitteeRun,
  getCommitteeRun,
  listCommitteeRuns,
  retryCommitteeRun,
  streamCommitteeEvents,
  type CommitteeArtifact,
  type ParsedSseEvent,
  type CommitteeRun,
} from './committeeApi'
import {
  applyChatSseEvent,
  chatMessagesReducer,
  initialChatMessagesState,
  messagesFromEvents,
  orderedChatMessages,
  type CommitteeChatMessage,
} from './chatMessages'
import {
  initialTimelineState,
  latestEventId,
  parsedEventToRecord,
  timelineReducer,
} from './timeline'
import ApprovalDialog from './components/ApprovalDialog'
import CommitteeChat from './components/CommitteeChat'
import CommitteeDetail from './components/CommitteeDetail'
import CommitteeDetailDrawer from './components/CommitteeDetailDrawer'
import CreateRunDialog from './components/CreateRunDialog'
import RunHistory from './components/RunHistory'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])
const INTERRUPTED_TERMINAL = new Set(['failed', 'cancelled'])
const REVEAL_INTERVAL_MS = 24

export default function CommitteePage() {
  const [runs, setRuns] = useState<CommitteeRun[]>([])
  const [selectedId, setSelectedId] = useState<string>()
  const [selectedRun, setSelectedRun] = useState<CommitteeRun>()
  const [artifacts, setArtifacts] = useState<CommitteeArtifact[]>([])
  const [historyLoading, setHistoryLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [streamState, setStreamState] = useState('未连接')
  const [showCreate, setShowCreate] = useState(false)
  const [showApproval, setShowApproval] = useState(false)
  const [showDetail, setShowDetail] = useState(false)
  const [actionPending, setActionPending] = useState(false)
  const [deleteInFlight, setDeleteInFlight] = useState(false)
  const [timeline, dispatch] = useReducer(timelineReducer, initialTimelineState)
  const [chat, dispatchChat] = useReducer(
    chatMessagesReducer,
    initialChatMessagesState,
  )
  const streamAbort = useRef<AbortController | null>(null)
  const detailAbort = useRef<AbortController | null>(null)
  const actionAbort = useRef<AbortController | null>(null)
  const refreshTimer = useRef<number | null>(null)
  const chatFlushTimer = useRef<number | null>(null)
  const chatDeltaBuffer = useRef<ParsedSseEvent[]>([])
  const chatRevealTimers = useRef(new Map<string, number>())
  const deltaMessageIds = useRef(new Set<string>())
  const knownCompletedMessageIds = useRef(new Set<string>())
  const liveSessionRef = useRef(false)
  const selectionRef = useRef(0)
  const selectedIdRef = useRef<string | undefined>(undefined)
  const deletedRunIds = useRef(new Set<string>())
  const deleteInFlightRef = useRef(false)
  const mountedRef = useRef(true)

  const clearChatDeltas = useCallback(() => {
    if (chatFlushTimer.current != null) {
      window.clearTimeout(chatFlushTimer.current)
      chatFlushTimer.current = null
    }
    chatDeltaBuffer.current = []
  }, [])

  const clearChatReveals = useCallback(() => {
    chatRevealTimers.current.forEach((timer) => window.clearInterval(timer))
    chatRevealTimers.current.clear()
  }, [])

  const revealCompletedMessage = useCallback((
    message: CommitteeChatMessage,
  ) => {
    const existing = chatRevealTimers.current.get(message.message_id)
    if (existing !== undefined) {
      window.clearInterval(existing)
      chatRevealTimers.current.delete(message.message_id)
    }

    const length = [...message.content].length
    dispatchChat({
      type: 'revealCompleted',
      message,
      visibleCodePoints: 0,
    })
    if (!length) return

    let visibleCodePoints = 0
    const timer = window.setInterval(() => {
      visibleCodePoints += 1
      dispatchChat({
        type: 'revealCompleted',
        message,
        visibleCodePoints,
      })
      if (visibleCodePoints >= length) {
        window.clearInterval(timer)
        chatRevealTimers.current.delete(message.message_id)
      }
    }, REVEAL_INTERVAL_MS)
    chatRevealTimers.current.set(message.message_id, timer)
  }, [])

  const flushChatDeltas = useCallback(() => {
    if (chatFlushTimer.current != null) {
      window.clearTimeout(chatFlushTimer.current)
      chatFlushTimer.current = null
    }
    const events = chatDeltaBuffer.current
    chatDeltaBuffer.current = []
    events.forEach((event) => dispatchChat({ type: 'sse', event }))
  }, [])

  const dispatchChatSse = useCallback((event: ParsedSseEvent) => {
    if (event.event === 'message_delta') {
      const messageId = typeof event.data.message_id === 'string'
        ? event.data.message_id
        : undefined
      if (messageId) deltaMessageIds.current.add(messageId)
      chatDeltaBuffer.current.push(event)
      if (chatFlushTimer.current == null) {
        chatFlushTimer.current = window.setTimeout(flushChatDeltas, 40)
      }
      return
    }
    if (event.event === 'message_completed') {
      flushChatDeltas()
      const messageId = typeof event.data.message_id === 'string'
        ? event.data.message_id
        : undefined
      const completed = messageId
        ? applyChatSseEvent(initialChatMessagesState, event).byId[messageId]
        : undefined
      const shouldReveal = Boolean(
        completed
        && liveSessionRef.current
        && !knownCompletedMessageIds.current.has(messageId!)
        && !deltaMessageIds.current.has(messageId!),
      )
      if (messageId) knownCompletedMessageIds.current.add(messageId)
      dispatchChat({ type: 'sse', event })
      if (shouldReveal && completed) revealCompletedMessage(completed)
      return
    }
    dispatchChat({ type: 'sse', event })
  }, [flushChatDeltas, revealCompletedMessage])

  const setDeleteLock = useCallback((locked: boolean) => {
    deleteInFlightRef.current = locked
    setDeleteInFlight(locked)
  }, [])

  const refreshRuns = useCallback(async () => {
    setHistoryLoading(true)
    try {
      const result = await listCommitteeRuns()
      const visibleRuns = result.runs.filter(
        (run) => !deletedRunIds.current.has(run.run_id),
      )
      if (mountedRef.current) setRuns(visibleRuns)
      return visibleRuns
    } catch (cause) {
      if (mountedRef.current) {
        setError(cause instanceof Error ? cause.message : '加载会议失败')
      }
      return []
    } finally {
      if (mountedRef.current) setHistoryLoading(false)
    }
  }, [])

  const fetchDetail = useCallback(async (
    runId: string,
    generation: number,
    mode: 'hydrate' | 'merge',
    initial: boolean,
  ) => {
    detailAbort.current?.abort()
    const controller = new AbortController()
    detailAbort.current = controller
    try {
      const detail = await getCommitteeRun(runId, controller.signal)
      if (
        controller.signal.aborted ||
        !mountedRef.current ||
        generation !== selectionRef.current ||
        selectedIdRef.current !== runId
      ) {
        return undefined
      }
      setSelectedRun(detail.run)
      setArtifacts(detail.artifacts)
      dispatch({ type: mode, events: detail.events })
      const completedMessages = messagesFromEvents(detail.events)
      const revealMessages = mode === 'merge' && liveSessionRef.current
        ? completedMessages.filter((message) =>
            !knownCompletedMessageIds.current.has(message.message_id)
            && !deltaMessageIds.current.has(message.message_id),
          )
        : []
      completedMessages.forEach((message) => {
        knownCompletedMessageIds.current.add(message.message_id)
      })
      dispatchChat({
        type: mode,
        events: detail.events,
        artifacts: detail.artifacts,
        context: { runId, attempt: detail.run.attempt },
      })
      revealMessages.forEach(revealCompletedMessage)
      if (INTERRUPTED_TERMINAL.has(detail.run.status)) {
        clearChatReveals()
        dispatchChat({ type: 'interruptStreaming' })
      }
      if (initial) setDetailLoading(false)
      return detail
    } catch (cause) {
      if (controller.signal.aborted) return undefined
      if (
        mountedRef.current &&
        generation === selectionRef.current &&
        selectedIdRef.current === runId
      ) {
        if (initial) {
          setSelectedRun(undefined)
          setArtifacts([])
          dispatch({ type: 'reset' })
          dispatchChat({ type: 'reset' })
          setDetailLoading(false)
        }
        setError(cause instanceof Error ? cause.message : '加载详情失败')
        setStreamState('错误')
      }
      return undefined
    }
  }, [clearChatReveals, revealCompletedMessage])

  const scheduleDetailRefresh = useCallback((
    runId: string,
    generation: number,
    immediate = false,
  ) => {
    if (
      !mountedRef.current ||
      selectedIdRef.current !== runId ||
      generation !== selectionRef.current
    ) return
    if (refreshTimer.current != null) {
      if (!immediate) return
      window.clearTimeout(refreshTimer.current)
    }
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = null
      void fetchDetail(runId, generation, 'merge', false)
    }, immediate ? 0 : 250)
  }, [fetchDetail])

  const selectRun = useCallback((runId: string) => {
    if (deleteInFlightRef.current) return
    const generation = ++selectionRef.current
    selectedIdRef.current = runId
    liveSessionRef.current = false
    streamAbort.current?.abort()
    detailAbort.current?.abort()
    actionAbort.current?.abort()
    if (refreshTimer.current != null) {
      window.clearTimeout(refreshTimer.current)
      refreshTimer.current = null
    }
    setSelectedId(runId)
    setSelectedRun(undefined)
    setArtifacts([])
    dispatch({ type: 'reset' })
    clearChatDeltas()
    clearChatReveals()
    deltaMessageIds.current.clear()
    knownCompletedMessageIds.current.clear()
    dispatchChat({ type: 'reset' })
    setShowDetail(false)
    setShowApproval(false)
    setActionPending(false)
    setDetailLoading(true)
    setError('')
    setStreamState('载入历史')

    void (async () => {
      const detail = await fetchDetail(runId, generation, 'hydrate', true)
      if (!detail || TERMINAL.has(detail.run.status)) {
        if (detail) setStreamState('历史完成态')
        return
      }
      if (
        !mountedRef.current ||
        generation !== selectionRef.current ||
        selectedIdRef.current !== runId
      ) return
      const controller = new AbortController()
      streamAbort.current = controller
      liveSessionRef.current = true
      setStreamState('实时连接')
      await streamCommitteeEvents(
        runId,
        {
          onEvent: (event) => {
            if (
              controller.signal.aborted ||
              !mountedRef.current ||
              generation !== selectionRef.current ||
              selectedIdRef.current !== runId
            ) return
            dispatch({ type: 'event', event: parsedEventToRecord(event) })
            dispatchChatSse(event)
            const terminal = TERMINAL.has(event.event)
            if (
              terminal ||
              event.event === 'node_completed' ||
              event.event.toLowerCase().includes('artifact')
            ) {
              scheduleDetailRefresh(runId, generation, terminal)
            }
            if (terminal) {
              liveSessionRef.current = false
              if (INTERRUPTED_TERMINAL.has(event.event)) {
                clearChatReveals()
                dispatchChat({ type: 'interruptStreaming' })
              }
              setSelectedRun((current) =>
                current?.run_id === runId
                  ? { ...current, status: event.event as CommitteeRun['status'] }
                  : current,
              )
              setStreamState('终态')
              void refreshRuns()
            }
          },
          onError: (cause) => {
            if (!controller.signal.aborted && mountedRef.current) {
              setStreamState(`断线：${cause.message}`)
            }
          },
          onReconnect: (attempt) => {
            if (!controller.signal.aborted && mountedRef.current) {
              setStreamState(`第 ${attempt} 次重连`)
            }
          },
        },
        {
          signal: controller.signal,
          lastEventId: latestEventId(detail.events),
        },
      )
    })()
  }, [clearChatDeltas, clearChatReveals, dispatchChatSse, fetchDetail, refreshRuns, scheduleDetailRefresh])

  useEffect(() => {
    mountedRef.current = true
    void refreshRuns().then((items) => {
      if (mountedRef.current && !selectedIdRef.current && items[0]) {
        selectRun(items[0].run_id)
      }
    })
    return () => {
      mountedRef.current = false
      streamAbort.current?.abort()
      detailAbort.current?.abort()
      actionAbort.current?.abort()
      if (refreshTimer.current != null) window.clearTimeout(refreshTimer.current)
      clearChatDeltas()
      clearChatReveals()
    }
  }, [clearChatDeltas, clearChatReveals, refreshRuns, selectRun])

  const closeCreate = useCallback(() => setShowCreate(false), [])
  const closeApproval = useCallback(() => setShowApproval(false), [])
  const handleCreated = useCallback((runId: string) => {
    void refreshRuns()
    selectRun(runId)
  }, [refreshRuns, selectRun])

  async function cancel() {
    const run = selectedRun
    if (
      !run ||
      run.run_id !== selectedId ||
      detailLoading ||
      actionPending
    ) return
    const generation = selectionRef.current
    actionAbort.current?.abort()
    const controller = new AbortController()
    actionAbort.current = controller
    setActionPending(true)
    setError('')
    try {
      const result = await cancelCommitteeRun(run.run_id, controller.signal)
      if (
        controller.signal.aborted ||
        selectedIdRef.current !== run.run_id ||
        generation !== selectionRef.current
      ) return
      setSelectedRun((current) =>
        current?.run_id === run.run_id
          ? { ...current, status: result.status }
          : current,
      )
      if (INTERRUPTED_TERMINAL.has(result.status)) {
        liveSessionRef.current = false
        clearChatReveals()
        dispatchChat({ type: 'interruptStreaming' })
      }
      await refreshRuns()
      await fetchDetail(run.run_id, generation, 'merge', false)
    } catch (cause) {
      if (!controller.signal.aborted && mountedRef.current) {
        setError(cause instanceof Error ? cause.message : '取消失败')
      }
    } finally {
      if (
        !controller.signal.aborted &&
        selectedIdRef.current === run.run_id &&
        generation === selectionRef.current
      ) setActionPending(false)
    }
  }

  async function retry() {
    const run = selectedRun
    if (
      !run ||
      run.run_id !== selectedId ||
      detailLoading ||
      actionPending
    ) return
    const generation = selectionRef.current
    setActionPending(true)
    setError('')
    const key = `committee-retry:${run.run_id}:${run.attempt + 1}`
    try {
      const result = await retryCommitteeRun(run.run_id, key)
      if (
        selectedIdRef.current !== run.run_id ||
        generation !== selectionRef.current
      ) return
      await refreshRuns()
      selectRun(result.run_id)
    } catch (cause) {
      if (
        mountedRef.current &&
        selectedIdRef.current === run.run_id &&
        generation === selectionRef.current
      ) {
        setError(cause instanceof Error ? cause.message : '重试失败')
        setActionPending(false)
      }
    }
  }

  async function remove() {
    const run = selectedRun
    if (
      !run ||
      run.run_id !== selectedId ||
      !TERMINAL.has(run.status) ||
      detailLoading ||
      actionPending
    ) return
    if (!window.confirm(
      '只会从历史列表隐藏此会议，不会撤销审批或订单。确认删除？',
    )) return

    const deletedId = run.run_id
    const controller = new AbortController()
    actionAbort.current?.abort()
    actionAbort.current = controller
    setActionPending(true)
    setDeleteLock(true)
    setError('')
    try {
      await deleteCommitteeRun(deletedId, controller.signal)
      if (!mountedRef.current) return
      deletedRunIds.current.add(deletedId)
      setRuns((current) => current.filter((item) => item.run_id !== deletedId))
      setDeleteLock(false)
      if (controller.signal.aborted) return

      streamAbort.current?.abort()
      detailAbort.current?.abort()
      if (refreshTimer.current != null) {
        window.clearTimeout(refreshTimer.current)
        refreshTimer.current = null
      }
      const generation = ++selectionRef.current
      selectedIdRef.current = undefined
      setSelectedId(undefined)
      setSelectedRun(undefined)
      setArtifacts([])
      dispatch({ type: 'reset' })
      clearChatDeltas()
      clearChatReveals()
      dispatchChat({ type: 'reset' })
      setShowDetail(false)
      setShowApproval(false)
      setStreamState('未连接')

      const items = await refreshRuns()
      if (
        !controller.signal.aborted &&
        mountedRef.current &&
        generation === selectionRef.current &&
        selectedIdRef.current == null &&
        items[0]
      ) {
        selectRun(items[0].run_id)
      }
    } catch (cause) {
      if (!controller.signal.aborted && mountedRef.current) {
        setError(cause instanceof Error ? cause.message : '删除失败')
      }
    } finally {
      setDeleteLock(false)
      if (!controller.signal.aborted && mountedRef.current) {
        setActionPending(false)
      }
    }
  }

  return (
    <div className="page committee-page">
      <header className="committee-hero">
        <div>
          <p className="committee-kicker">Investment Committee · Live</p>
          <h1>投委会实时工作台</h1>
          <p>冻结数据、四方分析、多空辩论、回测风控与人工审批的可审计链路。</p>
        </div>
        <button
          type="button"
          className="btn"
          disabled={deleteInFlight}
          onClick={() => setShowCreate(true)}
        >
          发起会议
        </button>
      </header>

      {error ? <p className="committee-alert" role="alert">{error}</p> : null}

      <div className="committee-workspace">
        <RunHistory
          runs={runs}
          selectedId={selectedId}
          loading={historyLoading}
          selectionLocked={deleteInFlight}
          onSelect={selectRun}
        />
        <main className="committee-main">
          {detailLoading ? <p className="status">载入会议详情与历史事件…</p> : null}
          {!detailLoading && !selectedRun ? (
            <div className="committee-empty">
              <h2>选择或发起一次会议</h2>
              <p>实时事件会在此处按节点重放。</p>
            </div>
          ) : null}
          {selectedRun && selectedRun.run_id === selectedId && !detailLoading ? (
            <section className="committee-chat-pane">
              <header className="committee-chat-header">
                <div>
                  <h2 className="mono">{selectedRun.run_id}</h2>
                  <p>{selectedRun.status} · {streamState}</p>
                </div>
                <div className="committee-actions" aria-label="会议操作">
                  <button
                    type="button"
                    className="btn ghost"
                    onClick={() => setShowDetail(true)}
                  >
                    查看详情
                  </button>
                  {!TERMINAL.has(selectedRun.status) ? (
                    <button
                      type="button"
                      className="btn ghost"
                      disabled={actionPending || detailLoading}
                      onClick={() => void cancel()}
                    >
                      取消会议
                    </button>
                  ) : null}
                  {TERMINAL.has(selectedRun.status) ? (
                    <button
                      type="button"
                      className="btn ghost committee-delete"
                      disabled={actionPending || detailLoading}
                      onClick={() => void remove()}
                    >
                      删除记录
                    </button>
                  ) : null}
                  {['failed', 'cancelled'].includes(selectedRun.status) ? (
                    <button
                      type="button"
                      className="btn ghost"
                      disabled={actionPending || detailLoading}
                      onClick={() => void retry()}
                    >
                      重试会议
                    </button>
                  ) : null}
                  {selectedRun.status === 'completed' ? (
                    <button
                      type="button"
                      className="btn"
                      disabled={detailLoading || actionPending}
                      onClick={() => setShowApproval(true)}
                    >
                      审批订单
                    </button>
                  ) : null}
                </div>
              </header>
              <CommitteeChat
                messages={orderedChatMessages(chat)}
                artifacts={artifacts}
                loading={detailLoading}
                streamState={streamState}
              />
            </section>
          ) : null}
        </main>
      </div>

      <CommitteeDetailDrawer
        open={showDetail}
        onClose={() => setShowDetail(false)}
      >
        {selectedRun && selectedRun.run_id === selectedId ? (
          <CommitteeDetail
            run={selectedRun}
            artifacts={artifacts}
            events={timeline.events}
            streamState={streamState}
          />
        ) : null}
      </CommitteeDetailDrawer>

      {showCreate ? (
        <CreateRunDialog
          onClose={closeCreate}
          onCreated={handleCreated}
        />
      ) : null}
      {showApproval && selectedRun && selectedRun.run_id === selectedId && !detailLoading ? (
        <ApprovalDialog run={selectedRun} onClose={closeApproval} />
      ) : null}
    </div>
  )
}
