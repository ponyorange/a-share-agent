from app.advisor.agent.web_tools import build_web_tools


def test_mount_research_includes_fetch_url(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": True, "tavily": False},
    )
    names = {t.name for t in build_web_tools("u1")}
    assert names == {"web_research", "fetch_url"}


def test_mount_tavily_only(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": False, "tavily": True},
    )
    names = {t.name for t in build_web_tools("u1")}
    assert names == {"web_search", "fetch_url"}


def test_mount_both(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": True, "tavily": True},
    )
    names = {t.name for t in build_web_tools("u1")}
    assert names == {"web_research", "web_search", "fetch_url"}


def test_mount_none(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tools.web_tool_flags",
        lambda uid: {"web_research": False, "tavily": False},
    )
    assert build_web_tools("u1") == []
