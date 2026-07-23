from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import auth, db
from app.advisor import llm_settings


def test_mongodb_uri_is_required_and_error_does_not_echo_environment(monkeypatch):
    db.get_client.cache_clear()
    monkeypatch.delenv("MONGODB_URI", raising=False)

    with pytest.raises(RuntimeError, match="MONGODB_URI") as caught:
        db.get_client()

    assert "mongodb://" not in str(caught.value)


def test_jwt_secret_is_required_and_rejects_weak_values(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.create_token("u", "name")

    monkeypatch.setenv("JWT_SECRET", "short")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.create_token("u", "name")


def test_llm_encryption_key_is_independent_and_required(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "j" * 48)
    monkeypatch.delenv("LLM_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LLM_ENCRYPTION_KEY"):
        llm_settings.encrypt_api_key("api-key")

    monkeypatch.setenv("LLM_ENCRYPTION_KEY", "e" * 48)
    token = llm_settings.encrypt_api_key("api-key")
    assert llm_settings.decrypt_api_key(token) == "api-key"


def test_dev_seed_is_disabled_in_production_and_never_resets_existing_user(
    monkeypatch,
):
    class Users:
        def __init__(self):
            self.updated = []

        def find_one(self, query):
            return {"_id": "existing", "username": query["username"]}

        def update_one(self, *args, **kwargs):
            self.updated.append((args, kwargs))

    database = SimpleNamespace(
        users=Users(),
        portfolios=SimpleNamespace(update_one=lambda *args, **kwargs: None),
        paper_accounts=SimpleNamespace(update_one=lambda *args, **kwargs: None),
    )
    monkeypatch.setattr(auth, "get_db", lambda: database)
    monkeypatch.setattr(auth, "ensure_indexes", lambda: None)
    monkeypatch.setenv("DEV_SEED_ENABLED", "1")
    monkeypatch.setenv("DEV_SEED_USERNAME", "developer")
    monkeypatch.setenv("DEV_SEED_PASSWORD", "Strong-development-password-123")

    monkeypatch.setenv("APP_ENV", "production")
    assert auth.seed_dev_user() is False

    monkeypatch.setenv("APP_ENV", "development")
    assert auth.seed_dev_user() is False
    assert database.users.updated == []


def test_dev_seed_requires_strong_environment_password(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEV_SEED_ENABLED", "1")
    monkeypatch.setenv("DEV_SEED_USERNAME", "developer")
    monkeypatch.setenv("DEV_SEED_PASSWORD", "weak")

    with pytest.raises(RuntimeError, match="DEV_SEED_PASSWORD"):
        auth.seed_dev_user()
