# 投委会会议记录软删除实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为投委会增加仅限终态、保留审计数据的单条会议记录软删除功能。

**Architecture:** 在 `CommitteeRun` 和 Mongo 仓储层实现 `deleted_at/deleted_by` 原子软删除，普通查询统一过滤已删除记录；FastAPI 暴露用户隔离的 DELETE 接口；React 工作台只在终态详情展示删除入口，并在删除成功后安全切换到下一条记录。

**Tech Stack:** Python 3.12、Pydantic 2、PyMongo、FastAPI、React 19、TypeScript 6、Vitest、Testing Library、pytest。

## Global Constraints

- 仅 `completed`、`failed`、`cancelled` 终态会议可删除。
- 活跃会议不得自动取消，DELETE 必须返回 HTTP 409。
- 删除只写入 `deleted_at` 和 `deleted_by`，不得清除事件、产物、checkpoint、审批、订单或模拟交易。
- 普通列表和详情必须隐藏已删除记录；重复删除按 404 处理。
- 第一版只删除当前选中的单条记录，不实现批量删除或恢复入口。
- 当前工作区不是 Git 仓库；计划中的提交命令仅在后续初始化 Git 后执行，本次执行以测试结果和文件 diff 作为检查点。

---

## 文件结构

- `backend/app/advisor/committee/models.py`：声明软删除字段及字段一致性约束。
- `backend/app/advisor/committee/repository.py`：提供原子软删除并统一过滤普通查询。
- `backend/app/advisor/committee/routes.py`：暴露认证 DELETE API 和错误映射。
- `backend/tests/test_committee_repository.py`：覆盖仓储软删除、过滤、权限和审计保留。
- `backend/tests/test_committee_task5_review.py`：覆盖 DELETE HTTP 契约。
- `frontend-advisor/src/committee/committeeApi.ts`：提供删除请求函数。
- `frontend-advisor/src/committee/committeeApi.test.ts`：验证 DELETE 请求。
- `frontend-advisor/src/committee/CommitteePage.tsx`：实现确认、删除和选择切换。
- `frontend-advisor/src/committee/CommitteePage.test.tsx`：覆盖前端交互和失败行为。
- `frontend-advisor/src/styles.css`：提供危险操作的视觉样式。

---

### Task 1: 领域模型与仓储软删除

**Files:**
- Modify: `backend/app/advisor/committee/models.py:186-274`
- Modify: `backend/app/advisor/committee/repository.py:95-120,347-367,495`
- Test: `backend/tests/test_committee_repository.py`

**Interfaces:**
- Consumes: `CommitteeRun`, `RunStatus`, `RunNotFound`, `IllegalStatusTransition`, `VersionConflict`。
- Produces: `CommitteeRepository.soft_delete_run(user_id: str, run_id: str, *, deleted_at: datetime, deleted_by: str) -> CommitteeRun`。

- [ ] **Step 1: 写软删除仓储失败测试**

在 `backend/tests/test_committee_repository.py` 添加终态构造器和测试。测试直接查看 fake collection，证明会议文档被标记、事件和产物仍存在：

```python
def _terminal_run(user_id: str, run_id: str, status: RunStatus) -> CommitteeRun:
    fields = {
        "status": status,
        "started_at": NOW,
        "completed_at": NOW,
    }
    if status is RunStatus.FAILED:
        fields.update(error_code="boom", error_message="failed")
    return _run(user_id, run_id).model_copy(update=fields)


def test_soft_delete_terminal_run_hides_it_but_preserves_audit_rows():
    database = FakeDatabase()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    repository.create_run(_terminal_run("alice", "run-1", RunStatus.FAILED))
    repository.append_event(
        "alice", "run-1", event_type="failed", payload={"reason": "boom"}
    )
    repository.append_artifact(
        "alice", "run-1", kind="errors", payload=[{"code": "boom"}]
    )

    deleted = repository.soft_delete_run(
        "alice", "run-1", deleted_at=NOW, deleted_by="alice"
    )

    assert deleted.deleted_at == NOW
    assert deleted.deleted_by == "alice"
    assert deleted.version == 2
    assert repository.list_runs("alice") == []
    with pytest.raises(RunNotFound):
        repository.get_run("alice", "run-1")
    assert len(database["committee_events"].documents) == 1
    assert len(database["committee_artifacts"].documents) == 1
```

