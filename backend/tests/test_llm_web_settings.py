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
    monkeypatch.setattr(llm_settings, "validate_deepseek_key", lambda *a, **k: None)
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
    monkeypatch.setattr(llm_settings, "validate_deepseek_key", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.advisor.agent.web_tavily.validate_tavily_key", lambda *a, **k: None
    )
    flags = llm_settings.web_tool_flags("u1")
    assert flags == {"web_research": False, "tavily": False}
    llm_settings.update_llm_settings("u1", api_key="sk-deepseek-test-key")
    flags = llm_settings.web_tool_flags("u1")
    assert flags["web_research"] is True
    assert flags["tavily"] is False
