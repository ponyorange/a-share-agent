# 数据子 Agent 实时进度展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在投研助手调用数据子 Agent 的阻塞期间，通过现有 SSE 实时展示脱敏的工具级工作进度，并在完成后保留精简轨迹。

**Architecture:** 新增基于 `ContextVar` 的请求级进度协议；主 Agent 流改为工作线程生产事件、外层生成器消费线程安全队列。数据子 Agent 在确定性的工具边界发出进度，前端解析 `subagent_progress` 并展示实时面板。

**Tech Stack:** Python 3.12、LangGraph、FastAPI SSE、React 19、TypeScript、Vitest、pytest。

## Global Constraints

- 只展示数据源、接口名、行数、截断状态和稳定错误码。
- 进度文案采用固定阶段映射：`emit_progress()` 与 `ProgressEvent` 不接受调用方自由文本 `message`；文案由模块内部根据 `step/status` 生成。
- `step` 与 `status` 做运行时枚举校验；`error_code` 须符合稳定码格式（小写字母开头，仅小写字母/数字/下划线，最长 64 字符），非法值拒绝。
- 不展示思维链、模型自由文本、Python 源码、完整参数、Provider 样例、原始数据、密钥或异常堆栈。
- 保持现有 `meta/tool/token/done/error` SSE 协议向后兼容。
- 不修改 Provider 协议、沙箱 HTTP 协议和 Mongo 消息 Schema。
- 非流式 `run_agent_chat()` 的公开签名和返回结构保持不变。
- 不新增第三方依赖。
- 未经用户明确要求不创建 Git commit；每个任务以测试通过和代码审查检查点结束。

---

## File Map

### 新增

- `backend/app/advisor/agent/progress.py`：进度事件类型、脱敏校验、ContextVar sink。
- `backend/tests/test_agent_progress.py`：进度隔离、脱敏和无 sink 行为。
- `backend/tests/test_agent_chat_progress.py`：阻塞工具期间的事件多路复用和持久化测试。
- `frontend-advisor/src/agentApi.test.ts`：SSE `subagent_progress` 解析测试。

### 修改

- `backend/app/advisor/agent/graph.py`：同步核心与队列桥接、进度轨迹持久化。
- `backend/app/advisor/agent/data_agent/delegate.py`：委派启动、完成和失败事件。
- `backend/app/advisor/agent/data_agent/provider_tools.py`：数据源、接口发现和 fetch 埋点。
- `backend/app/advisor/agent/data_agent/sandbox.py`：沙箱计算埋点。
- `backend/app/advisor/agent/data_agent/graph.py`：结果提交埋点。
- `backend/tests/test_data_agent_delegate.py`：委派进度测试。
- `backend/tests/test_data_agent_provider_tools.py`：Provider 工具进度测试。
- `backend/tests/test_data_agent_sandbox.py`：沙箱进度测试。
- `backend/tests/test_data_agent_graph.py`：提交结果进度测试。
- `frontend-advisor/src/agentApi.ts`：进度类型和回调。
- `frontend-advisor/src/pages/AgentChatPage.tsx`：实时进度状态和面板。
- `frontend-advisor/src/pages/AgentChatPage.test.tsx`：面板渲染、更新和折叠测试。
- `frontend-advisor/src/styles.css`：进度面板样式。

---

### Task 1: 请求级安全进度协议

**Files:**
- Create: `backend/app/advisor/agent/progress.py`
- Create: `backend/tests/test_agent_progress.py`

**Interfaces:**
- Produces: `ProgressEvent`, `bind_progress_sink()`, `emit_progress()`, `progress_to_tool_trace()`
- Consumes: Python `ContextVar` 和标准库 context manager

- [ ] **Step 1: 写进度事件与无 sink 的失败测试**

