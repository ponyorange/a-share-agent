"""Per-user DeepSeek / LLM API settings (encrypted at rest)."""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from openai import OpenAI

from ..db import get_db

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
PROVIDER = "deepseek"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fernet(name: str = "LLM_ENCRYPTION_KEY") -> Fernet:
    raw = (os.getenv(name) or "").strip()
    if len(raw.encode("utf-8")) < 32:
        raise RuntimeError(f"{name} must contain at least 32 bytes")
    secret = raw.encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_api_key(raw: str) -> str:
    return _fernet().encrypt(raw.strip().encode()).decode()


def decrypt_api_key(token: str) -> str:
    keys = ["LLM_ENCRYPTION_KEY"]
    if (os.getenv("LLM_ENCRYPTION_KEY_PREVIOUS") or "").strip():
        keys.append("LLM_ENCRYPTION_KEY_PREVIOUS")
    for name in keys:
        try:
            return _fernet(name).decrypt(token.encode()).decode()
        except InvalidToken:
            continue
    raise ValueError("API Key 解密失败，请重新配置")


def key_hint(raw: str) -> str:
    s = raw.strip()
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}…{s[-4:]}"


def get_llm_settings(user_id: str) -> dict[str, Any] | None:
    return get_db().user_llm_settings.find_one({"user_id": user_id}, {"_id": 0})


def public_llm_settings(user_id: str) -> dict[str, Any]:
    doc = get_llm_settings(user_id)
    if not doc or not doc.get("api_key_enc"):
        return {
            "configured": False,
            "provider": PROVIDER,
            "model": DEFAULT_MODEL,
            "base_url": DEFAULT_BASE_URL,
            "key_hint": None,
            "last_validated_at": None,
        }
    hint = doc.get("key_hint")
    if not hint and doc.get("api_key_enc"):
        try:
            hint = key_hint(decrypt_api_key(doc["api_key_enc"]))
        except Exception:
            hint = "****"
    return {
        "configured": True,
        "provider": doc.get("provider") or PROVIDER,
        "model": doc.get("model") or DEFAULT_MODEL,
        "base_url": doc.get("base_url") or DEFAULT_BASE_URL,
        "key_hint": hint,
        "last_validated_at": (
            doc["last_validated_at"].isoformat()
            if hasattr(doc.get("last_validated_at"), "isoformat")
            else doc.get("last_validated_at")
        ),
    }


def resolve_llm_credentials(user_id: str) -> dict[str, str]:
    """Return api_key / model / base_url for the user. Raises if not configured."""
    doc = get_llm_settings(user_id)
    if not doc or not doc.get("api_key_enc"):
        raise ValueError("尚未配置 DeepSeek API Key，请先在 Agent 设置中填写")
    return {
        "api_key": decrypt_api_key(doc["api_key_enc"]),
        "model": str(doc.get("model") or DEFAULT_MODEL),
        "base_url": str(doc.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
    }


def validate_deepseek_key(
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
) -> None:
    """Lightweight chat call to verify the key works."""
    client = OpenAI(api_key=api_key.strip(), base_url=base_url.rstrip("/"))
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=8,
    )


def save_llm_settings(
    user_id: str,
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    raw = (api_key or "").strip()
    if not raw:
        raise ValueError("API Key 不能为空")
    model_v = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    base_v = (base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    if validate:
        validate_deepseek_key(raw, model=model_v, base_url=base_v)
    now = _now()
    doc = {
        "user_id": user_id,
        "provider": PROVIDER,
        "api_key_enc": encrypt_api_key(raw),
        "key_hint": key_hint(raw),
        "model": model_v,
        "base_url": base_v.rstrip("/"),
        "updated_at": now,
        "last_validated_at": now if validate else None,
        "configured_at": now,
    }
    get_db().user_llm_settings.update_one(
        {"user_id": user_id},
        {
            "$set": doc,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return public_llm_settings(user_id)


def clear_llm_settings(user_id: str) -> dict[str, Any]:
    get_db().user_llm_settings.delete_one({"user_id": user_id})
    return public_llm_settings(user_id)
