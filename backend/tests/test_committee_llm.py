from __future__ import annotations

import inspect

from app.advisor.agent import llm
from app.advisor.agent.graph import iter_agent_chat_events, run_agent_chat
from app.advisor.config_loader import default_config, reload_config


class FakeChatModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_build_chat_model_preserves_default_and_supports_tiers(monkeypatch):
    monkeypatch.setattr(
        llm,
        "resolve_llm_credentials",
        lambda user_id: {
            "api_key": "secret",
            "base_url": "https://llm.example/v1",
            "model": "user-default",
        },
    )
    monkeypatch.setattr(llm, "ChatOpenAI", FakeChatModel)

    default = llm.build_chat_model("u")
    quick = llm.build_chat_model(
        "u",
        tier="quick",
        committee_config={"models": {"quick": "fast", "deep": "reasoner"}},
    )
    deep = llm.build_chat_model(
        "u",
        tier="deep",
        committee_config={"models": {"quick": "fast", "deep": "reasoner"}},
    )

    assert default.kwargs["model"] == "user-default"
    assert quick.kwargs["model"] == "fast"
    assert deep.kwargs["model"] == "reasoner"
    assert quick.kwargs["api_key"] == "secret"
    assert quick.kwargs["base_url"] == "https://llm.example/v1"


def test_tier_loads_committee_defaults_and_request_timeout(monkeypatch):
    monkeypatch.setattr(
        llm,
        "resolve_llm_credentials",
        lambda user_id: {
            "api_key": "secret",
            "base_url": "https://llm.example/v1",
            "model": "user-default",
        },
    )
    monkeypatch.setattr(llm, "ChatOpenAI", FakeChatModel)
    monkeypatch.setattr(
        llm,
        "load_config",
        lambda: {
            "committee": {
                "models": {"quick": "configured-quick", "deep": "configured-deep"}
            }
        },
        raising=False,
    )

    quick = llm.build_chat_model(
        "u",
        tier="quick",
        request_timeout=12,
    )
    assert quick.kwargs["model"] == "configured-quick"
    assert quick.kwargs["timeout"] == 12


def test_yaml_contains_committee_models_and_budget_defaults():
    reload_config()
    committee = default_config()["committee"]
    assert committee["models"]["quick"] != committee["models"]["deep"]
    assert committee["budget"]["max_calls"] >= 10
    assert committee["budget"]["total_timeout_seconds"] > 0


def test_legacy_agent_chat_public_signatures_remain_compatible():
    run_parameters = inspect.signature(run_agent_chat).parameters
    event_parameters = inspect.signature(iter_agent_chat_events).parameters
    assert list(run_parameters) == ["user_id", "message", "session_id", "history"]
    assert list(event_parameters) == ["user_id", "message", "session_id"]
