from __future__ import annotations

from langchain_core.messages import AIMessage

from app.advisor.agent import graph as graph_mod


class _FakeAgent:
    def stream(self, *_a, **_k):
        yield "messages", (AIMessage(content="定点报告正文"), {})

    def invoke(self, *_a, **_k):
        return {"messages": [AIMessage(content="定点报告正文")]}


def test_persist_false_skips_chat_store(monkeypatch):
    calls = {"ensure": 0, "append": 0}

    monkeypatch.setattr(
        graph_mod,
        "ensure_session",
        lambda *a, **k: calls.__setitem__("ensure", calls["ensure"] + 1) or "sid",
    )
    monkeypatch.setattr(
        graph_mod,
        "append_message",
        lambda *a, **k: calls.__setitem__("append", calls["append"] + 1),
    )
    monkeypatch.setattr(graph_mod, "session_exists", lambda *a, **k: True)
    monkeypatch.setattr(graph_mod, "build_context_history", lambda *a, **k: [])
    monkeypatch.setattr(graph_mod, "build_chat_model", lambda *_a, **_k: object())
    monkeypatch.setattr(graph_mod, "build_tools", lambda *_a, **_k: [])
    monkeypatch.setattr(
        graph_mod, "build_system_prompt", lambda *_a, **_k: "sys"
    )
    monkeypatch.setattr(
        graph_mod, "create_react_agent", lambda *_a, **_k: _FakeAgent()
    )
    monkeypatch.setattr(
        "app.advisor.knowledge.build_always_knowledge_text",
        lambda *_: "",
    )
    monkeypatch.setattr(
        "app.advisor.agent.web_limits.reset_web_turn_counters",
        lambda: None,
    )

    events = list(
        graph_mod._iter_agent_chat_events_sync(
            "u1",
            "hello",
            session_id=None,
            progress_trace=[],
            run_at_mode=True,
            persist=False,
        )
    )
    assert calls["ensure"] == 0
    assert calls["append"] == 0
    done = [e for e in events if e["event"] == "done"][0]
    assert done["data"]["session_id"] is None
    assert "定点报告正文" in done["data"]["reply"]
