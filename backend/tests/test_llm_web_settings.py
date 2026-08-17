from copy import deepcopy

import pytest

from app.advisor import llm_settings


class FakeColl:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def find_one(self, query, projection=None):
        uid = query.get("user_id")
        doc = self.docs.get(uid)
        return deepcopy(doc) if doc else None

    def update_one(self, query, update, upsert=False):
        uid = query["user_id"]
        doc = self.docs.get(uid)
        if doc is None:
            if not upsert:
                return
            doc = {"user_id": uid}
            self.docs[uid] = doc
        if "$set" in update:
            doc.update(deepcopy(update["$set"]))
        if "$unset" in update:
            for key in update["$unset"]:
                doc.pop(key, None)
        if "$setOnInsert" in update and doc.get("created_at") is None:
            # only apply on insert; simplify: if created_at missing
            for k, v in update["$setOnInsert"].items():
                doc.setdefault(k, v)

    def delete_one(self, query):
        self.docs.pop(query.get("user_id"), None)

    def insert_one(self, doc):
        self.docs[doc["user_id"]] = deepcopy(doc)


class FakeDb:
    def __init__(self):
        self.user_llm_settings = FakeColl()


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(llm_settings, "get_db", lambda: fake)
    monkeypatch.setenv(
        "LLM_ENCRYPTION_KEY",
        "unit-test-llm-encryption-key-32bytes-min!!",
    )
    return fake


def test_public_defaults_without_doc(db):
    pub = llm_settings.public_llm_settings("u1")
    assert pub["configured"] is False
    assert pub["web_research_enabled"] is True
    assert pub["tavily_enabled"] is False
    assert pub["tavily_configured"] is False
    assert pub["providers"]["deepseek"]["configured"] is False
    assert pub["providers"]["kimi"]["default_model"] == "kimi-k2.6"
    assert pub["providers"]["qwen"]["default_model"] == "qwen3.7-plus"
    assert pub["slots"]["agent"] is None
    assert "model" not in pub


def test_migrates_legacy_top_level_key(db, monkeypatch):
    monkeypatch.setenv(
        "LLM_ENCRYPTION_KEY",
        "unit-test-llm-encryption-key-32bytes-min!!",
    )
    enc = llm_settings.encrypt_api_key("sk-legacy-key-xxxx")
    db.user_llm_settings.docs["u1"] = {
        "user_id": "u1",
        "api_key_enc": enc,
        "key_hint": "sk-l…xxxx",
        "model": "deepseek-v4-pro",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
    }
    pub = llm_settings.public_llm_settings("u1")
    assert pub["configured"] is True
    assert pub["providers"]["deepseek"]["configured"] is True
    assert "deepseek-v4-pro" in pub["providers"]["deepseek"]["enabled_models"]
    assert pub["slots"]["agent"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
    }
    assert pub["slots"]["committee_deep"]["provider"] == "deepseek"
    stored = db.user_llm_settings.docs["u1"]
    assert "api_key_enc" not in stored
    assert stored["providers"]["deepseek"]["api_key_enc"]


def test_tavily_enable_without_key_raises(db, monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tavily.validate_tavily_key", lambda *a, **k: None
    )
    with pytest.raises(ValueError, match="Tavily"):
        llm_settings.update_llm_settings("u1", tavily_enabled=True)


def test_tavily_key_and_enable(db, monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tavily.validate_tavily_key", lambda *a, **k: None
    )
    pub = llm_settings.update_llm_settings(
        "u1",
        tavily_api_key="tvly-test-key-xxxxx",
        tavily_enabled=True,
        web_research_enabled=False,
    )
    assert pub["tavily_configured"] is True
    assert pub["tavily_enabled"] is True
    assert pub["web_research_enabled"] is False
    assert pub["tavily_key_hint"]


def test_clear_tavily(db, monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.web_tavily.validate_tavily_key", lambda *a, **k: None
    )
    llm_settings.update_llm_settings(
        "u1", tavily_api_key="tvly-test-key-xxxxx", tavily_enabled=True
    )
    pub = llm_settings.clear_tavily_settings("u1")
    assert pub["tavily_configured"] is False
    assert pub["tavily_enabled"] is False


