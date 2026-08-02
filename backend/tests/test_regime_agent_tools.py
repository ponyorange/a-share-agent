import json

import pytest

from app.advisor.agent import tools as agent_tools
from app.advisor.agent.graph import SYSTEM_PROMPT
from app.advisor.agent.tools import build_tools


@pytest.fixture(autouse=True)
def _isolate_external_tool_builders(monkeypatch):
    monkeypatch.setattr(agent_tools.context, "bind_user", lambda user_id: None)
    monkeypatch.setattr(agent_tools, "build_web_tools", lambda user_id: [])
    monkeypatch.setattr(agent_tools, "build_agent_python_tools", lambda user_id: [])
    monkeypatch.setattr(agent_tools, "build_delegate_data_tool", lambda user_id: None)


def _tool_by_name(name: str):
    return next(
        t for t in build_tools("fake-user-id") if t is not None and t.name == name
    )


def test_build_tools_includes_regime_tools():
    tools = build_tools("fake-user-id")
    names = {t.name for t in tools if t is not None}

    assert "get_market_regime" in names
    assert "get_sentiment_dashboard" in names


def test_regime_tools_return_json(monkeypatch):
    monkeypatch.setattr(
        agent_tools,
        "get_current_regime",
        lambda: {
            "gate_level": "risk_off",
            "position_cap": 0.15,
            "data_quality": "ok",
        },
        raising=False,
    )
    monkeypatch.setattr(
        agent_tools,
        "get_sentiment_detail",
        lambda: {"metrics": {"limit_up_count": 8}, "sentiment_cycle": "ebb"},
        raising=False,
    )

    regime = json.loads(_tool_by_name("get_market_regime").invoke({}))
    sentiment = json.loads(_tool_by_name("get_sentiment_dashboard").invoke({}))

    assert regime["gate_level"] == "risk_off"
    assert regime["position_cap"] == 0.15
    assert sentiment["metrics"]["limit_up_count"] == 8


def test_get_today_recommendations_passes_regime_override(monkeypatch):
    seen = {}

    monkeypatch.setattr(agent_tools, "effective_rec_date", lambda: "2026-08-02")
    monkeypatch.setattr(
        agent_tools,
        "has_snapshot",
        lambda trade_date, user_id=None: True,
    )

    def fake_snapshot(trade_date, *, board=None, user_id=None, regime_override=False):
        seen["regime_override"] = regime_override
        return {
            "buy_threshold": 0.7,
            "boards": {"hs": {"count": 0, "items": []}},
        }

    monkeypatch.setattr(agent_tools, "snapshot_as_recommendations", fake_snapshot)

    payload = json.loads(
        _tool_by_name("get_today_recommendations").invoke(
            {"board": "hs", "regime_override": True}
        )
    )

    assert seen["regime_override"] is True
    assert payload["boards"]["hs"]["count"] == 0


def test_system_prompt_includes_regime_rules():
    assert "25. 买卖/仓位/今天能否交易：先 get_market_regime" in SYSTEM_PROMPT
    assert "26. gate_level=risk_off" in SYSTEM_PROMPT
    assert "27. 用户明确 override 时：get_today_recommendations" in SYSTEM_PROMPT
    assert "28. 打板情绪细节用 get_sentiment_dashboard" in SYSTEM_PROMPT