```python
# backend/tests/test_agent_progress.py
import threading

import pytest

from app.advisor.agent.progress import (
    ProgressEvent,
    ProgressValidationError,
    bind_progress_sink,
    emit_progress,
    progress_to_tool_trace,
)


def test_emit_progress_without_sink_is_noop():
    emit_progress(step="fetch", status="started")


def test_bound_sink_receives_only_allowlisted_fields():
    rows = []
    with bind_progress_sink(rows.append):
        emit_progress(
            step="fetch",
            status="completed",
            source="akshare",
            interface="stock_zh_index_daily_tx",
            rows=53,
            truncated=False,
        )
    assert rows == [
        {
            "phase": "data_agent",
            "step": "fetch",
            "status": "completed",
            "message": "已获取 53 行数据",
            "source": "akshare",
            "interface": "stock_zh_index_daily_tx",
            "rows": 53,
            "truncated": False,
        }
    ]


def test_progress_trace_never_contains_parameters_or_data():
    event = ProgressEvent(
        step="fetch",
        status="completed",
        source="akshare",
        interface="daily",
        rows=2,
    )
    trace = progress_to_tool_trace(event.as_dict())
    assert trace == {
        "tool": "data_agent.fetch",
        "content": "akshare/daily：2 行",
    }
    assert "params" not in str(trace)


def test_emit_progress_rejects_extra_fields():
    with pytest.raises(ProgressValidationError):
        emit_progress(step="fetch", status="started", message="自由文本")
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_agent_progress.py -q
```

Expected: collection fails because `app.advisor.agent.progress` does not exist.

- [ ] **Step 3: 实现固定字段、固定阶段文案、长度和枚举校验**

`ProgressEvent` 与 `emit_progress()` 不接受调用方 `message`；文案由模块内部
`_stage_message(step, status, ...)` 根据白名单 `step/status` 及结构化字段生成。
`error_code` 须匹配 `^[a-z][a-z0-9_]{0,63}$`，非法 `step/status/error_code` 或
额外字段（`message`、`metadata`、`params`、`raw` 等）抛出 `ProgressValidationError`。
`progress_to_tool_trace()` 仅使用内部固定文案与结构化字段，忽略事件字典中的
自由文本或敏感键。

```python
# backend/app/advisor/agent/progress.py — 关键接口
def emit_progress(
    *,
    step: str,
    status: str,
    source: str | None = None,
    interface: str | None = None,
    rows: int | None = None,
    truncated: bool | None = None,
    error_code: str | None = None,
) -> None: ...

def progress_to_tool_trace(event: dict[str, object]) -> dict[str, str]: ...
```

- [ ] **Step 4: 添加并发 ContextVar 隔离与负面测试**

使用两个线程分别绑定独立列表，断言每个列表只收到自己的 `source`，并断言退出
context manager 后再次 `emit_progress()` 不再写入。补充非法 `step/status/error_code`、
禁止额外字段、以及 trace 不含 metadata/raw/异常堆栈/Provider 样例的负面测试。

- [ ] **Step 5: 运行测试确认 GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_agent_progress.py -q
```

Expected: all tests pass.

- [ ] **Step 6: 审查检查点**

确认 `ProgressEvent` 无任何通用 `metadata`、`params` 或 `raw` 字段，避免调用方绕过白名单；
确认 `message` 仅由内部 `_stage_message()` 生成，调用方无法注入自由文本。

---

### Task 2: 主 Agent SSE 队列桥接

**Files:**
- Modify: `backend/app/advisor/agent/graph.py`
- Create: `backend/tests/test_agent_chat_progress.py`
- Modify: `backend/tests/test_committee_llm.py`

**Interfaces:**
- Consumes: `bind_progress_sink()`, `progress_to_tool_trace()`
- Produces: `iter_agent_chat_events()` 新增 `subagent_progress` 事件，但保持原公开签名

- [ ] **Step 1: 写阻塞期间可先收到进度的失败测试**

测试用 fake 同步核心：

```python
def test_progress_is_yielded_before_blocking_agent_finishes(monkeypatch):
    release = threading.Event()

    def fake_sync(*args, progress_trace, **kwargs):
        emit_progress(
            step="delegate",
            status="started",
        )
        release.wait(timeout=2)
        yield {"event": "done", "data": {"session_id": "s", "reply": "完成"}}

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")
    first = next(events)
    assert first["event"] == "subagent_progress"
    release.set()
    assert next(events)["event"] == "done"
