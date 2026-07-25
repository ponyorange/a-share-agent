from __future__ import annotations

import threading

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.advisor.agent import graph as agent_graph
from app.advisor.agent.progress import ProgressValidationError, emit_progress


DELEGATE_SAFE_CONTENT = "数据子 Agent 已返回结构化结果"


class FakeAgent:
    def __init__(self, stream_events, invoke_messages=None):
        self._stream_events = stream_events
        self._invoke_messages = invoke_messages or []

    def stream(self, *_args, **_kwargs):
        yield from self._stream_events

    def invoke(self, *_args, **_kwargs):
        return {"messages": self._invoke_messages}


def _delegate_tool_message() -> ToolMessage:
    return ToolMessage(
        content=(
            '{"data":[{"api_key":"SECRET_DATA_ROW"}],'
            '"sources":[{"params_summary":{"token":"SECRET_PARAMS"}}],'
            '"warnings":[{"message":"SECRET_WARNING"}],'
            '"failures":[{"message":"SECRET_FAILURE"}]}'
        ),
        name="delegate_data_task",
        tool_call_id="call_delegate",
    )


def _tool_message(name: str, content: str) -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=f"call_{name}")


def _install_chat_core(monkeypatch, fake_agent: FakeAgent, captured_messages: list[dict]):
    monkeypatch.setattr(agent_graph, "ensure_session", lambda _user_id, session_id=None: session_id or "s")
    monkeypatch.setattr(agent_graph, "session_exists", lambda _user_id, _session_id: True)
    monkeypatch.setattr(
        agent_graph,
        "build_context_history",
        lambda *_args, **_kwargs: [{"role": "user", "content": "query"}],
    )
    monkeypatch.setattr(agent_graph, "build_chat_model", lambda _user_id: object())
    monkeypatch.setattr(agent_graph, "build_tools", lambda _user_id: [])
    monkeypatch.setattr(
        agent_graph,
        "create_react_agent",
        lambda *_args, **_kwargs: fake_agent,
    )

    def fake_append_message(_user_id, _sid, *, role, content, tool_trace=None):
        captured_messages.append(
            {"role": role, "content": content, "tool_trace": tool_trace or []}
        )

    monkeypatch.setattr(agent_graph, "append_message", fake_append_message)


def _assert_no_delegate_secrets(events, captured_messages):
    serialized_events = str(events)
    serialized_persisted = str(captured_messages)

    assert "SECRET_DATA_ROW" not in serialized_events
    assert "SECRET_PARAMS" not in serialized_events
    assert "SECRET_WARNING" not in serialized_events
    assert "SECRET_FAILURE" not in serialized_events
    assert "SECRET_DATA_ROW" not in serialized_persisted
    assert "SECRET_PARAMS" not in serialized_persisted
    assert "SECRET_WARNING" not in serialized_persisted
    assert "SECRET_FAILURE" not in serialized_persisted


def _assert_delegate_result_is_sanitized(events, captured_messages):
    _assert_no_delegate_secrets(events, captured_messages)

    tool_events = [event for event in events if event["event"] == "tool"]
    for event in tool_events:
        assert event["data"] == {
            "tool": "delegate_data_task",
            "content": DELEGATE_SAFE_CONTENT,
        }

    done = next(event for event in events if event["event"] == "done")
    assert done["data"]["tool_trace"] == [
        {"tool": "delegate_data_task", "content": DELEGATE_SAFE_CONTENT}
    ]
    persisted_assistant = [
        message for message in captured_messages if message["role"] == "assistant"
    ][-1]
    assert persisted_assistant["tool_trace"] == [
        {"tool": "delegate_data_task", "content": DELEGATE_SAFE_CONTENT}
    ]


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
    assert first["data"]["message"] == "正在启动数据子 Agent"
    release.set()
    assert next(events)["event"] == "done"