再添加活跃状态、跨用户和重复删除测试：

```python
def test_soft_delete_rejects_active_foreign_and_already_deleted_runs():
    repository = CommitteeRepository(FakeDatabase(), clock=lambda: NOW)
    repository.create_run(_run("alice", "active"))
    repository.create_run(_terminal_run("alice", "terminal", RunStatus.CANCELLED))

    with pytest.raises(IllegalStatusTransition):
        repository.soft_delete_run(
            "alice", "active", deleted_at=NOW, deleted_by="alice"
        )
    with pytest.raises(RunNotFound):
        repository.soft_delete_run(
            "mallory", "terminal", deleted_at=NOW, deleted_by="mallory"
        )

    repository.soft_delete_run(
        "alice", "terminal", deleted_at=NOW, deleted_by="alice"
    )
    with pytest.raises(RunNotFound):
        repository.soft_delete_run(
            "alice", "terminal", deleted_at=NOW, deleted_by="alice"
        )
```

- [ ] **Step 2: 运行测试并确认因功能缺失而失败**

Run:

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_repository.py::test_soft_delete_terminal_run_hides_it_but_preserves_audit_rows \
  tests/test_committee_repository.py::test_soft_delete_rejects_active_foreign_and_already_deleted_runs -q
```

Expected: FAIL，原因是 `CommitteeRepository` 尚无 `soft_delete_run` 或 `CommitteeRun` 尚无删除字段。

- [ ] **Step 3: 扩展模型字段和一致性校验**

在 `CommitteeRun` 增加：

```python
    deleted_at: datetime | None = None
    deleted_by: Annotated[
        str | None, Field(min_length=1, max_length=256)
    ] = None
```

在 `validate_state_invariants` 返回前增加：

```python
        if (self.deleted_at is None) != (self.deleted_by is None):
            raise ValueError("deleted_at and deleted_by must be set together")
        if self.deleted_at is not None:
            if self.status not in terminal:
                raise ValueError("only terminal runs may be deleted")
            if self.deleted_at < self.completed_at:
                raise ValueError("deleted_at cannot precede completion")
```

字段已有通用 UTC validator，不引入第二套时间处理。

- [ ] **Step 4: 实现查询过滤和原子软删除**

向 `CommitteeRepositoryProtocol` 增加：

```python
    def soft_delete_run(
        self,
        user_id: str,
        run_id: str,
        *,
        deleted_at: datetime,
        deleted_by: str,
    ) -> CommitteeRun: ...
```

将 `get_run` 查询改为：

```python
        document = self._runs.find_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "deleted_at": None,
            },
            {"_id": 0},
        )
```

将 `list_runs` 查询改为：

```python
            self._runs.find(
                {"user_id": user_id, "deleted_at": None},
                {"_id": 0},
            )