```

- [ ] **Step 2: 运行单测确认 RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/test_agent_chat_progress.py -q
```

Expected: fails because `_iter_agent_chat_events_sync` and queue bridge do not exist.

- [ ] **Step 3: 提取现有同步核心**

将当前 `iter_agent_chat_events()` 主体移动到：

```python
def _iter_agent_chat_events_sync(
    user_id: str,
    message: str,
    *,
    session_id: str | None,
    progress_trace: list[dict[str, str]],
) -> Iterator[dict[str, Any]]:
    ...
```

保留现有 `meta/tool/token/done/error` 行为。在 `append_message()` 和 `done.tool_trace`
前，将 `progress_trace[-20:]` 与原 `tool_trace` 合并并限制总数：

```python
persisted_trace = [*progress_trace, *tool_trace][-20:]
append_message(..., tool_trace=persisted_trace)
done_trace = persisted_trace[-12:]
```

- [ ] **Step 4: 实现线程生产者和有界队列消费者**

```python
_STREAM_END = object()
_EVENT_QUEUE_SIZE = 128


def iter_agent_chat_events(...):
    output: queue.Queue[object] = queue.Queue(maxsize=_EVENT_QUEUE_SIZE)
    stopped = threading.Event()
    progress_trace: list[dict[str, str]] = []

    def put_required(value: object) -> None:
        while not stopped.is_set():
            try:
                output.put(value, timeout=0.1)
                return
            except queue.Full:
                continue

    def progress_sink(event: dict[str, object]) -> None:
        trace = progress_to_tool_trace(event)
        if not progress_trace or progress_trace[-1] != trace:
            progress_trace.append(trace)
        try:
            output.put_nowait({"event": "subagent_progress", "data": event})
        except queue.Full:
            pass

    def produce() -> None:
        try:
            with bind_progress_sink(progress_sink):
                for event in _iter_agent_chat_events_sync(
                    user_id,
                    message,
                    session_id=session_id,
                    progress_trace=progress_trace,
                ):
                    put_required(event)
        finally:
            put_required(_STREAM_END)

    context = contextvars.copy_context()
    worker = threading.Thread(target=context.run, args=(produce,), daemon=True)
    worker.start()
    try:
        while True:
            event = output.get()
            if event is _STREAM_END:
                return
            yield event  # type: ignore[misc]
    finally:
        stopped.set()
```

- [ ] **Step 5: 覆盖队列满、异常和非流式兼容**

新增测试：

- 进度队列满时可丢弃重复进度，但 `done` 最终到达；
- 同步核心抛异常时得到现有 `error`；
- `run_agent_chat()` 忽略进度并仍返回 `reply/tool_trace/disclaimer`；
- `inspect.signature()` 兼容测试仍通过；
- 两个并发聊天不会串进度。

- [ ] **Step 6: 运行后端测试**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/test_agent_progress.py \
  tests/test_agent_chat_progress.py \
  tests/test_committee_llm.py -q
