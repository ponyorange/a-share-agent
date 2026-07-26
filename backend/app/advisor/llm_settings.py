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
DEFAULT_WEB_RESEARCH_ENABLED = True
DEFAULT_TAVILY_ENABLED = False


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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value else None


def _web_public_fields(doc: dict[str, Any] | None) -> dict[str, Any]:
    d = doc or {}
    web_research_enabled = d.get("web_research_enabled")
    if web_research_enabled is None:
        web_research_enabled = DEFAULT_WEB_RESEARCH_ENABLED
    tavily_enabled = d.get("tavily_enabled")
    if tavily_enabled is None:
        tavily_enabled = DEFAULT_TAVILY_ENABLED
    tavily_configured = bool(d.get("tavily_api_key_enc"))
    return {
        "web_research_enabled": bool(web_research_enabled),
        "tavily_enabled": bool(tavily_enabled),
        "tavily_configured": tavily_configured,
        "tavily_key_hint": d.get("tavily_key_hint") if tavily_configured else None,
        "tavily_validated_at": _iso(d.get("tavily_validated_at")),
    }


def public_llm_settings(user_id: str) -> dict[str, Any]:
    doc = get_llm_settings(user_id)
    web = _web_public_fields(doc)
    if not doc or not doc.get("api_key_enc"):
        return {
            "configured": False,
            "provider": PROVIDER,
            "model": (doc or {}).get("model") or DEFAULT_MODEL,
            "base_url": (doc or {}).get("base_url") or DEFAULT_BASE_URL,
            "key_hint": None,
            "last_validated_at": None,
            **web,
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
        "last_validated_at": _iso(doc.get("last_validated_at")),
        **web,
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


def resolve_tavily_api_key(user_id: str) -> str | None:
    doc = get_llm_settings(user_id)
    if not doc or not doc.get("tavily_api_key_enc"):
        return None
    try:
        return decrypt_api_key(doc["tavily_api_key_enc"])
    except Exception:
        return None


def web_tool_flags(user_id: str) -> dict[str, bool]:
    doc = get_llm_settings(user_id) or {}
    web_research_enabled = doc.get("web_research_enabled")
    if web_research_enabled is None:
        web_research_enabled = DEFAULT_WEB_RESEARCH_ENABLED
    tavily_enabled = doc.get("tavily_enabled")
    if tavily_enabled is None:
        tavily_enabled = DEFAULT_TAVILY_ENABLED
    return {
        "web_research": bool(web_research_enabled) and bool(doc.get("api_key_enc")),
        "tavily": bool(tavily_enabled) and bool(doc.get("tavily_api_key_enc")),
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
    return update_llm_settings(
        user_id,
        api_key=api_key,
        model=model,
        base_url=base_url,
        validate_deepseek=validate,
    )


def update_llm_settings(
    user_id: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    web_research_enabled: bool | None = None,
    tavily_enabled: bool | None = None,
    tavily_api_key: str | None = None,
    validate_deepseek: bool = True,
) -> dict[str, Any]:
    from .agent.web_tavily import validate_tavily_key

    raw_key = (api_key or "").strip()
    raw_tavily = (tavily_api_key or "").strip()
    has_deepseek_change = bool(raw_key) or model is not None or base_url is not None
    has_web_change = (
        web_research_enabled is not None
        or tavily_enabled is not None
        or bool(raw_tavily)
    )
    if not has_deepseek_change and not has_web_change:
        raise ValueError("没有可保存的变更")

    existing = get_llm_settings(user_id) or {}
    now = _now()
    sets: dict[str, Any] = {"updated_at": now, "user_id": user_id}

    if raw_key:
        model_v = (model or existing.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        base_v = (
            (base_url or existing.get("base_url") or DEFAULT_BASE_URL).strip()
            or DEFAULT_BASE_URL
        )
        if validate_deepseek:
            validate_deepseek_key(raw_key, model=model_v, base_url=base_v)
        sets.update(
            {
                "provider": PROVIDER,
                "api_key_enc": encrypt_api_key(raw_key),
                "key_hint": key_hint(raw_key),
                "model": model_v,
                "base_url": base_v.rstrip("/"),
                "last_validated_at": now if validate_deepseek else existing.get(
                    "last_validated_at"
                ),
                "configured_at": existing.get("configured_at") or now,
            }
        )
    else:
        if model is not None:
            if not existing.get("api_key_enc"):
                raise ValueError("请先配置 DeepSeek API Key")
            sets["model"] = model.strip() or DEFAULT_MODEL
        if base_url is not None:
            if not existing.get("api_key_enc"):
                raise ValueError("请先配置 DeepSeek API Key")
            sets["base_url"] = base_url.strip() or DEFAULT_BASE_URL

    if web_research_enabled is not None:
        sets["web_research_enabled"] = bool(web_research_enabled)

    if raw_tavily:
        validate_tavily_key(raw_tavily)
        sets["tavily_api_key_enc"] = encrypt_api_key(raw_tavily)
        sets["tavily_key_hint"] = key_hint(raw_tavily)
        sets["tavily_validated_at"] = now

    effective_tavily_enabled = (
        bool(tavily_enabled)
        if tavily_enabled is not None
        else bool(
            existing.get("tavily_enabled")
            if existing.get("tavily_enabled") is not None
            else DEFAULT_TAVILY_ENABLED
        )
    )
    if tavily_enabled is not None:
        sets["tavily_enabled"] = bool(tavily_enabled)

    will_have_tavily_key = bool(raw_tavily) or bool(existing.get("tavily_api_key_enc"))
    if effective_tavily_enabled and not will_have_tavily_key:
        raise ValueError("开启 Tavily 前请先填写有效的 API Key")

    get_db().user_llm_settings.update_one(
        {"user_id": user_id},
        {"$set": sets, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return public_llm_settings(user_id)


def clear_tavily_settings(user_id: str) -> dict[str, Any]:
    get_db().user_llm_settings.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "tavily_enabled": False,
                "updated_at": _now(),
            },
            "$unset": {
                "tavily_api_key_enc": "",
                "tavily_key_hint": "",
                "tavily_validated_at": "",
            },
        },
        upsert=False,
    )
    return public_llm_settings(user_id)


def clear_llm_settings(user_id: str) -> dict[str, Any]:
    """Clear DeepSeek credentials but preserve Tavily / web toggles."""
    existing = get_llm_settings(user_id)
    if not existing:
        return public_llm_settings(user_id)
    get_db().user_llm_settings.update_one(
        {"user_id": user_id},
        {
            "$unset": {
                "api_key_enc": "",
                "key_hint": "",
                "last_validated_at": "",
                "configured_at": "",
                "provider": "",
            },
            "$set": {"updated_at": _now()},
        },
    )
    return public_llm_settings(user_id)
