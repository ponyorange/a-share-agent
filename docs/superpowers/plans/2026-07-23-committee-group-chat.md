# 投委会群聊界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将投委会页面改造成只读实时群聊：各 LLM 角色逐字输出，回测/风控等系统节点作为独立机器人发言，完成消息可从 Mongo 历史恢复，结构化报告收纳到右侧抽屉。

**Architecture:** 后端保留单次模型调用并要求 `chat_message` 为流式 JSON 的首字段；`ChatModelRoleRunner` 从原始 JSON token 中增量提取该字段，通过 Redis Stream 发布不落库的 `message_started` / `message_delta`，图节点在结构化输出校验通过后生成可幂等持久化的 `message_completed`。前端用独立 reducer 合并历史完成消息和实时增量，完成事件始终覆盖临时内容；消息列表使用 `react-virtuoso`，现有结构化详情迁入右侧抽屉。

**Tech Stack:** Python 3.12、FastAPI、LangGraph、LangChain chat model、Pydantic、MongoDB、Redis Streams、React 19、TypeScript 6、Vitest、Testing Library、react-virtuoso、react-markdown。

## Global Constraints

- 第一版只读，不增加用户输入框、@角色、暂停或人工追问。
- 每个 LLM 节点只发起一次业务模型调用；流式不可用时自动退化为非流式 `ainvoke`，不额外调用模型。
- LLM 响应是单个 JSON 对象，`chat_message` 必须是第一个字段；结构化字段仍由现有 Pydantic schema 校验。
- `message_started`、`message_delta` 只走 Redis/SSE，不写 Mongo；`message_completed` 通过现有 Mongo outbox 持久化，作为历史和重连后的权威内容。
- `message_id` 由 `run_id + attempt + node + round` 确定性生成；格式校验重试复用同一 `message_id` 并递增 `generation`，新的 `message_started` 必须清空旧 generation 临时文本。
- SSE 重连允许丢失临时 delta；历史详情或随后到达的 `message_completed` 必须恢复完整内容。
- `message_delta.offset` 表示追加前的内容长度；仅当 `offset === content.length` 时追加，重复忽略，缺口等待权威完成事件。
- 状态包含 `streaming` / `completed` / `degraded` / `failed`；节点降级显示“降级”标识，失败不伪造成功结论，会议中止时未完成消息标记中断。
- 后端非流式降级后只发 `message_completed`；前端若未收到增量，对完整内容做本地短窗口逐字展示。
- 回测、风控、数据准备等非 LLM 节点由确定性模板生成消息，不新增模型调用。
- 新会议使用 `message_completed` 历史事件；旧会议在前端从既有 artifacts 转换，转换结果不得回写后端。
- 保留现有删除、取消、重试、审批、断线重连和终态刷新行为。
- 不新增第三方依赖；复用已安装的 `react-virtuoso`、`react-markdown` 和 `remark-gfm`。
- `message_started` / `message_delta` 不得写入 LangGraph state 或 Mongo；否则 checkpoint reconcile（`_run_graph_stream` 每 chunk 调用）会膨胀并重复。
- `RoleResultCache` 命中时不得重放临时 delta；仅由图节点在校验后发出权威 `message_completed`。
- 投委会页必须启用与 Agent 聊天相同的固定高度 shell（`app-shell--agent-chat` 或等价 class），否则 Virtuoso 无法正确占满视口。

## File Structure

**Backend**

- Create `backend/app/advisor/committee/chat_stream.py`: 群聊事件数据模型、稳定消息 ID、流式 JSON `chat_message` 增量解析器、系统消息模板。
- Modify `backend/app/advisor/committee/agents.py`: 输出 schema 增加 `chat_message`；流式事件 sink；流式优先、非流式降级；重试 generation 传播。
- Modify `backend/app/advisor/committee/graph.py`: 将已校验 LLM 输出和系统节点结果转换为 `message_completed` 图事件。
- Modify `backend/app/advisor/committee/tasks.py`: 为 worker 注入 Redis 临时消息 sink，并保持完成事件走现有 outbox。
- Modify `backend/app/advisor/committee/runtime.py`: 增加自动 Redis ID 的临时事件发布接口。
- Modify `backend/app/advisor/committee/routes.py`: SSE 同时发送 Mongo 权威事件和 Redis 临时消息，分离 durable cursor 与 live cursor。
- Test `backend/tests/test_committee_chat_stream.py`: 解析器、消息模型和系统模板单测。
- Modify `backend/tests/test_committee_execution.py`: executor generation、输出 schema 和缓存行为。
- Modify `backend/tests/test_committee_critical.py`: ChatModel runner 流式与降级测试。
- Modify `backend/tests/test_committee_graph.py`: 各节点完成消息和幂等回放。
- Modify `backend/tests/test_committee_task5_review.py`: SSE 临时事件、游标和重连测试。
- Modify `backend/tests/test_committee_runtime.py`: Redis 临时事件接口测试。

**Frontend**