```

Expected: all tests pass.

- [ ] **Step 7: 审查检查点**

确认客户端断开会设置 `stopped`；生产线程为 daemon；普通事件使用可靠写入，
只有 `subagent_progress` 允许在队列满时丢弃。

---

### Task 3: 数据子 Agent 工具边界埋点

**Files:**
- Modify: `backend/app/advisor/agent/data_agent/delegate.py`
- Modify: `backend/app/advisor/agent/data_agent/provider_tools.py`
- Modify: `backend/app/advisor/agent/data_agent/sandbox.py`
- Modify: `backend/app/advisor/agent/data_agent/graph.py`
- Modify: `backend/tests/test_data_agent_delegate.py`
- Modify: `backend/tests/test_data_agent_provider_tools.py`
- Modify: `backend/tests/test_data_agent_sandbox.py`
- Modify: `backend/tests/test_data_agent_graph.py`

**Interfaces:**
- Consumes: `emit_progress(step, status, message, source?, interface?, rows?, truncated?, error_code?)`
- Produces: 工具调用前后确定性事件

- [ ] **Step 1: 写 delegate 启动/完成/失败测试**

绑定 sink 后调用 fake `run_data_agent()`，断言顺序：

```python
assert [(e["step"], e["status"]) for e in events] == [
    ("delegate", "started"),
    ("delegate", "completed"),
]
```

异常场景断言最后一条为：

```python
{
    "phase": "data_agent",
    "step": "delegate",
    "status": "failed",
    "message": "数据子 Agent 执行失败",
    "error_code": "data_agent_failure",
}
```

- [ ] **Step 2: 写 Provider 工具进度测试**

分别覆盖：

- `list_data_sources`：started/completed；
- `search_data_interfaces`：含 `source`，不含 keyword；
- `get_data_interface`：含 `source/interface`；
- `fetch_provider_data`：started 后 completed，completed 含 `rows/truncated`；
- Provider 异常：failed 只含稳定错误码，不含原始异常或参数。

- [ ] **Step 3: 写沙箱与 submit 进度测试**

沙箱测试断言 `sandbox started → completed/failed`，并断言事件中不存在 `code`、
`dataset_ids_json` 或数据行。提交工具测试断言 `submit started → completed/failed`。

- [ ] **Step 4: 运行测试确认 RED**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/test_data_agent_delegate.py \
  tests/test_data_agent_provider_tools.py \
  tests/test_data_agent_sandbox.py \
  tests/test_data_agent_graph.py -q
```

Expected: new assertions fail because no progress is emitted.

- [ ] **Step 5: 在工具边界实现埋点**

使用固定文案和白名单字段。例如 fetch：

```python
emit_progress(
    step="fetch",
    status="started",
    source=source,
    interface=name,
)
try:
    payload = provider.fetch(name, params, limit=effective_limit)
    meta = workspace.create_dataset(source, name, params, payload)
except Exception:
    emit_progress(
        step="fetch",
        status="failed",
        source=source,
        interface=name,
        error_code="provider_error",
    )
    ...
emit_progress(
    step="fetch",
    status="completed",
    source=source,
    interface=name,
    rows=meta.returned,
    truncated=meta.truncated,
)
```

不要把 `params`、`params_json`、Provider 异常正文或工具返回 JSON传入
`emit_progress()`。

- [ ] **Step 6: 运行测试确认 GREEN**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/test_agent_progress.py \
  tests/test_data_agent_delegate.py \
  tests/test_data_agent_provider_tools.py \
  tests/test_data_agent_sandbox.py \
  tests/test_data_agent_graph.py -q
