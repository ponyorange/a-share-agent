# Task 4 Re-review Package (no git)

Workspace is not a Git repository. Diff is provided as natural-language + current source excerpts for the soft-delete UI task after the tombstone fix.

## Scope

- Task: Committee soft-delete Task 4 (workbench delete UX)
- Prior review: docs/superpowers/reports/committee-delete-task-4-review.md (CHANGES_REQUESTED)
- Implementer report: docs/superpowers/reports/committee-delete-task-4-report.md (includes second re-review fix section)

## Change Description

### frontend-advisor/src/committee/CommitteePage.tsx (modified)

- Added session-scoped `deletedRunIds` ref (Set) as tombstones after successful DELETE.
- `refreshRuns()` filters every list response through tombstones before `setRuns` and before returning.
- Tombstone is recorded after DELETE resolves and while mounted, before aborted-controller early return, so stale in-flight list responses cannot resurrect a successfully deleted id even if the user switched selection.
- DELETE failure path does not add tombstones.
- Terminal-only delete button, confirm copy, abort/generation guards, local filter, auto-select next / empty state remain.

### frontend-advisor/src/committee/CommitteePage.test.tsx (modified)

- Added deferred race test `删除前旧列表请求乱序返回时不会复活已删除记录` that proves a pre-delete stale list response can write other ids (`run-3`) but not the deleted id (`run-live`).
- Retains prior delete UX tests: terminal visibility, cancel confirm, auto-select, empty state, failure retention, selection abort during refresh, refresh-failure local filter, dblclick once, failed/cancelled visibility.
- Removed a weaker duplicate stale-list test that did not positively prove the stale response applied.

### frontend-advisor/src/styles.css (modified)

- Added `.committee-delete` danger styling for the delete action.

## Key Source Excerpts

### Excerpt: tombstone + refreshRuns

```tsx
  const deletedRunIds = useRef(new Set<string>())
  const mountedRef = useRef(true)

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
```

### Excerpt: remove()

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
    setError('')
    try {
      await deleteCommitteeRun(deletedId, controller.signal)
      if (!mountedRef.current) return
      deletedRunIds.current.add(deletedId)
      setRuns((current) => current.filter((item) => item.run_id !== deletedId))
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
      if (!controller.signal.aborted && mountedRef.current) {
        setActionPending(false)
      }
    }
  }
```

### Excerpt: stale-list race test

```tsx
  it('删除前旧列表请求乱序返回时不会复活已删除记录', async () => {
    const deletedRun = { ...completedRun, run_id: 'run-live' }
    const nextRun = { ...completedRun, run_id: 'run-2' }
    const staleOtherRun = { ...completedRun, run_id: 'run-3' }
    const staleRefresh = deferred<{ runs: (typeof completedRun)[] }>()
    let streamHandlers: {
      onEvent: (event: {
        id: string
        event: string
        data: Record<string, unknown>
      }) => void
    } | undefined
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.listCommitteeRuns
      .mockResolvedValueOnce({ runs: [runningRun, nextRun] })
      .mockImplementationOnce(() => staleRefresh.promise)
      .mockResolvedValueOnce({ runs: [nextRun] })
    api.getCommitteeRun
      .mockResolvedValueOnce({
        run: runningRun,
        events: [],
        artifacts: [],
      })
      .mockImplementation((runId: string) => Promise.resolve({
        run: runId === 'run-2' ? nextRun : deletedRun,
        events: [],
        artifacts: [],
      }))
    api.streamCommitteeEvents.mockImplementation(
      async (_runId: string, handlers: typeof streamHandlers) => {
        streamHandlers = handlers
      },
    )

    render(
      <MemoryRouter>
        <CommitteePage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(streamHandlers).toBeDefined())
    await act(async () => {
      streamHandlers?.onEvent({
        id: '2-0',
        event: 'completed',
        data: {},
      })
    })
    await waitFor(() => expect(api.listCommitteeRuns).toHaveBeenCalledTimes(2))

    await userEvent.click(
      await screen.findByRole('button', { name: '删除记录' }),
    )
    await waitFor(() => expect(api.listCommitteeRuns).toHaveBeenCalledTimes(3))
    expect(
      await screen.findByText('run-2', { selector: '.committee-overview h2' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /run-live/ }),
    ).not.toBeInTheDocument()

    await act(async () => {
      staleRefresh.resolve({ runs: [deletedRun, nextRun, staleOtherRun] })
      await Promise.resolve()
    })
    await screen.findByRole('button', { name: /run-3/ })
    expect(
      screen.queryByRole('button', { name: /run-live/ }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByText('run-2', { selector: '.committee-overview h2' }),
    ).toBeInTheDocument()
  })
```