```

在 `request_cancel` 前实现：

```python
    def soft_delete_run(
        self,
        user_id: str,
        run_id: str,
        *,
        deleted_at: datetime,
        deleted_by: str,
    ) -> CommitteeRun:
        current = self.get_run(user_id, run_id)
        terminal = {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        if current.status not in terminal:
            raise IllegalStatusTransition(
                "only completed, failed, or cancelled runs can be deleted"
            )
        candidate = current.model_copy(
            update={
                "deleted_at": deleted_at,
                "deleted_by": deleted_by,
                "updated_at": deleted_at,
                "version": current.version + 1,
            }
        )
        candidate = CommitteeRun.model_validate(
            candidate.model_dump(mode="python")
        )
        document = self._runs.find_one_and_update(
            {
                "user_id": user_id,
                "run_id": run_id,
                "version": current.version,
                "status": current.status.value,
                "deleted_at": None,
            },
            {
                "$set": encode_bson(
                    {
                        "deleted_at": candidate.deleted_at,
                        "deleted_by": candidate.deleted_by,
                        "updated_at": candidate.updated_at,
                    }
                ),
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            try:
                latest = self.get_run(user_id, run_id)
            except RunNotFound:
                raise
            if latest.status not in terminal:
                raise IllegalStatusTransition(
                    "run status changed before deletion"
                )
            raise VersionConflict("run changed while it was deleted")
        return CommitteeRun.model_validate(_without_mongo_id(document))
```

- [ ] **Step 5: 调整 fake Mongo 匹配器并跑绿**

`FakeCollection` 文档会显式存储 `deleted_at=None`，现有 `_matches` 已可匹配；补充断言确保所有普通读取都带删除过滤：

```python
    run_queries = database["committee_runs"].queries
    assert any(query.get("deleted_at") is None for query in run_queries)
```

Run:

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_repository.py -q
```

Expected: 全部 PASS。

- [ ] **Step 6: 检查点**

```bash
git add backend/app/advisor/committee/models.py \
  backend/app/advisor/committee/repository.py \
  backend/tests/test_committee_repository.py
git commit -m "feat: add committee run soft delete repository"
```

当前无 Git 时跳过命令，记录本任务 pytest 输出。

---

### Task 2: 认证 DELETE HTTP API

**Files:**
- Modify: `backend/app/advisor/committee/routes.py:340-405`
- Modify: `backend/tests/test_committee_task5_review.py:58-120`

**Interfaces:**
- Consumes: `CommitteeRepository.soft_delete_run(...)`。
- Produces: `DELETE /api/advisor/committee/runs/{run_id}`，成功响应 `{run_id, deleted}`。

- [ ] **Step 1: 先把 DELETE 加入认证测试**

在 `test_http_committee_routes_require_authentication` 的参数列表加入：

```python
        ("delete", "/api/advisor/committee/runs/r", {}),
```

Run:

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_task5_review.py::test_http_committee_routes_require_authentication -q
```

Expected: FAIL，当前路由返回 405 而不是 401。

- [ ] **Step 2: 添加成功、404 和 409 路由测试**

在同一测试文件添加：

```python
def test_http_delete_run_is_user_scoped_and_maps_domain_errors(monkeypatch):
    repository = Mock()
    repository.soft_delete_run.return_value = SimpleNamespace()
    monkeypatch.setattr(committee_routes, "_repository", lambda: repository)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "alice",
        "username": "alice",
    }
    try:
        response = TestClient(app).delete(
            "/api/advisor/committee/runs/run-1"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "deleted": True}
    args = repository.soft_delete_run.call_args
    assert args.args[:2] == ("alice", "run-1")
    assert args.kwargs["deleted_by"] == "alice"
    assert args.kwargs["deleted_at"].tzinfo is not None


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (committee_routes.RunNotFound("missing"), 404),
        (committee_routes.IllegalStatusTransition("active"), 409),
        (committee_routes.VersionConflict("changed"), 409),
    ],
)
def test_http_delete_run_maps_errors(monkeypatch, error, status):
    repository = Mock()
    repository.soft_delete_run.side_effect = error
    monkeypatch.setattr(committee_routes, "_repository", lambda: repository)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "alice",
        "username": "alice",
    }
    try:
        response = TestClient(app).delete(
            "/api/advisor/committee/runs/run-1"
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == status
```

- [ ] **Step 3: 运行并确认 DELETE 路由测试失败**

Run:

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_task5_review.py::test_http_delete_run_is_user_scoped_and_maps_domain_errors \
  tests/test_committee_task5_review.py::test_http_delete_run_maps_errors -q
```

Expected: FAIL，原因是 DELETE 路由不存在。

- [ ] **Step 4: 实现 DELETE 路由**

在 `get_run` 与 `cancel_run` 之间添加：

```python
@router.delete("/runs/{run_id}")
def delete_run(
    run_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    uid = _uid(user)
    try:
        _repository().soft_delete_run(
            uid,
            run_id,
            deleted_at=datetime.now(timezone.utc),
            deleted_by=uid,
        )
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    except (IllegalStatusTransition, VersionConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id, "deleted": True}
```

该接口不调用 `_infra()`，因此不会触碰 Redis、RQ 或 checkpoint。

- [ ] **Step 5: 运行后端相关测试**

Run:

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_committee_repository.py \
  tests/test_committee_task5_review.py -q
```

Expected: 全部 PASS。

- [ ] **Step 6: 检查点**

```bash
git add backend/app/advisor/committee/routes.py \
  backend/tests/test_committee_task5_review.py
git commit -m "feat: expose committee run delete API"
```

当前无 Git 时跳过命令，记录本任务 pytest 输出。

---

### Task 3: 前端 API 客户端

**Files:**
- Modify: `frontend-advisor/src/committee/committeeApi.ts:159-180`
- Modify: `frontend-advisor/src/committee/committeeApi.test.ts:1-125`

**Interfaces:**
- Consumes: 后端 DELETE 路由。
- Produces: `deleteCommitteeRun(runId: string, signal?: AbortSignal): Promise<{run_id: string; deleted: true}>`。

- [ ] **Step 1: 写 DELETE 请求失败测试**

导入 `deleteCommitteeRun`，并在 `committee API` describe 中添加：

```typescript
it('删除会议使用 DELETE、认证头和编码后的 run id', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    response({ run_id: 'run/1', deleted: true }),
  )

  await deleteCommitteeRun('run/1')

  const [url, init] = fetchMock.mock.calls[0]
  expect(url).toBe('/api/advisor/committee/runs/run%2F1')
  expect(init?.method).toBe('DELETE')
  expect(new Headers(init?.headers).get('Authorization')).toBe(
    'Bearer secret',
  )
})
```

- [ ] **Step 2: 运行并确认测试失败**

Run:

```bash
cd frontend-advisor
npm test -- --run src/committee/committeeApi.test.ts
```

Expected: FAIL，`deleteCommitteeRun` 尚未导出。

- [ ] **Step 3: 实现 API 函数**

在 `cancelCommitteeRun` 前添加：

```typescript
export function deleteCommitteeRun(runId: string, signal?: AbortSignal) {
  return request<{ run_id: string; deleted: true }>(
    `/runs/${encodeURIComponent(runId)}`,
    { method: 'DELETE', signal },
  )
}
```

- [ ] **Step 4: 运行 API 测试**

Run:

```bash
cd frontend-advisor
npm test -- --run src/committee/committeeApi.test.ts
```

Expected: 全部 PASS。

- [ ] **Step 5: 检查点**

```bash
git add frontend-advisor/src/committee/committeeApi.ts \
  frontend-advisor/src/committee/committeeApi.test.ts