def test_clear_deepseek_keeps_tavily(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    monkeypatch.setattr(
        "app.advisor.agent.web_tavily.validate_tavily_key", lambda *a, **k: None
    )
    llm_settings.update_llm_settings(
        "u1",
        api_key="sk-deepseek-test-key",
        tavily_api_key="tvly-test-key-xxxxx",
        tavily_enabled=True,
    )
    pub = llm_settings.clear_llm_settings("u1")
    assert pub["configured"] is False
    assert pub["tavily_configured"] is True
    assert pub["tavily_enabled"] is True


def test_web_tool_flags(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    monkeypatch.setattr(
        "app.advisor.agent.web_tavily.validate_tavily_key", lambda *a, **k: None
    )
    flags = llm_settings.web_tool_flags("u1")
    assert flags == {"web_research": False, "tavily": False}
    llm_settings.update_llm_settings("u1", api_key="sk-deepseek-test-key")
    flags = llm_settings.web_tool_flags("u1")
    assert flags["web_research"] is True
    assert flags["tavily"] is False


def _ok_list(ids):
    return lambda *a, **k: list(ids)


def _ok_ping(*a, **k):
    return None


def test_save_first_provider_fills_slots(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6", "kimi-k3"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    pub = llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    assert pub["configured"] is True
    assert pub["providers"]["kimi"]["configured"] is True
    assert pub["providers"]["kimi"]["enabled_models"] == ["kimi-k2.6", "kimi-k3"]
    assert pub["slots"]["agent"] == {"provider": "kimi", "model": "kimi-k2.6"}
    assert pub["slots"]["paper"]["provider"] == "kimi"


def test_save_second_provider_does_not_reset_slots(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    pub = llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    assert pub["slots"]["agent"]["provider"] == "deepseek"
    assert pub["providers"]["kimi"]["configured"] is True


def test_ping_failure_does_not_save(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))

    def boom(*a, **k):
        raise RuntimeError("unauthorized")

    monkeypatch.setattr(llm_settings, "ping_chat", boom)
    with pytest.raises(RuntimeError):
        llm_settings.save_provider_key("u1", "kimi", "bad")
    assert llm_settings.public_llm_settings("u1")["configured"] is False


def test_list_failure_still_saves_key(db, monkeypatch):
    def boom_list(*a, **k):
        raise RuntimeError("models down")

    monkeypatch.setattr(llm_settings, "list_model_ids", boom_list)
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    pub = llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    assert pub["providers"]["deepseek"]["configured"] is True
    assert pub["providers"]["deepseek"]["available_models"] == []
    assert pub["providers"]["deepseek"]["enabled_models"] == ["deepseek-v4-flash"]
    assert pub["providers"]["deepseek"]["models_synced_at"] is None


def test_refresh_intersects_and_rewrites_slots(db, monkeypatch):
    monkeypatch.setattr(
        llm_settings,
        "list_model_ids",
        _ok_list(["deepseek-v4-flash", "deepseek-v4-pro"]),
    )
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    doc = db.user_llm_settings.docs["u1"]
    doc["providers"]["deepseek"]["enabled_models"] = [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]
    doc["slots"]["agent"]["model"] = "deepseek-v4-pro"
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    pub = llm_settings.refresh_provider_models("u1", "deepseek")
    assert pub["providers"]["deepseek"]["enabled_models"] == ["deepseek-v4-flash"]
    assert pub["slots"]["agent"]["model"] == "deepseek-v4-flash"


def test_clear_provider_remaps_to_remaining(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    pub = llm_settings.clear_provider("u1", "deepseek")
    assert pub["providers"]["deepseek"]["configured"] is False
    assert pub["slots"]["agent"]["provider"] == "kimi"
    assert pub["tavily_configured"] is False


def test_update_slots_rejects_unconfigured_provider(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    with pytest.raises(ValueError, match="请先配置该模型提供方"):
        llm_settings.update_llm_settings(
            "u1",
            slots={"agent": {"provider": "kimi", "model": "kimi-k2.6"}},
        )


def test_update_enabled_and_slot_ok(db, monkeypatch):
    monkeypatch.setattr(
        llm_settings,
        "list_model_ids",
        _ok_list(["deepseek-v4-flash", "deepseek-v4-pro"]),
    )
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    pub = llm_settings.update_llm_settings(
        "u1",
        enabled_models={"deepseek": ["deepseek-v4-pro"]},
        slots={"agent": {"provider": "deepseek", "model": "deepseek-v4-pro"}},
    )
    assert pub["providers"]["deepseek"]["default_model"] == "deepseek-v4-pro"
    assert pub["slots"]["agent"]["model"] == "deepseek-v4-pro"
    assert pub["slots"]["paper"]["model"] == "deepseek-v4-pro"


def test_update_slots_coerces_model_not_in_enabled(db, monkeypatch):
    monkeypatch.setattr(
        llm_settings,
        "list_model_ids",
        _ok_list(["kimi-k2.6", "kimi-k2.7-code", "kimi-k2.5"]),
    )
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    pub = llm_settings.update_llm_settings(
        "u1",
        enabled_models={"kimi": ["kimi-k2.6", "kimi-k2.7-code"]},
        slots={"agent": {"provider": "kimi", "model": "kimi-k2.5"}},
    )
    assert pub["slots"]["agent"]["model"] == "kimi-k2.6"


def test_update_enabled_allows_multiple_when_catalog_empty(db, monkeypatch):
    def boom_list(*a, **k):
        raise RuntimeError("models down")

    monkeypatch.setattr(llm_settings, "list_model_ids", boom_list)
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    pub = llm_settings.update_llm_settings(
        "u1",
        enabled_models={"kimi": ["kimi-k2.6", "kimi-k2.7-code"]},
    )
    assert pub["providers"]["kimi"]["enabled_models"] == ["kimi-k2.6", "kimi-k2.7-code"]


def test_update_enabled_ignores_empty_unconfigured_providers(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    pub = llm_settings.update_llm_settings(
        "u1",
        enabled_models={
            "deepseek": ["deepseek-v4-flash"],
            "kimi": [],
            "qwen": [],
        },
        slots={"agent": {"provider": "deepseek", "model": "deepseek-v4-flash"}},
    )
    assert pub["providers"]["deepseek"]["configured"] is True
    assert pub["providers"]["kimi"]["configured"] is False
    assert pub["slots"]["agent"]["model"] == "deepseek-v4-flash"


def test_legacy_api_key_writes_deepseek(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    pub = llm_settings.update_llm_settings("u1", api_key="sk-deepseek-test-key")
    assert pub["providers"]["deepseek"]["configured"] is True


def test_resolve_slot_and_missing(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    with pytest.raises(ValueError, match="模型配置"):
        llm_settings.resolve_llm_credentials("u1", "agent")
    llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    creds = llm_settings.resolve_llm_credentials("u1", "agent")
    assert creds["provider"] == "kimi"
    assert creds["model"] == "kimi-k2.6"
    assert creds["base_url"] == "https://api.moonshot.cn/v1"
    assert creds["api_key"].startswith("sk-kimi")


def test_web_tool_flags_agent_vs_home(db, monkeypatch):
    monkeypatch.setattr(llm_settings, "ping_chat", _ok_ping)
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["deepseek-v4-flash"]))
    llm_settings.save_provider_key("u1", "deepseek", "sk-ds-test-key")
    monkeypatch.setattr(llm_settings, "list_model_ids", _ok_list(["kimi-k2.6"]))
    llm_settings.save_provider_key("u1", "kimi", "sk-kimi-test-key")
    llm_settings.update_llm_settings(
        "u1", slots={"agent": {"provider": "kimi", "model": "kimi-k2.6"}}
    )
    agent_flags = llm_settings.web_tool_flags("u1", agent_tools=True)
    home_flags = llm_settings.web_tool_flags("u1", agent_tools=False)
    assert agent_flags["web_research"] is False
    assert home_flags["web_research"] is True
    assert llm_settings.resolve_deepseek_api_key("u1").startswith("sk-ds")