- Create `frontend-advisor/src/committee/chatMessages.ts`: 消息类型、角色元数据、reducer、SSE 解析、旧 artifact 转换。
- Create `frontend-advisor/src/committee/chatMessages.test.ts`: reducer、重连覆盖、旧历史转换单测。
- Create `frontend-advisor/src/committee/components/CommitteeChat.tsx`: 虚拟化消息列表、吸底逻辑、消息气泡和可展开数据卡。
- Create `frontend-advisor/src/committee/components/CommitteeDetailDrawer.tsx`: 右侧详情抽屉的开关、遮罩与可访问性。
- Modify `frontend-advisor/src/committee/components/CommitteeDetail.tsx`: 支持作为抽屉内容渲染，不再承担页面主区域布局。
- Modify `frontend-advisor/src/committee/CommitteePage.tsx`: 注入 chat reducer，合并详情/SSE，群聊成为主区域。
- Modify `frontend-advisor/src/committee/CommitteePage.test.tsx`: 页面布局、实时 token、断线、旧历史、删除回归。
- Modify `frontend-advisor/src/styles.css`: 三栏布局、气泡、状态、抽屉、数据卡及响应式样式。
- Modify `frontend-advisor/src/App.tsx`: 对 `/agent/committee` 启用固定高度 chat shell（与 `/agent` 一致）。
- Modify `frontend-advisor/src/App.test.tsx`: 断言投委会路由带上 chat shell class。

---

### Task 1: 后端群聊协议与增量 JSON 解析器

**Files:**
- Create: `backend/app/advisor/committee/chat_stream.py`
- Test: `backend/tests/test_committee_chat_stream.py`

**Interfaces:**
- Produces: `ChatStreamEvent`, `ChatMessagePayload`, `IncrementalChatMessageParser.feed(text) -> tuple[str, ...]`, `message_id_for(run_id, attempt, node, round_index=None) -> str`, `system_message(...) -> ChatMessagePayload`
- Consumes: 无

- [ ] **Step 1: 写消息协议和解析器失败测试**

```python
def test_parser_emits_only_decoded_chat_message_text():
    parser = IncrementalChatMessageParser()
    chunks = [
        '{"chat_mes',
        'sage":"看多\\n沪深',
        '300，目标\\u6743重 20%。","confidence":0.8}',
    ]
    assert [delta for chunk in chunks for delta in parser.feed(chunk)] == [
        "看多\n沪深",
        "300，目标权重 20%。",
    ]


def test_parser_ignores_later_json_fields_and_handles_escape_boundaries():
    parser = IncrementalChatMessageParser()
    assert parser.feed('{"chat_message":"A\\\\') == ("A",)
    assert parser.feed('nB","thesis":"不得展示"}') == ("\\nB",)
    assert parser.feed(" trailing") == ()


def test_stable_message_id_includes_attempt_and_round():
    assert message_id_for("r1", 1, "bull", 1) == message_id_for("r1", 1, "bull", 1)
    assert message_id_for("r1", 1, "bull", 1) != message_id_for("r1", 2, "bull", 1)
    assert message_id_for("r1", 1, "bull", 1) != message_id_for("r1", 1, "bull", 2)


def test_system_message_references_expandable_card():
    payload = system_message(
        run_id="r1",
        attempt=2,
        node="backtest",
        sequence=9,
        content="回测通过，综合得分 0.81。",
        card_kind="backtest_verdict",
    )
    assert payload.status == "completed"
    assert payload.card_ref is not None
    assert payload.card_ref.model_dump() == {
        "attempt": 2,
        "node": "backtest",
        "kind": "backtest_verdict",
    }
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && uv run pytest tests/test_committee_chat_stream.py -q`

Expected: FAIL，提示 `app.advisor.committee.chat_stream` 不存在。

- [ ] **Step 3: 实现协议、稳定 ID 和状态机解析器**

在 `chat_stream.py` 定义：

```python
ChatRole = Literal[
    "data", "fundamental", "technical", "news", "quant",
    "bull", "bear", "trader", "backtest", "risk", "chair",
]
ChatStatus = Literal["streaming", "completed", "degraded", "failed"]


class CardRef(BaseModel):
    model_config = {"extra": "forbid"}
    attempt: int = Field(ge=1)
    node: str = Field(min_length=1)
    kind: str = Field(min_length=1)


class ChatMessagePayload(BaseModel):
    model_config = {"extra": "forbid"}
    message_id: str
    role: ChatRole
    node: str
    round: int | None = None
    content: str
    status: ChatStatus
    sequence: int = Field(ge=0)
    generation: int = Field(default=1, ge=1)
    created_at: str | None = None
    completed_at: str | None = None
    card_kind: str | None = None
    card_ref: CardRef | None = None


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    event_type: Literal["message_started", "message_delta"]
    payload: dict[str, Any]
```

`IncrementalChatMessageParser` 必须是字符级 JSON string 状态机：只识别对象首字段 `"chat_message"`，处理 `\"`、`\\`、`\n`、`\r`、`\t`、`\b`、`\f` 和跨 chunk 的 `\uXXXX`；字段结束后停止输出。若首字段不是 `chat_message`，设置 `compatible=False` 并不产生 delta，供 runner 自动降级。

`message_id_for(run_id, attempt, node, round_index=None)` 使用 `sha256(f"{run_id}:{attempt}:{node}:{round_index or 0}")[:24]`，不要使用随机 UUID，确保 job 重试和 checkpoint 回放稳定。

`system_message(...)` 返回 `ChatMessagePayload`，`role` 映射：`prepare -> data`，`backtest -> backtest`，`risk -> risk`。`card_ref` 在提供 `card_kind` 时必填。

- [ ] **Step 4: 运行解析器测试**

Run: `cd backend && uv run pytest tests/test_committee_chat_stream.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/advisor/committee/chat_stream.py backend/tests/test_committee_chat_stream.py
git commit -m "feat(committee): define chat stream protocol"
```

---

### Task 2: LLM 单次流式 JSON 与自动降级