git commit -m "feat: add committee delete API client"
```

当前无 Git 时跳过命令，记录 Vitest 输出。

---

### Task 4: 工作台删除交互

**Files:**
- Modify: `frontend-advisor/src/committee/CommitteePage.tsx:1-390`
- Modify: `frontend-advisor/src/committee/CommitteePage.test.tsx:10-605`
- Modify: `frontend-advisor/src/styles.css:417-420`

**Interfaces:**
- Consumes: `deleteCommitteeRun(runId, signal)`。
- Produces: 终态详情中的“删除记录”入口，以及成功后的下一条自动选择。

- [ ] **Step 1: 在测试 mock 中加入删除 API**

在 hoisted API mock 添加：

```typescript
  deleteCommitteeRun: vi.fn(),
```

在 `beforeEach` 添加：

```typescript
  api.deleteCommitteeRun.mockResolvedValue({
    run_id: 'run-1',
    deleted: true,
  })
```

- [ ] **Step 2: 写显示范围和取消确认测试**

```typescript
it('仅终态会议显示删除记录且取消确认不发请求', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  render(
    <MemoryRouter>
      <CommitteePage />
    </MemoryRouter>,
  )

  const button = await screen.findByRole('button', {
    name: '删除记录',
  })
  await userEvent.click(button)

  expect(confirm).toHaveBeenCalledWith(
    '只会从历史列表隐藏此会议，不会撤销审批或订单。确认删除？',
  )
  expect(api.deleteCommitteeRun).not.toHaveBeenCalled()

  api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
})
```

为活跃状态单独渲染并断言：

```typescript
it('进行中的会议不显示删除记录', async () => {
  api.listCommitteeRuns.mockResolvedValue({ runs: [runningRun] })
  api.getCommitteeRun.mockResolvedValue({
    run: runningRun,
    events: [],
    artifacts: [],
  })
  render(
    <MemoryRouter>
      <CommitteePage />
    </MemoryRouter>,
  )
  await screen.findByRole('button', { name: '取消会议' })
  expect(
    screen.queryByRole('button', { name: '删除记录' }),
  ).not.toBeInTheDocument()
})
```

- [ ] **Step 3: 写删除成功后切换下一条测试**

```typescript
it('确认删除后刷新历史并自动选择下一条', async () => {
  const nextRun = { ...completedRun, run_id: 'run-2' }
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  api.listCommitteeRuns
    .mockResolvedValueOnce({ runs: [completedRun, nextRun] })
    .mockResolvedValueOnce({ runs: [nextRun] })
  api.getCommitteeRun
    .mockResolvedValueOnce({
      run: completedRun,
      events: [],
      artifacts: [],
    })
    .mockResolvedValueOnce({
      run: nextRun,
      events: [],
      artifacts: [],
    })

  render(
    <MemoryRouter>
      <CommitteePage />
    </MemoryRouter>,
  )
  await userEvent.click(
    await screen.findByRole('button', { name: '删除记录' }),
  )

  expect(api.deleteCommitteeRun).toHaveBeenCalledWith(
    'run-1',
    expect.any(AbortSignal),
  )
  await waitFor(() =>
    expect(api.getCommitteeRun).toHaveBeenLastCalledWith(
      'run-2',
      expect.any(AbortSignal),
    ),
  )
  expect(
    await screen.findByText('run-2', {
      selector: '.committee-overview h2',
    }),
  ).toBeInTheDocument()
})
```

- [ ] **Step 4: 写空列表和失败保留测试**

```typescript
it('删除最后一条后显示空状态', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  api.listCommitteeRuns
    .mockResolvedValueOnce({ runs: [completedRun] })
    .mockResolvedValueOnce({ runs: [] })
  render(
    <MemoryRouter>
      <CommitteePage />
    </MemoryRouter>,
  )
  await userEvent.click(
    await screen.findByRole('button', { name: '删除记录' }),
  )
  expect(
    await screen.findByText('选择或发起一次会议'),
  ).toBeInTheDocument()
})


