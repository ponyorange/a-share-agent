from __future__ import annotations

import inspect

from app.advisor.agent import llm
from app.advisor.agent.graph import iter_agent_chat_events, run_agent_chat
from app.advisor.config_loader import default_config, reload_config


class FakeChatModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_build_chat_model_uses_slot_and_tier(monkeypatch):
    def fake_resolve(user_id, slot):
        models = {
            "agent": "user-default",
            "committee_quick": "fast",
            "committee_deep": "reasoner",
        }
        return {
            "api_key": "secret",
            "base_url": "https://llm.example/v1",
            "model": models[slot],
            "provider": "deepseek",
        }

    monkeypatch.setattr(llm, "resolve_llm_credentials", fake_resolve)
    monkeypatch.setattr(llm, "ChatOpenAI", FakeChatModel)

    default = llm.build_chat_model("u", slot="agent")
    quick = llm.build_chat_model("u", tier="quick")
    deep = llm.build_chat_model("u", tier="deep", request_timeout=12)
    assert default.kwargs["model"] == "user-default"
    assert quick.kwargs["model"] == "fast"
    assert deep.kwargs["model"] == "reasoner"
    assert deep.kwargs["timeout"] == 12
    assert "temperature" in default.kwargs


def test_kimi_omits_temperature(monkeypatch):
    monkeypatch.setattr(
        llm,
        "resolve_llm_credentials",
        lambda user_id, slot: {
            "api_key": "secret",
            "base_url": "https://api.moonshot.cn/v1",
            "model": "kimi-k2.6",
            "provider": "kimi",
        },
    )
    monkeypatch.setattr(llm, "ChatOpenAI", FakeChatModel)
    model = llm.build_chat_model("u", slot="paper", temperature=0.2)
    assert "temperature" not in model.kwargs
    assert model.kwargs["model"] == "kimi-k2.6"


def test_build_chat_model_requires_slot_or_tier(monkeypatch):
    monkeypatch.setattr(llm, "ChatOpenAI", FakeChatModel)
    try:
        llm.build_chat_model("u")
        raise AssertionError("expected TypeError or ValueError")
    except (TypeError, ValueError):
        pass


def test_yaml_contains_committee_models_and_budget_defaults():
    reload_config()
    committee = default_config()["committee"]
    assert committee["models"]["quick"] != committee["models"]["deep"]
    assert committee["budget"]["max_calls"] >= 10
    assert committee["budget"]["total_timeout_seconds"] > 0


def test_legacy_agent_chat_public_signatures_remain_compatible():
    run_parameters = inspect.signature(run_agent_chat).parameters
    event_parameters = inspect.signature(iter_agent_chat_events).parameters
    assert list(run_parameters) == [
        "user_id",
        "message",
        "session_id",
        "history",
        "run_at_mode",
        "persist",
    ]
    assert list(event_parameters) == [
        "user_id",
        "message",
        "session_id",
        "run_at_mode",
        "persist",
    ]