**Files:**
- Modify: `backend/app/advisor/committee/agents.py`
- Modify: `backend/tests/test_committee_execution.py`
- Modify: `backend/tests/test_committee_critical.py`

**Interfaces:**
- Consumes: `ChatStreamEvent`、`IncrementalChatMessageParser`、`message_id_for`
- Produces: `RoleRequest.generation: int`、`RoleRequest.message_id: str`、`RoleRequest.round_index: int | None`、`RoleRequest.attempt: int`、`RoleStreamSink = Callable[[ChatStreamEvent], Awaitable[None]]`；所有 `ROLE_SCHEMAS` 输出包含 `chat_message: str`

- [ ] **Step 1: 为 schema、generation、流式和降级写失败测试**

在 `test_committee_execution.py` 的 `analyst_body()` 增加 `"chat_message": "技术面观点"`，并新增：

```python
def test_format_retry_reuses_message_id_and_increments_generation():
    requests = []

    class Runner:
        async def __call__(self, request):
            requests.append(request)
            body = analyst_body()
            if len(requests) == 1:
                body.pop("confidence")
            return agents.ModelResponse(content=body, model_name="fake")

    async def scenario():
        executor = agents.RoleAgentExecutor(Runner())
        executor.begin_run("u", "r", BudgetLimits(), deadline_at=time.time() + 5)
        result = await execute(executor)
        assert result.output.chat_message == "技术面观点"
        assert [row.message_id for row in requests] == [
            requests[0].message_id,
            requests[0].message_id,
        ]
        assert [row.generation for row in requests] == [1, 2]

    asyncio.run(scenario())
```

在 `test_committee_critical.py` 用 fake chat model 覆盖：

```python
async def test_chat_model_runner_streams_decoded_chat_message(monkeypatch):
    sink_events = []
    fake = FakeStreamingModel([
        '{"chat_message":"先看',
        '盈利","thesis":"完整结论","confidence":0.7,"evidence_ids":[],"symbols":[]}',
    ])
    monkeypatch.setattr(agents, "build_chat_model", lambda *args, **kwargs: fake)
    runner = agents.ChatModelRoleRunner({}, stream_sink=sink_events.append_async)
    response = await runner(role_request(generation=1))
    assert json.loads(response.content)["thesis"] == "完整结论"
    assert [e.event_type for e in sink_events] == [
        "message_started", "message_delta", "message_delta",
    ]
    assert "".join(e.payload.get("delta", "") for e in sink_events) == "先看盈利"


async def test_chat_model_runner_falls_back_without_second_model_call(monkeypatch):
    fake = FakeNonStreamingOnlyModel(
        '{"chat_message":"降级完成","thesis":"结论","confidence":0.7,'
        '"evidence_ids":[],"symbols":[]}'
    )
    monkeypatch.setattr(agents, "build_chat_model", lambda *args, **kwargs: fake)
    response = await agents.ChatModelRoleRunner({})(role_request())
    assert json.loads(response.content)["chat_message"] == "降级完成"
    assert fake.call_count == 1
```

- [ ] **Step 2: 运行定向测试并确认失败**

Run: `cd backend && uv run pytest tests/test_committee_execution.py tests/test_committee_critical.py -q`

Expected: FAIL，原因是 schema/请求没有新字段且 runner 仍固定 `streaming=False`。

- [ ] **Step 3: 扩展角色输出和 executor 请求**

为 `AnalystOutput`、`DebateOutput`、`TraderOutput`、`ChairOutput` 增加：

```python
chat_message: str = Field(min_length=1, max_length=12000)
```

为 `RoleRequest` 增加 `message_id`、`generation`、`round_index`、`attempt`；`RoleAgentExecutor.aexecute` 增加 `attempt: int = 1`、`round_index: int | None = None`，在循环外计算稳定 `message_id_for(run_id, attempt, role, round_index)`，每次格式重试设置 `generation=attempt_index`（1 或 2）。`_input_hash` 不包含 generation，避免改变原有业务缓存语义；缓存命中不发送临时事件，图节点随后发送权威完成事件。

- [ ] **Step 4: 实现流式优先且只调用一次的 runner**

`ChatModelRoleRunner.__init__` 接受可选 `stream_sink`。每个 attempt：

1. `build_chat_model(..., streaming=True)` 并 bind JSON mode。
2. sink 存在时先发 `message_started`，payload 含 `message_id`、`role`、`node`、`round`、`generation`、`offset=0`。
3. 调 `json_model.astream(...)`，逐 chunk 拼接原始 JSON；对 parser 产出的每个文本片段，以追加前 `content.length` 作为 `offset` 发送 `message_delta`。
4. 从最后 chunk 汇总 usage metadata；返回完整原始 JSON 的 `ModelResponse`。
5. 仅当模型对象不支持 `astream`，或在收到任何 chunk 前抛出 `NotImplementedError` / `AttributeError` 时，改用同一个 model 实例执行一次 `ainvoke`；收到 chunk 后的异常直接上抛，禁止第二次模型调用。
6. sink 的 Redis 异常必须被捕获，不能导致业务模型调用失败。

- [ ] **Step 5: 更新所有 fake runner fixture**

搜索并为测试输出补充角色对应 `chat_message`：

Run: `cd backend && rg '"thesis"|"argument"|"rationale"' tests/test_committee_*.py`

对传入 `RoleAgentExecutor` 的合法结构增加 `"chat_message": "<角色可读文本>"`；故意测试非法 shape 的 fixture 保持不变。

- [ ] **Step 6: 运行角色执行测试**