def test_full_progress_queue_can_drop_progress_but_keeps_done(monkeypatch):
    monkeypatch.setattr(agent_graph, "_EVENT_QUEUE_SIZE", 1, raising=False)

    def fake_sync(*args, progress_trace, **kwargs):
        for _ in range(20):
            emit_progress(step="fetch", status="completed", rows=1)
        yield {"event": "done", "data": {"session_id": "s", "reply": "完成"}}

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")

    seen: list[str] = []
    for event in events:
        seen.append(event["event"])
        if event["event"] == "done":
            break

    assert "done" in seen


def test_required_events_are_reliably_delivered_under_progress_pressure(monkeypatch):
    monkeypatch.setattr(agent_graph, "_EVENT_QUEUE_SIZE", 1, raising=False)

    def fake_sync(*args, progress_trace, **kwargs):
        for row in range(50):
            emit_progress(step="fetch", status="completed", rows=row)
        yield {"event": "tool", "data": {"tool": "safe_tool", "content": "ok"}}
        yield {"event": "token", "data": {"delta": "hello"}}
        yield {"event": "error", "data": {"detail": "stable_error", "session_id": "s"}}

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")

    seen: list[str] = []
    for event in events:
        seen.append(event["event"])
        if event["event"] == "error":
            break

    assert "tool" in seen
    assert "token" in seen
    assert seen[-1] == "error"


