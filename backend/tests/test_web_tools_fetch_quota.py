from app.advisor.agent import web_tools as wt
from app.advisor.agent.web_limits import reset_web_turn_counters


def test_fetch_url_quota_once_across_escalation(monkeypatch):
    monkeypatch.setattr(
        wt, "web_tool_flags", lambda uid: {"web_research": True, "tavily": False}
    )
    reset_web_turn_counters()
    calls = {"n": 0}
    quota_calls = {"n": 0}
    real_consume = wt.consume_web_quota

    def counting_consume(kind: str):
        out = real_consume(kind)  # type: ignore[arg-type]
        if kind == "fetch_url":
            quota_calls["n"] += 1
        return out

    def fake_escalation(url, on_level=None, **kwargs):
        calls["n"] += 1
        if on_level:
            on_level("httpx")
            on_level("scrapling")
        return "# fetch_via: scrapling\n" + ("Z" * 250)

    monkeypatch.setattr(wt, "consume_web_quota", counting_consume)
    monkeypatch.setattr(wt, "fetch_url_with_escalation", fake_escalation)
    tool = next(t for t in wt.build_web_tools("u1") if t.name == "fetch_url")
    out = tool.invoke({"url": "https://example.com"})
    assert not out.startswith("错误：")
    assert calls["n"] == 1
    assert quota_calls["n"] == 1
