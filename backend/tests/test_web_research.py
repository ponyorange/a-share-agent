import json

from app.advisor.agent import web_research


def test_run_web_research_parses_text_and_sources(monkeypatch):
    monkeypatch.setattr(
        web_research,
        "_post_messages",
        lambda **kw: {
            "content": [
                {"type": "text", "text": "结论正文"},
                {
                    "type": "web_search_tool_result",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://example.com/1",
                            "title": "T1",
                        }
                    ],
                },
            ]
        },
    )
    out = json.loads(web_research.run_web_research("sk-x", "什么是科创板？"))
    assert "结论" in out["answer"]
    assert out["sources"] == ["https://example.com/1"]


def test_run_web_research_truncates_query(monkeypatch):
    seen: dict = {}

    def capture(**kw):
        seen["messages"] = kw["messages"]
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(web_research, "_post_messages", capture)
    monkeypatch.setattr(
        web_research,
        "get_agent_web_config",
        lambda: {
            "web_research": {
                "model": "deepseek-v4-flash",
                "anthropic_base_url": "https://api.deepseek.com/anthropic",
                "server_tool_type": "web_search_20250305",
                "max_tokens": 100,
                "timeout_seconds": 10,
                "max_query_chars": 10,
            }
        },
    )
    web_research.run_web_research("sk", "abcdefghijklmnopqrstuvwxyz")
    assert len(seen["messages"][0]["content"]) == 10