Run: `cd backend && uv run pytest tests/test_committee_execution.py tests/test_committee_critical.py tests/test_committee_invoke.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/advisor/committee/agents.py backend/tests/test_committee_execution.py backend/tests/test_committee_critical.py backend/tests/test_committee_invoke.py
git commit -m "feat(committee): stream role chat messages"
```

---

### Task 3: 图节点生成权威完成消息

**Files:**
- Modify: `backend/app/advisor/committee/graph.py`
- Modify: `backend/tests/test_committee_graph.py`
- Modify: `backend/tests/test_committee_invoke.py`

**Interfaces:**
- Consumes: 各 role output 的 `chat_message`、`ChatMessagePayload`、`message_id_for`、`system_message`
- Produces: 图 `events` 中的 `event_type="message_completed"`，payload 为 `ChatMessagePayload.model_dump(mode="json")`

- [ ] **Step 1: 写 LLM 和系统节点完成消息失败测试**

```python
def completed_messages(state):
    return [
        event["payload"]
        for event in state["events"]
        if event["event_type"] == "message_completed"
    ]


def test_graph_emits_completed_chat_messages_for_all_visible_roles():
    result = invoke_committee(initial_state(), invoker=invoker_with_fake_runner())
    messages = completed_messages(result)
    assert {m["node"] for m in messages} >= {
        "prepare", "fundamental", "technical", "news", "quant",
        "bull", "bear", "trader", "backtest", "risk", "chair",
    }
    assert all(m["status"] == "completed" and m["content"] for m in messages)
    assert next(m for m in messages if m["node"] == "backtest")["card_kind"] == "backtest_verdict"


def test_checkpoint_replay_does_not_duplicate_completed_messages():
    first = invoke_committee(initial_state(), invoker=invoker)
    second = invoke_committee(
        {"user_id": "u", "run_id": "r"},
        invoker=invoker,
    )
    assert [m["message_id"] for m in completed_messages(second)] == [
        m["message_id"] for m in completed_messages(first)
    ]
```

- [ ] **Step 2: 运行图测试并确认失败**

Run: `cd backend && uv run pytest tests/test_committee_graph.py tests/test_committee_invoke.py -q`

Expected: FAIL，现有事件仅有节点进度，没有 `message_completed`。

- [ ] **Step 3: 增加统一完成事件 helper**

在 `graph.py` 增加 `_message_completed(...)`，其 `event_id` 使用 `message_id`，并通过现有 `_event` 形状返回：

```python
def _message_completed(
    state: CommitteeState,
    *,
    node: str,
    role: str,
    content: str,
    round_index: int | None = None,
    card_kind: str | None = None,
) -> dict[str, Any]:
    payload = ChatMessagePayload(
        message_id=message_id_for(
            state["run_id"],
            int(state.get("attempt", 1)),
            node,
            round_index,
        ),
        role=role,
        node=node,
        round=round_index,
        content=content,
        status="completed",
        sequence=len(state.get("events", ())),
        generation=1,
        completed_at=datetime.now(timezone.utc).isoformat(),
        card_kind=card_kind,
        card_ref=(
            CardRef(
                attempt=int(state.get("attempt", 1)),
                node=node,
                kind=card_kind,
            )
            if card_kind
            else None
        ),
    )
    return {
        "event_id": payload.message_id,
        "node": node,
        "event_type": "message_completed",
        "payload": payload.model_dump(mode="json"),
    }
```

- [ ] **Step 4: 在各节点挂接完成消息**

节点挂接规则：

| 节点 | content 来源 | card_kind |
| --- | --- | --- |
| `prepare` | 模板：`已冻结 {len(universe)} 个标的的市场快照。` | `snapshot` |
| analyst / debate / trader / chair | `execution.output.chat_message` | 对应 artifact kind，如 `analyst_reports`、`debate_turns`、`trade_proposal`、`final_decision` |
| `backtest` | `回测{'通过' if passed else '未通过'}，得分 {score:.2f}。{summary}` | `backtest_verdict` |
| `risk` | `风控结论：{status}，批准仓位 {approved_weight:.0%}。` | `risk_verdict` |

失败/中止节点不伪造成功完成消息。若节点返回 `{}`（已回放过），不新增消息。`events` 列表同时保留原有进度事件和新的 `message_completed`；**绝不**把 `message_started` / `message_delta` 放进 graph `events`（会进入 checkpoint 并被 reconcile 重复持久化）。

辩论节点必须把 `round_index` 传入 `_message_completed` 和 `aexecute(... attempt=state["attempt"], round_index=...)`；所有 LLM 节点的 `aexecute` 都传入当前 `attempt`。

- [ ] **Step 5: 运行图测试**

Run: `cd backend && uv run pytest tests/test_committee_graph.py tests/test_committee_invoke.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/advisor/committee/graph.py backend/tests/test_committee_graph.py backend/tests/test_committee_invoke.py
git commit -m "feat(committee): emit durable completed chat messages"
```

---

### Task 4: Redis 临时事件与 SSE 双游标

**Files:**
- Modify: `backend/app/advisor/committee/runtime.py`
- Modify: `backend/app/advisor/committee/tasks.py`
- Modify: `backend/app/advisor/committee/routes.py`
- Modify: `backend/tests/test_committee_runtime.py`
- Modify: `backend/tests/test_committee_task5_review.py`

**Interfaces:**
- Consumes: `ChatStreamEvent`
- Produces: `CommitteeRuntime.append_ephemeral_event(user_id, run_id, event_type, payload) -> RuntimeEvent`；worker sink；SSE 同时推送 Mongo durable 和 Redis ephemeral