it('删除失败时保留当前详情并显示错误', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  api.deleteCommitteeRun.mockRejectedValue(new Error('删除失败'))
  render(
    <MemoryRouter>
      <CommitteePage />
    </MemoryRouter>,
  )
  await userEvent.click(
    await screen.findByRole('button', { name: '删除记录' }),
  )
  expect(await screen.findByRole('alert')).toHaveTextContent('删除失败')
  expect(
    screen.getByText('run-1', { selector: '.committee-overview h2' }),
  ).toBeInTheDocument()
})
```

- [ ] **Step 5: 运行并确认交互测试失败**

Run:

```bash
cd frontend-advisor
npm test -- --run src/committee/CommitteePage.test.tsx
```

Expected: FAIL，页面尚无“删除记录”按钮。

- [ ] **Step 6: 实现删除状态重置和自动选择**

在导入列表加入 `deleteCommitteeRun`。

在 `CommitteePage` 添加：

```typescript
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
      if (controller.signal.aborted || !mountedRef.current) return

      streamAbort.current?.abort()
      detailAbort.current?.abort()
      if (refreshTimer.current != null) {
        window.clearTimeout(refreshTimer.current)
        refreshTimer.current = null
      }
      ++selectionRef.current
      selectedIdRef.current = undefined
      setSelectedId(undefined)
      setSelectedRun(undefined)
      setArtifacts([])
      dispatch({ type: 'reset' })
      setShowApproval(false)
      setStreamState('未连接')

      const items = await refreshRuns()
      if (mountedRef.current && items[0]) {
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

在终态详情操作区添加：

```tsx
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
```

- [ ] **Step 7: 添加危险操作样式**

在 `.committee-actions` 附近添加：

```css
.committee-delete {
  color: var(--down, #e85d4c);
  border-color: color-mix(in srgb, var(--down, #e85d4c) 55%, transparent);
}

.committee-delete:hover:not(:disabled) {
  background: color-mix(in srgb, var(--down, #e85d4c) 12%, transparent);
}
```

- [ ] **Step 8: 运行前端测试、类型检查和 lint**

Run:

```bash
cd frontend-advisor
npm test -- --run src/committee/CommitteePage.test.tsx \
  src/committee/committeeApi.test.ts
npm run build
npm run lint
```

Expected: 测试全部 PASS；TypeScript 构建成功；lint 无新增错误。

- [ ] **Step 9: 检查点**

```bash
git add frontend-advisor/src/committee/CommitteePage.tsx \
  frontend-advisor/src/committee/CommitteePage.test.tsx \
  frontend-advisor/src/styles.css
git commit -m "feat: add committee record delete interaction"
```

当前无 Git 时跳过命令，记录 Vitest、build 和 lint 输出。

---

### Task 5: 全量回归与运行态验证

**Files:**
- Verify only; no production file changes expected.

**Interfaces:**
- Consumes: Tasks 1-4 的后端和前端接口。
- Produces: 可交付的验证证据。

- [ ] **Step 1: 运行全量后端测试**

Run:

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
```

Expected: 全部 PASS；仅允许项目已有的第三方弃用警告。

- [ ] **Step 2: 运行全量前端测试**

Run:

```bash
cd frontend-advisor
npm test
```

Expected: 全部 PASS。

- [ ] **Step 3: 检查改动文件 lint**

使用 IDE linter检查：

- `backend/app/advisor/committee/models.py`
- `backend/app/advisor/committee/repository.py`
- `backend/app/advisor/committee/routes.py`
- `frontend-advisor/src/committee/committeeApi.ts`
- `frontend-advisor/src/committee/CommitteePage.tsx`
- `frontend-advisor/src/styles.css`

Expected: 无新增诊断。

- [ ] **Step 4: 本地 API 冒烟验证**

在服务已启动且已登录的浏览器中：

1. 打开 `/agent/committee`。
2. 选择一条 `failed` 或 `cancelled` 会议。
3. 点击“删除记录”，取消一次，确认记录仍存在。
4. 再次点击并确认，验证记录从列表消失且下一条自动加载。
5. 验证 Mongo `committee_runs` 记录保留且含 `deleted_at/deleted_by`。
6. 验证对应 `committee_events` 和 `committee_artifacts` 数量未减少。

Expected: UI、API 和审计保留均符合设计。

- [ ] **Step 5: 最终检查点**

若工作区之后初始化为 Git 仓库：

```bash
git status --short
git log --oneline -4
```

Expected: 仅包含本计划文件和实现改动；提交历史按任务分离。
