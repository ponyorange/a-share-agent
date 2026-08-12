from app.advisor.agent.progress import bind_progress_sink, emit_progress


def test_emit_web_research_step():
    events: list[dict] = []
    with bind_progress_sink(events.append):
        emit_progress(step="web_research", status="started", phase="main_agent")
    assert events[-1]["step"] == "web_research"
    assert "联网调研" in events[-1]["message"]


def test_emit_web_search_and_fetch_url():
    events: list[dict] = []
    with bind_progress_sink(events.append):
        emit_progress(step="web_search", status="completed", phase="main_agent")
        emit_progress(step="fetch_url", status="failed", phase="main_agent")
    assert events[0]["step"] == "web_search"
    assert events[1]["step"] == "fetch_url"


def test_emit_fetch_url_escalation_steps():
    events: list[dict] = []
    with bind_progress_sink(events.append):
        emit_progress(step="fetch_url_l2", status="started", phase="main_agent")
        emit_progress(step="fetch_url_l3", status="started", phase="main_agent")
    assert "增强" in events[0]["message"] or "Scrapling" in events[0]["message"]
    assert "浏览器" in events[1]["message"] or "增强" in events[1]["message"]

