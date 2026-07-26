from app.advisor.agent import web_limits


def test_consume_web_quota_blocks_after_max(monkeypatch):
    monkeypatch.setattr(
        web_limits,
        "get_agent_web_config",
        lambda: {
            "web_research": {"max_calls_per_turn": 2},
            "web_search": {"max_calls_per_turn": 5},
            "fetch_url": {"max_calls_per_turn": 8},
        },
    )
    web_limits.reset_web_turn_counters()
    assert web_limits.consume_web_quota("web_research") is None
    assert web_limits.consume_web_quota("web_research") is None
    assert web_limits.consume_web_quota("web_research") == "已达本轮调用上限"