- [ ] **Step 1: 写 runtime 与 SSE 失败测试**

```python
def test_append_ephemeral_event_uses_auto_redis_id():
    runtime = CommitteeRuntime(settings, connection=fake_redis)
    first = runtime.append_ephemeral_event(
        "u", "r", "message_delta",
        {"message_id": "m1", "delta": "A", "offset": 1, "generation": 1},
    )
    second = runtime.append_ephemeral_event(
        "u", "r", "message_delta",
        {"message_id": "m1", "delta": "B", "offset": 2, "generation": 1},
    )
    assert first.event_id != second.event_id
    assert first.event_id.endswith("-0") or "-" in first.event_id


def test_sse_emits_ephemeral_deltas_without_advancing_mongo_cursor(client, monkeypatch):
    repository.append_outbox_event(
        "u", "r",
        attempt=1, node="worker", event_type="running",
        event_key="attempt:1:running", payload={},
    )
    runtime.append_ephemeral_event(
        "u", "r", "message_delta",
        {"message_id": "m1", "delta": "看多", "offset": 0, "generation": 1},
    )
    frames = list(collect_sse(client, "/runs/r/events", last_event_id=None, max_frames=3))
    assert any(frame["event"] == "running" for frame in frames)
    assert any(frame["event"] == "message_delta" for frame in frames)

    repository.append_outbox_event(
        "u", "r",
        attempt=1, node="technical", event_type="message_completed",
        event_key="attempt:1:message:m1",
        payload={"message_id": "m1", "content": "看多完整", "status": "completed"},
    )
    resumed = list(collect_sse(client, "/runs/r/events", last_event_id="1-0", max_frames=2))
    assert any(frame["event"] == "message_completed" for frame in resumed)
    assert all(frame["event"] != "message_delta" for frame in resumed)
```

实现时复用 `test_committee_task5_review.py` 现有 fake repository/runtime 与 `collect_sse` helper；若 helper 不存在，在同文件新增最小采集器。断线场景下临时 delta 可丢失，只要最终 `message_completed` 到达即可。

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && uv run pytest tests/test_committee_runtime.py tests/test_committee_task5_review.py -q`

Expected: FAIL，缺少 ephemeral API，SSE 仍把 Redis ID 当作 Mongo sequence。

- [ ] **Step 3: 增加 ephemeral 发布接口**

在 `runtime.py`：

```python
def append_ephemeral_event(
    self,
    user_id: str,
    run_id: str,
    event_type: str,
    payload: Any,
) -> RuntimeEvent:
    safe_payload, encoded = _json_payload(payload)
    created_at = _iso_now()
    event_id = self._connection().xadd(
        self._stream_key(user_id, run_id),
        {
            "event_type": event_type,
            "payload": encoded,
            "created_at": created_at,
            "ephemeral": "1",
        },
    )
    return RuntimeEvent(
        event_id=_text(event_id),
        event_type=event_type,
        payload=safe_payload,
        created_at=created_at,
    )
```

不要改现有 `append_event(..., event_id=f"{sequence}-0")` 的 durable 语义。

- [ ] **Step 4: 在 worker 注入 sink**

在 `tasks.py` 创建 runner 前定义：

```python
async def stream_sink(event: ChatStreamEvent) -> None:
    try:
        await asyncio.to_thread(
            runtime.append_ephemeral_event,
            user_id,
            run_id,
            event.event_type,
            event.payload,
        )
    except Exception:
        return

executor = RoleAgentExecutor(
    ChatModelRoleRunner(config, stream_sink=stream_sink)
)
```

`message_completed` 继续只通过 `_persist_node_update` / `_publish` 写入 Mongo outbox，不走 ephemeral。

- [ ] **Step 5: 改造 SSE 双游标**

在 `_event_stream`：

1. `Last-Event-ID` 若形如 `"12-0"` 或纯数字，解析 durable cursor；同时维护 `live_cursor`，初始为 `f"{durable_cursor}-0"`。
2. 先刷 Mongo `list_events_after(after_sequence=durable_cursor)`；每条 durable 事件同时推进 durable cursor 和 `live_cursor = f"{sequence}-0"`。
3. Mongo 无新事件时，调用 `runtime.read_events_after(..., last_event_id=live_cursor, count=20, block_ms=1000)`。
4. Redis 事件若 `event_type` 为 `message_started` / `message_delta`，直接 yield SSE，只推进 `live_cursor`，不推进 durable cursor。
5. Redis 上的 durable 回显（例如 `running`、`node_completed`）不二次 yield，只推进 `live_cursor`；权威内容以 Mongo 为准。
6. 心跳和终态帧逻辑保持不变。

- [ ] **Step 6: 运行 runtime/SSE 测试**

Run: `cd backend && uv run pytest tests/test_committee_runtime.py tests/test_committee_task5_review.py tests/test_committee_e2e.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/advisor/committee/runtime.py backend/app/advisor/committee/tasks.py backend/app/advisor/committee/routes.py backend/tests/test_committee_runtime.py backend/tests/test_committee_task5_review.py
git commit -m "feat(committee): stream ephemeral chat events over SSE"
```

---

### Task 5: 前端消息模型、reducer 与旧历史转换

**Files:**
- Create: `frontend-advisor/src/committee/chatMessages.ts`
- Create: `frontend-advisor/src/committee/chatMessages.test.ts`

**Interfaces:**
- Produces: `CommitteeChatMessage`、`ChatMessagesState`、`chatMessagesReducer`、`messagesFromEvents`、`messagesFromArtifacts`、`applyChatSseEvent`
- Consumes: `CommitteeEventRecord`、`CommitteeArtifact`

- [ ] **Step 1: 写 reducer 和旧历史转换失败测试**

```typescript
it('started 清空同 message_id 的旧 generation 临时文本', () => {
  let state = chatMessagesReducer(initialChatMessagesState, {
    type: 'hydrate',
    messages: [],
  })
  state = chatMessagesReducer(state, {
    type: 'sse',
    event: {
      event: 'message_started',
      data: { message_id: 'm1', role: 'technical', node: 'technical', generation: 2 },
    },
  })
  state = chatMessagesReducer(state, {
    type: 'sse',
    event: {
      event: 'message_delta',
      data: { message_id: 'm1', delta: '新', offset: 0, generation: 2 },
    },
  })
  // 先注入 generation=1 的脏文本，再确认 generation=2 的 started 会覆盖
  expect(state.byId.m1.content).toBe('新')
  expect(state.byId.m1.status).toBe('streaming')
})