```

Expected: all tests pass.

- [ ] **Step 7: 审查检查点**

搜索所有 `emit_progress(` 调用，确认参数集合只来自 `ProgressEvent` 白名单，
并确认错误路径不会传 `str(exc)`。

---

### Task 4: 前端 SSE 协议解析

**Files:**
- Modify: `frontend-advisor/src/agentApi.ts`
- Create: `frontend-advisor/src/agentApi.test.ts`

**Interfaces:**
- Produces: `SubagentProgress` 类型和 `handlers.onSubagentProgress`
- Consumes: `event: subagent_progress`

- [ ] **Step 1: 写 SSE 解析失败测试**

```typescript
import { expect, it, vi } from 'vitest'
import { streamAgentChat } from './agentApi'

it('解析 subagent_progress SSE', async () => {
  const onSubagentProgress = vi.fn()
  const body = [
    'event: subagent_progress',
    'data: {"phase":"data_agent","step":"fetch","status":"completed","message":"已获取 53 行数据","source":"akshare","interface":"daily","rows":53,"truncated":false}',
    '',
    '',
  ].join('\n')
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(body))
          controller.close()
        },
      }), { status: 200 }),
    ),
  )

  await streamAgentChat('query', 's', { onSubagentProgress })
  expect(onSubagentProgress).toHaveBeenCalledWith({
    phase: 'data_agent',
    step: 'fetch',
    status: 'completed',
    message: '已获取 53 行数据',
    source: 'akshare',
    interface: 'daily',
    rows: 53,
    truncated: false,
  })
})
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd frontend-advisor
npm test -- src/agentApi.test.ts
```

Expected: fails because `onSubagentProgress` is not part of the handler type.

- [ ] **Step 3: 添加类型与事件分支**

```typescript
export type SubagentProgress = {
  phase: 'data_agent'
  step: 'delegate' | 'list_sources' | 'search' | 'describe' | 'fetch' | 'sandbox' | 'submit'
  status: 'started' | 'completed' | 'failed'
  message: string
  source?: string
  interface?: string
  rows?: number
  truncated?: boolean
  error_code?: string
}
```

在 handler 中新增：

```typescript
onSubagentProgress?: (progress: SubagentProgress) => void
```

在 SSE 分发中新增：

```typescript
} else if (eventName === 'subagent_progress') {
  handlers.onSubagentProgress?.(data as SubagentProgress)
```

并把注释更新为：

```typescript
/** SSE：meta → (tool | subagent_progress)* → token* → done */
```

- [ ] **Step 4: 运行测试确认 GREEN**

Run:

```bash
cd frontend-advisor
npm test -- src/agentApi.test.ts
```

Expected: test passes.

- [ ] **Step 5: 审查检查点**

确认未知 SSE 事件仍被忽略，原有 `onMeta/onTool/onToken/onDone/onError` 行为未改变。

---

### Task 5: 聊天页实时进度面板

**Files:**
- Modify: `frontend-advisor/src/pages/AgentChatPage.tsx`
- Modify: `frontend-advisor/src/pages/AgentChatPage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Consumes: `SubagentProgress`, `onSubagentProgress`
- Produces: `SubagentProgressPanel` 和去重更新函数

- [ ] **Step 1: 写进度条目合并与面板渲染测试**

从 `AgentChatPage.tsx` 导出纯函数：

```typescript
export function mergeSubagentProgress(
  current: SubagentProgress[],
  next: SubagentProgress,
): SubagentProgress[]
```

测试相同 `step/status/source/interface` 更新原项，不重复增加；不同阶段按顺序追加。

导出 `SubagentProgressPanel` 并测试：

```typescript
render(<SubagentProgressPanel items={[
  {
    phase: 'data_agent',
    step: 'fetch',
    status: 'completed',
    message: '已获取 53 行数据',
    source: 'akshare',
    interface: 'stock_zh_index_daily_tx',
    rows: 53,
    truncated: false,
  },
]} collapsed={false} />)

expect(screen.getByText('数据子 Agent')).toBeInTheDocument()
expect(screen.getByText(/akshare/)).toBeInTheDocument()
expect(screen.getByText(/stock_zh_index_daily_tx/)).toBeInTheDocument()
expect(screen.getByText(/53 行/)).toBeInTheDocument()
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd frontend-advisor
npm test -- src/pages/AgentChatPage.test.tsx
```

Expected: fails because exports do not exist.

- [ ] **Step 3: 实现状态、合并和生命周期**

在页面增加：

```typescript
const [liveSubagentProgress, setLiveSubagentProgress] =
  useState<SubagentProgress[]>([])
const [progressCollapsed, setProgressCollapsed] = useState(false)
```

发送前清空；处理进度时合并；首个 token 到达时折叠；`done/error/finally`
停止动画并保留本轮条目直至消息完成。切换会话、新建会话和删除当前会话时清空。

回调：

```typescript
onSubagentProgress: (progress) => {
  setLiveSubagentProgress((current) => mergeSubagentProgress(current, progress))
},
onToken: (delta) => {
  setProgressCollapsed(true)
  // 保留现有 token 累加逻辑
},
```

- [ ] **Step 4: 实现安全展示组件**

组件只读取 `SubagentProgress` 白名单字段，不使用 `dangerouslySetInnerHTML`。
失败状态显示 `error_code`；完成 fetch 显示 `rows` 和截断标签。

在最后一个 streaming 助手气泡旁渲染：

```tsx
{sending && index === messages.length - 1 && liveSubagentProgress.length > 0 ? (
  <SubagentProgressPanel
    items={liveSubagentProgress}
    collapsed={progressCollapsed}
  />
) : null}
```

- [ ] **Step 5: 添加样式**

在 `styles.css` 添加 `.subagent-progress`、`.subagent-progress-header`、
`.subagent-progress-list`、`.subagent-progress-item` 和状态修饰类。复用现有颜色变量，
不新增动画库；运行中仅使用 CSS pulse，并尊重 `prefers-reduced-motion`。

- [ ] **Step 6: 运行前端测试、lint 和构建**

Run:

```bash
cd frontend-advisor
npm test -- src/agentApi.test.ts src/pages/AgentChatPage.test.tsx
npm run lint
npm run build
```

Expected: tests pass, lint exits 0, build succeeds.

- [ ] **Step 7: 审查检查点**

确认长接口名可换行，进度面板不会撑破聊天宽度；收到回答 token 后默认折叠，
用户仍可展开查看。

---

### Task 6: 端到端回归与安全验证

**Files:**
- Modify only if verification reveals a defect in files listed above.

**Interfaces:**
- Verifies: Backend SSE → frontend parser → progress panel → final reply

- [ ] **Step 1: 运行后端相关测试**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/test_agent_progress.py \
  tests/test_agent_chat_progress.py \
  tests/test_data_agent_delegate.py \
  tests/test_data_agent_provider_tools.py \
  tests/test_data_agent_sandbox.py \
  tests/test_data_agent_graph.py \
  tests/test_committee_llm.py -q
```

Expected: all tests pass.

- [ ] **Step 2: 运行完整 backend 测试**

Run:

```bash
cd backend
.venv/bin/pytest -q
```

Expected: all existing and new tests pass.

- [ ] **Step 3: 运行前端完整验证**

Run:

```bash
cd frontend-advisor
npm test
npm run lint
npm run build
```

Expected: all tests pass, lint exits 0, build succeeds.

- [ ] **Step 4: 启动服务并验证 SSE 顺序**

使用现有 backend、frontend 和沙箱服务，发送：

> 用数据源查一下沪深300最近20个交易日的收盘价，并告诉我最新收盘、区间最高/最低，以及来源接口名。不要凭记忆编造。

浏览器 Network 中应在最终 `tool/token` 前看到：

```text
event: subagent_progress
data: {"phase":"data_agent","step":"delegate","status":"started",...}

event: subagent_progress
data: {"phase":"data_agent","step":"search","status":"started","source":"akshare",...}

event: subagent_progress
data: {"phase":"data_agent","step":"fetch","status":"completed","source":"akshare","interface":"...","rows":...}

event: subagent_progress
data: {"phase":"data_agent","step":"sandbox","status":"started",...}
```

- [ ] **Step 5: 验证 UI 和历史轨迹**

确认：

1. 请求开始数秒内出现“数据子 Agent”；
2. 显示数据源、接口、行数、截断或稳定错误码；
3. 首个最终回答 token 到达后进度面板折叠；
4. 最终回答正常；
5. 刷新并重新打开会话后，工具调用折叠区仍含 `data_agent.*` 脱敏轨迹。

- [ ] **Step 6: 执行敏感信息扫描**

检查浏览器 SSE、Mongo `agent_chat_messages.tool_trace` 和 backend 日志，确认不存在：

- API Key、Token、Authorization；
- `params_json` 或完整查询参数；
- Provider 样例或原始 20 日数据；
- Python 源码、异常堆栈、宿主路径；
- 模型思维链。

- [ ] **Step 7: 最终审查检查点**

记录实际测试数量、端到端事件顺序和任何已知限制；不做与实时进度无关的重构。