def test_closing_stream_stops_consuming_sync_core_after_next_yield(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    post_close_yields: list[int] = []

    def fake_sync(*args, progress_trace, **kwargs):
        try:
            yield {"event": "token", "data": {"delta": "first"}}
            release.wait(timeout=2)
            for index in range(5):
                post_close_yields.append(index)
                yield {"event": "token", "data": {"delta": str(index)}}
        finally:
            finished.set()

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")

    assert next(events)["event"] == "token"
    events.close()
    release.set()

    assert finished.wait(timeout=2)
    assert post_close_yields == [0]


def test_closing_stream_stops_late_progress_queue_and_trace_writes(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    captured_trace: list[dict[str, str]] = []

    def fake_sync(*args, progress_trace, **kwargs):
        captured_trace.append(progress_trace)
        try:
            yield {"event": "token", "data": {"delta": "first"}}
            release.wait(timeout=2)
            emit_progress(step="fetch", status="completed", rows=1)
        finally:
            finished.set()

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")

    assert next(events)["event"] == "token"
    events.close()
    release.set()

    assert finished.wait(timeout=2)
    assert captured_trace == [[]]


def test_progress_trace_keeps_only_last_twenty_non_duplicate_entries(monkeypatch):
    def fake_sync(*args, progress_trace, **kwargs):
        for index in range(25):
            emit_progress(
                step="fetch",
                status="completed",
                source=f"source-{index}",
                rows=index,
            )
        yield {
            "event": "done",
            "data": {
                "session_id": "s",
                "reply": "完成",
                "progress_trace": list(progress_trace),
            },
        }

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")

    done = None
    for event in events:
        if event["event"] == "done":
            done = event
            break

    assert done is not None
    trace = done["data"]["progress_trace"]
    assert len(trace) == 20
    assert trace[0]["content"].startswith("source-5")
    assert trace[-1]["content"].startswith("source-24")


def test_progress_validation_error_is_sanitized(monkeypatch):
    def fake_sync(*args, progress_trace, **kwargs):
        raise ProgressValidationError("invalid progress step: 'secret_chain_of_thought'")
        yield

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")

    error = next(events)

    assert error["event"] == "error"
    assert error["data"]["detail"] == "progress_validation_error"
    assert "secret_chain_of_thought" not in str(error["data"])
    assert "ProgressValidationError" not in str(error["data"])


def test_delegate_tool_message_from_messages_stream_is_sanitized(monkeypatch):
    captured_messages: list[dict] = []
    fake_agent = FakeAgent(
        [
            ("messages", (_delegate_tool_message(), {})),
            ("updates", {"agent": {"messages": [AIMessage(content="完成")]}}),
        ]
    )
    _install_chat_core(monkeypatch, fake_agent, captured_messages)

    events = list(
        agent_graph._iter_agent_chat_events_sync(
            "u",
            "query",
            session_id="s",
            progress_trace=[],
        )
    )

    _assert_delegate_result_is_sanitized(events, captured_messages)


def test_delegate_tool_message_from_updates_stream_is_sanitized(monkeypatch):
    captured_messages: list[dict] = []
    fake_agent = FakeAgent(
        [
            ("updates", {"tools": {"messages": [_delegate_tool_message()]}}),
            ("updates", {"agent": {"messages": [AIMessage(content="完成")]}}),
        ]
    )
    _install_chat_core(monkeypatch, fake_agent, captured_messages)

    events = list(
        agent_graph._iter_agent_chat_events_sync(
            "u",
            "query",
            session_id="s",
            progress_trace=[],
        )
    )

    _assert_delegate_result_is_sanitized(events, captured_messages)


def test_fallback_invoke_reversed_scan_preserves_tool_trace_boundary(monkeypatch):
    captured_messages: list[dict] = []
    fake_agent = FakeAgent(
        [],
        invoke_messages=[
            _tool_message("early_tool", "EARLY_TOOL_SHOULD_NOT_TRACE"),
            AIMessage(content="上一轮回答"),
            _tool_message("before_final_tool", "BEFORE_FINAL_SHOULD_NOT_TRACE"),
            AIMessage(content="完成"),
            _delegate_tool_message(),
            _tool_message("tail_tool", "TAIL_TOOL_VISIBLE"),
        ],
    )
    _install_chat_core(monkeypatch, fake_agent, captured_messages)

    events = list(
        agent_graph._iter_agent_chat_events_sync(
            "u",
            "query",
            session_id="s",
            progress_trace=[],
        )
    )

    _assert_no_delegate_secrets(events, captured_messages)

    done = next(event for event in events if event["event"] == "done")
    expected_trace = [
        {"tool": "tail_tool", "content": "TAIL_TOOL_VISIBLE"},
        {"tool": "delegate_data_task", "content": DELEGATE_SAFE_CONTENT},
    ]
    assert done["data"]["reply"] == f"完成\n\n{agent_graph.DISCLAIMER}"
    assert done["data"]["tool_trace"] == expected_trace
    assert "EARLY_TOOL_SHOULD_NOT_TRACE" not in str(done["data"]["tool_trace"])
    assert "BEFORE_FINAL_SHOULD_NOT_TRACE" not in str(done["data"]["tool_trace"])

    persisted_assistant = [
        message for message in captured_messages if message["role"] == "assistant"
    ][-1]
    assert persisted_assistant["tool_trace"] == expected_trace


def test_deleted_session_is_not_revived_by_late_assistant_write(monkeypatch):
    captured_messages: list[dict] = []
    session_alive = True

    class DeletingAgent(FakeAgent):
        def stream(self, *_args, **_kwargs):
            nonlocal session_alive
            session_alive = False
            yield (
                "updates",
                {"agent": {"messages": [AIMessage(content="不应持久化")]}},
            )

    _install_chat_core(monkeypatch, DeletingAgent([]), captured_messages)
    monkeypatch.setattr(
        agent_graph,
        "session_exists",
        lambda user_id, session_id: session_alive
        and user_id == "u"
        and session_id == "s",
        raising=False,
    )

    events = list(
        agent_graph._iter_agent_chat_events_sync(
            "u",
            "query",
            session_id="s",
            progress_trace=[],
        )
    )

    assert [message["role"] for message in captured_messages] == ["user"]
    assert events[-1] == {
        "event": "error",
        "data": {"detail": "session_not_found", "session_id": "s"},
    }


def test_sync_core_unknown_exception_uses_fixed_error_detail(monkeypatch):
    monkeypatch.setattr(agent_graph, "ensure_session", lambda _user_id, session_id=None: session_id or "s")
    monkeypatch.setattr(
        agent_graph,
        "append_message",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(agent_graph, "build_context_history", lambda *_args, **_kwargs: [])

    def raise_secret(_user_id):
        raise RuntimeError("SECRET_PROVIDER_TOKEN")

    monkeypatch.setattr(agent_graph, "build_chat_model", raise_secret)

    events = list(
        agent_graph._iter_agent_chat_events_sync(
            "u",
            "query",
            session_id="s",
            progress_trace=[],
        )
    )

    error = events[-1]
    assert error["event"] == "error"
    assert error["data"]["detail"] == "Agent 执行失败"
    assert "SECRET_PROVIDER_TOKEN" not in str(error["data"])
    assert "RuntimeError" not in str(error["data"])


def test_iter_agent_chat_events_producer_unknown_exception_uses_fixed_detail(monkeypatch):
    def fake_sync(*args, progress_trace, **kwargs):
        raise RuntimeError("SECRET_QUEUE_TOKEN")
        yield

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)

    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")
    error = next(events)

    assert error["event"] == "error"
    assert error["data"]["detail"] == "Agent 执行失败"
    assert error["data"]["session_id"] == "s"
    assert "SECRET_QUEUE_TOKEN" not in str(error["data"])
    assert "RuntimeError" not in str(error["data"])


def test_stream_end_sentinel_finishes_generator_normally(monkeypatch):
    def fake_sync(*args, progress_trace, **kwargs):
        yield {"event": "done", "data": {"session_id": "s", "reply": "完成"}}

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")

    assert next(events)["event"] == "done"
    with pytest.raises(StopIteration):
        next(events)


def test_sync_core_exception_is_returned_as_error(monkeypatch):
    def fake_sync(*args, progress_trace, **kwargs):
        emit_progress(step="delegate", status="started")
        raise RuntimeError("SECRET_THREAD_TOKEN")
        yield

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    events = agent_graph.iter_agent_chat_events("u", "query", session_id="s")

    assert next(events)["event"] == "subagent_progress"
    error = next(events)
    assert error["event"] == "error"
    assert error["data"]["detail"] == "Agent 执行失败"
    assert error["data"]["session_id"] == "s"
    assert "SECRET_THREAD_TOKEN" not in str(error["data"])
    assert "RuntimeError" not in str(error["data"])


def test_run_agent_chat_ignores_progress_and_returns_done_payload(monkeypatch):
    def fake_sync(*args, progress_trace, **kwargs):
        emit_progress(step="delegate", status="started")
        yield {
            "event": "done",
            "data": {
                "session_id": "s",
                "reply": "完成",
                "tool_trace": [{"tool": "data_agent.delegate", "content": "正在启动数据子 Agent"}],
                "disclaimer": agent_graph.DISCLAIMER,
            },
        }

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)

    result = agent_graph.run_agent_chat("u", "query", session_id="s")

    assert result == {
        "session_id": "s",
        "reply": "完成",
        "tool_trace": [{"tool": "data_agent.delegate", "content": "正在启动数据子 Agent"}],
        "disclaimer": agent_graph.DISCLAIMER,
    }


def test_concurrent_agent_chats_do_not_share_progress(monkeypatch):
    barrier = threading.Barrier(2)

    def fake_sync(user_id, *args, progress_trace, **kwargs):
        barrier.wait(timeout=2)
        emit_progress(
            step="fetch",
            status="completed",
            source=user_id,
            rows=1,
        )
        yield {"event": "done", "data": {"session_id": user_id, "reply": "完成"}}

    monkeypatch.setattr(agent_graph, "_iter_agent_chat_events_sync", fake_sync)
    results: dict[str, dict[str, object]] = {}

    def collect(user_id: str) -> None:
        events = agent_graph.iter_agent_chat_events(user_id, "query", session_id=user_id)
        results[user_id] = next(events)
        assert next(events)["event"] == "done"

    left = threading.Thread(target=collect, args=("left",))
    right = threading.Thread(target=collect, args=("right",))
    left.start()
    right.start()
    left.join(timeout=3)
    right.join(timeout=3)

    assert not left.is_alive()
    assert not right.is_alive()
    assert results["left"]["data"]["source"] == "left"
    assert results["right"]["data"]["source"] == "right"