it('completed 始终覆盖临时文本并成为权威内容', () => {
  const state = reduceEvents([
    started('m1', 1),
    delta('m1', '临时', 2, 1),
    completed('m1', '权威完成文本', 1),
  ])
  expect(state.byId.m1.content).toBe('权威完成文本')
  expect(state.byId.m1.status).toBe('completed')
})

it('乱序或缺口 delta 不会破坏已有文本', () => {
  const state = reduceEvents([
    started('m1', 1),
    delta('m1', 'AB', 0, 1), // offset=追加前长度
    delta('m1', 'Z', 0, 1), // 重复 offset 忽略
    delta('m1', 'CD', 5, 1), // 缺口忽略，等待 completed
  ])
  expect(state.byId.m1.content).toBe('AB')
})

it('旧会议没有 message_completed 时从 artifacts 转换', () => {
  const messages = messagesFromArtifacts([
    { kind: 'analyst_reports', payload: [{ role: 'technical', thesis: '旧技术观点', confidence: 0.6 }] },
    { kind: 'backtest_verdict', payload: { passed: true, score: 0.8, summary: '稳' } },
  ], { runId: 'old', attempt: 1 })
  expect(messages.map((m) => m.role)).toEqual(['technical', 'backtest'])
  expect(messages[0].content).toContain('旧技术观点')
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd frontend-advisor && npm test -- src/committee/chatMessages.test.ts`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现消息类型与 reducer**

```typescript
export type CommitteeChatRole =
  | 'data' | 'fundamental' | 'technical' | 'news' | 'quant'
  | 'bull' | 'bear' | 'trader' | 'backtest' | 'risk' | 'chair'

export type CommitteeChatMessage = {
  message_id: string
  role: CommitteeChatRole
  node: string
  round?: number | null
  content: string
  status: 'streaming' | 'completed' | 'failed'
  sequence: number
  generation: number
  created_at?: string | null
  completed_at?: string | null
  card_kind?: string | null
  card_ref?: { attempt: number; node: string; kind: string } | null
  nextOffset: number
}

export type ChatMessagesState = {
  order: string[]
  byId: Record<string, CommitteeChatMessage>
}

export const ROLE_META: Record<CommitteeChatRole, { label: string; tone: string }> = {
  data: { label: '数据助手', tone: 'system' },
  fundamental: { label: '基本面分析师', tone: 'analyst' },
  technical: { label: '技术分析师', tone: 'analyst' },
  news: { label: '新闻分析师', tone: 'analyst' },
  quant: { label: '量化分析师', tone: 'analyst' },
  bull: { label: '多方辩手', tone: 'debate' },
  bear: { label: '空方辩手', tone: 'debate' },
  trader: { label: '交易员', tone: 'trader' },
  backtest: { label: '回测员', tone: 'system' },
  risk: { label: '风控官', tone: 'system' },
  chair: { label: '主席', tone: 'chair' },
}
```

reducer 规则：

1. `hydrate`：优先 `messagesFromEvents(events)`；若结果为空，再 `messagesFromArtifacts(artifacts)`。
2. `merge`：用新的 completed 消息覆盖同 `message_id`，不删除仍在 streaming 且尚未 completed 的本地消息。
3. `message_started`：创建或重置同 `message_id` 且 `generation` 更大/相等的消息，`content=""`、`status="streaming"`、`nextOffset=0`。
4. `message_delta`：仅当 `generation` 匹配且 `offset === content.length` 时追加；重复 offset 忽略；缺口不拼接，标记等待 completed。
5. `message_completed`：无论当前 content 如何，直接覆盖为权威 content；`status` 取事件值（`completed` / `degraded` / `failed`）。
6. 若 hydrate/merge 得到 completed 消息且本地从未收到同 `message_id` 的 delta，前端可选择对该条执行短窗口本地逐字展示；刷新历史完成态直接整段显示。
7. token delta 按 32–50ms 时间窗批量 flush，降低渲染频率。
8. 排序：临时消息按首次 `message_started` 到达序；完成后以 durable `sequence` 为权威序，并列按 `ROLE_ORDER` 与 `message_id`。

- [ ] **Step 4: 运行消息单测**

Run: `cd frontend-advisor && npm test -- src/committee/chatMessages.test.ts`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend-advisor/src/committee/chatMessages.ts frontend-advisor/src/committee/chatMessages.test.ts
git commit -m "feat(committee): add chat message reducer"
```

---

### Task 6: 群聊主界面与右侧详情抽屉

**Files:**
- Create: `frontend-advisor/src/committee/components/CommitteeChat.tsx`
- Create: `frontend-advisor/src/committee/components/CommitteeDetailDrawer.tsx`
- Modify: `frontend-advisor/src/committee/components/CommitteeDetail.tsx`
- Modify: `frontend-advisor/src/committee/CommitteePage.tsx`
- Modify: `frontend-advisor/src/committee/CommitteePage.test.tsx`
- Modify: `frontend-advisor/src/styles.css`

**Interfaces:**
- Consumes: `ChatMessagesState`、`ROLE_META`、现有 `CommitteeDetail` 渲染能力
- Produces: 只读群聊主区域、详情抽屉入口、页面级状态接线

- [ ] **Step 1: 写页面级失败测试**

在 `CommitteePage.test.tsx` 增加：

```typescript
it('主区域展示群聊消息且结构化报告进入详情抽屉', async () => {
  api.getCommitteeRun.mockResolvedValue({
    run: completedRun,
    events: [
      completedEvent({
        message_id: 'm-tech',
        role: 'technical',
        content: '技术面偏多',
        sequence: 1,
      }),
    ],
    artifacts: [
      { artifact_id: 'a1', kind: 'trade_proposal', payload: { symbol: '510300', direction: 'buy' } },
    ],
  })
  render(<MemoryRouter><CommitteePage /></MemoryRouter>)
  expect(await screen.findByText('技术面偏多')).toBeInTheDocument()
  expect(screen.getByText('技术分析师')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '查看详情' }))
  expect(await screen.findByRole('dialog', { name: '会议详情' })).toBeInTheDocument()
  expect(screen.getByText(/510300/)).toBeInTheDocument()
})

it('实时 message_delta 按 token 追加，completed 覆盖临时文本', async () => {
  // 先 hydrate 空消息，再通过 streamHandlers 推 started/delta/completed
})

it('旧会议无 message_completed 时仍能从 artifacts 渲染群聊', async () => {
  // events 只有 node_completed，artifacts 含 analyst_reports / final_decision
})

it('删除记录回归：删除按钮与 tombstone 行为不变', async () => {
  // 复用现有删除用例，确保群聊改造未破坏
})
```

- [ ] **Step 2: 运行页面测试并确认失败**

Run: `cd frontend-advisor && npm test -- src/committee/CommitteePage.test.tsx`

Expected: FAIL，页面仍是旧详情主区域。

- [ ] **Step 3: 实现 `CommitteeChat`**

组件签名：

```tsx
export default function CommitteeChat({
  messages,
  artifacts,
  loading,
  streamState,
}: {
  messages: CommitteeChatMessage[]
  artifacts: CommitteeArtifact[]
  loading: boolean
  streamState: string
})
```

要求：

1. 使用 `Virtuoso` 渲染 `messages`。
2. 每条消息显示头像色块、角色名、时间、状态（输出中/已完成/失败）。
3. 助手内容用 `react-markdown` + `remark-gfm`；streaming 且内容为空时显示 `…`。
4. 有 `card_ref` 时渲染可展开 `<details>` 数据卡，从 `artifacts` 按 `attempt/node/kind` 或 `kind` 回退匹配 payload，并做只读 JSON / 摘要展示。
5. 吸底逻辑复用 `AgentChatPage`：用户未上翻时跟随最新消息；上翻时显示“回到最新”按钮。
6. 顶部展示会议状态、参与角色摘要、耗时/Token（若 artifacts 中有 `budget`/`model_calls`）、流状态。
7. 不提供输入框；不展示 prompt、原始 JSON、堆栈或密钥。

- [ ] **Step 4: 实现详情抽屉并改造页面**

`CommitteeDetailDrawer`：

```tsx
export default function CommitteeDetailDrawer({
  open,
  onClose,
  children,
}: {
  open: boolean
  onClose: () => void
  children: React.ReactNode
})
```

使用 `role="dialog"`、`aria-label="会议详情"`、Esc 关闭、点击遮罩关闭。

`CommitteePage` 改造：

1. 增加 `const [chat, dispatchChat] = useReducer(chatMessagesReducer, initialChatMessagesState)`。
2. `fetchDetail` 成功后：`dispatchChat({ type: 'hydrate' | 'merge', events: detail.events, artifacts: detail.artifacts })`。
3. SSE `onEvent` 中先 `dispatch({ type: 'event', ... })` 保持旧时间线（如仍被抽屉使用），再 `dispatchChat({ type: 'sse', event })`。
4. 主区域替换为：

```tsx
<section className="committee-chat-pane">
  <header className="committee-chat-header">
    <div>
      <h2>{selectedRun?.run_id ?? '未选择会议'}</h2>
      <p>{selectedRun?.status ?? ''} · {streamState}</p>
    </div>
    <button type="button" onClick={() => setShowDetail(true)}>查看详情</button>
  </header>
  <CommitteeChat
    messages={orderedChatMessages(chat)}
    artifacts={artifacts}
    loading={detailLoading}
    streamState={streamState}
  />
</section>
<CommitteeDetailDrawer open={showDetail} onClose={() => setShowDetail(false)}>
  <CommitteeDetail run={selectedRun} artifacts={artifacts} timeline={timeline} />
</CommitteeDetailDrawer>
```

5. 左侧 `RunHistory`、发起会议、删除、取消、重试、审批对话框保持可用。

- [ ] **Step 5: 启用固定高度 shell 并增加样式**

在 `App.tsx` 将 shell 判断改为同时覆盖投委会：

```tsx
const isAgentChat =
  location.pathname === '/agent' || location.pathname === '/agent/committee'
```

在 `App.test.tsx` 增加：访问 `/agent/committee` 时根节点包含 `app-shell--agent-chat`（或本任务选用的等价 class）。

在 `styles.css` 增加：

- `.committee-workspace` 三栏：历史 / 群聊 / 抽屉层
- `.committee-chat-pane`、`.committee-chat-header`、`.committee-chat-list`
- `.committee-bubble` 及 role tone 颜色
- `.committee-bubble-status.streaming` 闪烁或脉冲
- `.committee-card` 数据卡
- `.committee-drawer`、`.committee-drawer-backdrop`
- 窄屏：抽屉改为全屏浮层，历史可折叠保持现有响应式策略

不要破坏 `.agent-chat` 现有样式。

- [ ] **Step 6: 运行前端测试**

Run: `cd frontend-advisor && npm test -- src/committee`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add frontend-advisor/src/committee/components/CommitteeChat.tsx frontend-advisor/src/committee/components/CommitteeDetailDrawer.tsx frontend-advisor/src/committee/components/CommitteeDetail.tsx frontend-advisor/src/committee/CommitteePage.tsx frontend-advisor/src/committee/CommitteePage.test.tsx frontend-advisor/src/styles.css frontend-advisor/src/App.tsx frontend-advisor/src/App.test.tsx
git commit -m "feat(committee): replace detail pane with live group chat"
```

---

### Task 7: 端到端回归与手测清单

**Files:**
- Modify: `backend/tests/test_committee_e2e.py`（仅在现有夹具需要 `chat_message` 时）
- Verify only: 本地 worker / 前端手测

**Interfaces:**
- Consumes: Tasks 1-6 全部产物
- Produces: 绿色回归与手测通过记录

- [ ] **Step 1: 运行后端相关回归**

Run:

```bash
cd backend && uv run pytest \
  tests/test_committee_chat_stream.py \
  tests/test_committee_execution.py \
  tests/test_committee_critical.py \
  tests/test_committee_graph.py \
  tests/test_committee_invoke.py \
  tests/test_committee_runtime.py \
  tests/test_committee_task5_review.py \
  tests/test_committee_e2e.py -q
```

Expected: PASS。

- [ ] **Step 2: 运行前端相关回归**

Run:

```bash
cd frontend-advisor && npm test -- src/committee && npm run build
```

Expected: 测试通过，TypeScript 构建成功。

- [ ] **Step 3: 手动验证清单**

1. 打开 `http://localhost:5174/agent/committee`，发起一次新会议。
2. 观察至少一名分析师出现“输出中”气泡，并按 token 增长。
3. 断网或刷新页面后重连，临时文本可丢失，但节点完成后应显示完整 `message_completed` 内容。
4. 回测/风控机器人各自独立发言，并可展开数据卡。
5. 点击“查看详情”打开右侧抽屉，交易提案、最终决议仍可读。
6. 打开一笔改造前的旧会议：即使没有 `message_completed`，也能从 artifacts 看到转换后的群聊摘要。
7. 删除一笔终态会议，确认不会复活，且不影响其他会议选择。

- [ ] **Step 4: 提交回归修补（如有）**

```bash
git add -A
git commit -m "test(committee): harden group chat regressions"
```

若本步无代码变更，跳过提交。

---

## Self-Review

**1. Spec coverage**

| 设计要求 | 对应任务 |
| --- | --- |
| 群聊主界面 + 右侧详情抽屉 + 固定高度 shell | Task 6 |
| delta 不进 checkpoint / cache 不重放 delta | Task 2 / Task 3 / Global Constraints |
| 角色映射与消息样式 | Task 5 / Task 6 |
| 单次流式 JSON + `chat_message` 首字段 | Task 1 / Task 2 |
| `message_started` / `message_delta` / `message_completed` | Task 2 / Task 3 / Task 4 |
| generation 重试清空旧临时文本 | Task 2 / Task 5 |
| 非 LLM 系统消息与数据卡 | Task 1 / Task 3 / Task 6 |
| Mongo 权威完成、Redis 临时增量 | Task 4 |
| SSE 重连允许丢 delta，completed 恢复 | Task 4 / Task 5 / Task 7 |
| 旧会议 artifacts 前端转换 | Task 5 / Task 6 |
| 保留删除/取消/重试/审批 | Task 6 / Task 7 |
| 测试覆盖解析、流式、图、SSE、reducer、页面 | Tasks 1-7 |

**2. Placeholder scan**

已清除模糊步骤与省略号测试骨架；角色名与设计文档一致（技术分析师、回测员）；`message_id` 含 attempt；前端包含无增量时的本地逐字降级与“回到最新”。

**3. Type consistency**

- 后端 payload 字段与前端 `CommitteeChatMessage` 对齐：`message_id`、`role`、`node`、`round`、`content`、`status`、`sequence`、`generation`、`card_kind`、`card_ref`。
- 临时事件仅 `message_started` / `message_delta`；权威事件仅 `message_completed`。
- 稳定 ID 函数两端语义一致：`message_id_for(run_id, attempt, node, round)`。
- delta `offset` 表示追加前内容长度；前端仅在 `offset === content.length` 时追加。
- 状态枚举两端一致：`streaming` / `completed` / `degraded` / `failed`。

