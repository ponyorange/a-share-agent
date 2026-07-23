# Final Whole-Branch Review Package (re-review after DELETE in-flight lock)

Previous Important finding: DELETE in-flight selection switch could abort request and desync client/server.

## Fix summary
- `deleteInFlight` state + ref lock around DELETE HTTP
- unlock after tombstone before refresh continuation (keeps post-success switch test)
- `selectRun` no-ops while locked
- RunHistory `selectionLocked` disables history buttons
- Create button disabled while locked
- New test: `删除请求在途时禁止切换历史且不中止删除信号`

## Evidence
- CommitteePage + committeeApi tests: 55 passed
- build OK

## Excerpts

### setDeleteLock
```tsx
const setDeleteLock = useCallback((locked: boolean) => {
    deleteInFlightRef.current = locked
    setDeleteInFlight(locked)
  }, [])

  
```

### selectRun head
```tsx
const selectRun = useCallback((runId: string) => {
    if (deleteInFlightRef.current) return
    const generation = ++selectionRef.current
    selectedIdRef.current = runId
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
    setShowApproval(false)
    setActionPending(false)
    setDetailLoading(true)
    setError('')
    setStreamState('载入历史')

    void (async () => {
      const detail = await fetchDetail(runId, generation, 'hydrate', true)
      if (!detail || TERMINAL.has(detail.run.status)) {
        if (detail) setStreamState('历史完成态')
```

### remove()
```tsx
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

  
```

### RunHistory selectionLocked
```tsx
_LABELS: Record<string, string> = {
  queued: '排队',
  running: '进行中',
  completed: '完成',
  failed: '失败',
  cancelled: '已取消',
}

export default function RunHistory({
  runs,
  selectedId,
  loading,
  selectionLocked = false,
  onSelect,
}: {
  runs: CommitteeRun[]
  selectedId?: string
  loading: boolean
  selectionLocked?: boolean
  onSelect: (runId: string) => void
}) {
  const [status, setStatus] = useState('all')
  const filtered = useMemo(
    () => runs.filter((run) => status === 'all' || run.status === status),
    [runs, status],
  )
  return (
    <aside className="committee-history" 
```
