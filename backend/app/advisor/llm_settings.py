"""Per-user LLM API settings (encrypted at rest)."""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..db import get_db
from .llm_providers import (
    PROVIDER_IDS,
    PROVIDERS,
    SLOT_IDS,
    compute_default_model,
    default_enabled_models,
    intersect_enabled,
    list_model_ids,
    ping_chat,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
PROVIDER = "deepseek"
DEFAULT_WEB_RESEARCH_ENABLED = True
DEFAULT_TAVILY_ENABLED = False
MISSING_KEY_MESSAGE = "尚未配置 API Key，请先在模型配置中填写"
HTTP_MISSING_KEY_DETAIL = "请先在模型配置中填写 API Key"

_LEGACY_UNSET = {
    "api_key_enc": "",
    "key_hint": "",
    "model": "",
    "base_url": "",
    "provider": "",
    "last_validated_at": "",
    "configured_at": "",
}


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


def _empty_provider_public(pid: str) -> dict[str, Any]:
    spec = PROVIDERS[pid]
    return {
        "configured": False,
        "key_hint": None,
        "last_validated_at": None,
        "available_models": [],
        "enabled_models": [],
        "default_model": spec["default_model"],
        "models_synced_at": None,
    }


def _provider_public(pid: str, pdata: dict[str, Any] | None) -> dict[str, Any]:
    spec = PROVIDERS[pid]
    d = pdata or {}
    out = _empty_provider_public(pid)
    if not d.get("api_key_enc"):
        return out
    out.update(
        {
            "configured": True,
            "key_hint": d.get("key_hint"),
            "last_validated_at": _iso(d.get("last_validated_at")),
            "available_models": list(d.get("available_models") or []),
            "enabled_models": list(d.get("enabled_models") or []),
            "default_model": d.get("default_model") or spec["default_model"],
            "models_synced_at": _iso(d.get("models_synced_at")),
        }
    )
    return out


def _any_provider_configured(doc: dict[str, Any]) -> bool:
    providers = doc.get("providers") or {}
    return any(
        bool((providers.get(pid) or {}).get("api_key_enc")) for pid in PROVIDER_IDS
    )


def _migrate_legacy_inplace(doc: dict[str, Any]) -> bool:
    providers = dict(doc.get("providers") or {})
    ds = dict(providers.get("deepseek") or {})
    if not (doc.get("api_key_enc") and not ds.get("api_key_enc")):
        return False
    old_model = str(doc.get("model") or PROVIDERS["deepseek"]["default_model"])
    pre = list(PROVIDERS["deepseek"]["preselect"])
    enabled: list[str] = []
    for mid in [old_model, *pre]:
        if mid not in enabled:
            enabled.append(mid)
    providers["deepseek"] = {
        "api_key_enc": doc["api_key_enc"],
        "key_hint": doc.get("key_hint"),
        "last_validated_at": doc.get("last_validated_at"),
        "configured_at": doc.get("configured_at"),
        "available_models": [],
        "enabled_models": enabled,
        "default_model": old_model,
        "models_synced_at": None,
    }
    doc["providers"] = providers
    doc["slots"] = {sid: {"provider": "deepseek", "model": old_model} for sid in SLOT_IDS}
    for key in _LEGACY_UNSET:
        doc.pop(key, None)
    return True


def ensure_migrated(user_id: str) -> dict[str, Any]:
    coll = get_db().user_llm_settings
    doc = coll.find_one({"user_id": user_id}, {"_id": 0}) or {}
    if not doc:
        return {}
    if _migrate_legacy_inplace(doc):
        coll.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "providers": doc["providers"],
                    "slots": doc["slots"],
                    "updated_at": _now(),
                    "user_id": user_id,
                },
                "$unset": _LEGACY_UNSET,
            },
            upsert=True,
        )
    return doc


def public_llm_settings(user_id: str) -> dict[str, Any]:
    doc = ensure_migrated(user_id)
    web = _web_public_fields(doc)
    providers_doc = doc.get("providers") or {}
    slots_doc = doc.get("slots") or {}
    configured = _any_provider_configured(doc)
    slots: dict[str, Any] = {}
    for sid in SLOT_IDS:
        raw = slots_doc.get(sid)
        if not configured or not isinstance(raw, dict) or not raw.get("provider"):
            slots[sid] = None
        else:
            slots[sid] = {"provider": raw["provider"], "model": raw.get("model")}
    return {
        "configured": configured,
        "providers": {
            pid: _provider_public(pid, providers_doc.get(pid)) for pid in PROVIDER_IDS
        },
        "slots": slots,
        **web,
    }


def resolve_llm_credentials(user_id: str, slot: str = "agent") -> dict[str, str]:
    """Return api_key / model / base_url / provider for a slot. Raises if missing."""
    if slot not in SLOT_IDS:
        raise ValueError("未知模型槽位")
    doc = ensure_migrated(user_id)
    raw = (doc.get("slots") or {}).get(slot)
    if not isinstance(raw, dict) or not raw.get("provider"):
        raise ValueError(MISSING_KEY_MESSAGE)
    pid = str(raw["provider"])
    if pid not in PROVIDERS:
        raise ValueError(MISSING_KEY_MESSAGE)
    pdata = (doc.get("providers") or {}).get(pid) or {}
    if not pdata.get("api_key_enc"):
        raise ValueError(MISSING_KEY_MESSAGE)
    return {
        "api_key": decrypt_api_key(pdata["api_key_enc"]),
        "model": str(
            raw.get("model")
            or pdata.get("default_model")
            or PROVIDERS[pid]["default_model"]
        ),
        "base_url": str(PROVIDERS[pid]["base_url"]).rstrip("/"),
        "provider": pid,
    }


def resolve_deepseek_api_key(user_id: str) -> str | None:
    doc = ensure_migrated(user_id)
    enc = ((doc.get("providers") or {}).get("deepseek") or {}).get("api_key_enc")
    if not enc:
        return None
    try:
        return decrypt_api_key(enc)
    except Exception:
        return None


def resolve_tavily_api_key(user_id: str) -> str | None:
    doc = get_llm_settings(user_id)
    if not doc or not doc.get("tavily_api_key_enc"):
        return None
    try:
        return decrypt_api_key(doc["tavily_api_key_enc"])
    except Exception:
        return None


def web_tool_flags(user_id: str, *, agent_tools: bool = True) -> dict[str, bool]:
    doc = ensure_migrated(user_id) or {}
    web_research_enabled = doc.get("web_research_enabled")
    if web_research_enabled is None:
        web_research_enabled = DEFAULT_WEB_RESEARCH_ENABLED
    tavily_enabled = doc.get("tavily_enabled")
    if tavily_enabled is None:
        tavily_enabled = DEFAULT_TAVILY_ENABLED
    has_ds = bool(
        ((doc.get("providers") or {}).get("deepseek") or {}).get("api_key_enc")
        or doc.get("api_key_enc")
    )
    agent_is_ds = False
    slot = (doc.get("slots") or {}).get("agent")
    if isinstance(slot, dict) and slot.get("provider") == "deepseek":
        agent_is_ds = True
    research = bool(web_research_enabled) and has_ds and (
        agent_is_ds if agent_tools else True
    )
    return {
        "web_research": research,
        "tavily": bool(tavily_enabled) and bool(doc.get("tavily_api_key_enc")),
    }


def validate_deepseek_key(
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
) -> None:
    """Lightweight chat call to verify the key works."""
    ping_chat(api_key, base_url, model, provider="deepseek")


def _first_remaining_provider(providers: dict[str, Any]) -> str | None:
    for pid in PROVIDER_IDS:
        if (providers.get(pid) or {}).get("api_key_enc"):
            return pid
    return None


def _remap_slots(slots: dict[str, Any], providers: dict[str, Any]) -> dict[str, Any]:
    remain = _first_remaining_provider(providers)
    if remain is None:
        return {sid: None for sid in SLOT_IDS}
    default = (providers[remain] or {}).get("default_model") or PROVIDERS[remain][
        "default_model"
    ]
    out: dict[str, Any] = {}
    for sid in SLOT_IDS:
        cur = slots.get(sid) if isinstance(slots.get(sid), dict) else None
        pid = (cur or {}).get("provider")
        pdata = providers.get(pid) or {} if pid else {}
        if cur and pdata.get("api_key_enc"):
            enabled = list(pdata.get("enabled_models") or [])
            model = cur.get("model") if cur.get("model") in enabled else pdata.get(
                "default_model"
            )
            out[sid] = {"provider": pid, "model": model}
        else:
            out[sid] = {"provider": remain, "model": default}
    return out


def _write_providers_slots(
    user_id: str, providers: dict[str, Any], slots: dict[str, Any]
) -> dict[str, Any]:
    now = _now()
    get_db().user_llm_settings.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "providers": providers,
                "slots": slots,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return public_llm_settings(user_id)


def save_provider_key(user_id: str, provider: str, api_key: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("未知模型提供方")
    raw = api_key.strip()
    if not raw:
        raise ValueError("请填写 API Key")
    spec = PROVIDERS[provider]
    list_ok = True
    try:
        ids = list_model_ids(raw, spec["base_url"])
    except Exception:
        ids = []
        list_ok = False
    ping_model = spec["default_model"]
    if ids:
        ping_model = ping_model if ping_model in ids else ids[0]
    ping_chat(raw, spec["base_url"], ping_model, provider=provider)
    now = _now()
    if list_ok:
        available = [{"id": mid} for mid in ids]
        enabled = default_enabled_models(provider, ids)
        synced = now
    else:
        available = []
        enabled = [spec["default_model"]]
        synced = None
    default_model = compute_default_model(provider, enabled)
    existing = ensure_migrated(user_id)
    was_any = _any_provider_configured(existing)
    providers = dict(existing.get("providers") or {})
    prev = dict(providers.get(provider) or {})
    providers[provider] = {
        **prev,
        "api_key_enc": encrypt_api_key(raw),
        "key_hint": key_hint(raw),
        "last_validated_at": now,
        "configured_at": prev.get("configured_at") or now,
        "available_models": available,
        "enabled_models": enabled,
        "default_model": default_model,
        "models_synced_at": synced,
    }
    slots = dict(existing.get("slots") or {})
    if not was_any:
        slots = {sid: {"provider": provider, "model": default_model} for sid in SLOT_IDS}
    return _write_providers_slots(user_id, providers, slots)


def refresh_provider_models(user_id: str, provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("未知模型提供方")
    existing = ensure_migrated(user_id)
    pdata = dict((existing.get("providers") or {}).get(provider) or {})
    enc = pdata.get("api_key_enc")
    if not enc:
        raise ValueError("请先配置该模型提供方")
    raw = decrypt_api_key(enc)
    spec = PROVIDERS[provider]
    ids = list_model_ids(raw, spec["base_url"])
    available = [{"id": mid} for mid in ids]
    enabled = intersect_enabled(list(pdata.get("enabled_models") or []), ids)
    if not enabled:
        enabled = default_enabled_models(provider, ids)
    default_model = compute_default_model(provider, enabled)
    pdata.update(
        {
            "available_models": available,
            "enabled_models": enabled,
            "default_model": default_model,
            "models_synced_at": _now(),
        }
    )
    providers = dict(existing.get("providers") or {})
    providers[provider] = pdata
    slots = dict(existing.get("slots") or {})
    for sid in SLOT_IDS:
        cur = slots.get(sid)
        if isinstance(cur, dict) and cur.get("provider") == provider:
            if cur.get("model") not in enabled:
                slots[sid] = {"provider": provider, "model": default_model}
    return _write_providers_slots(user_id, providers, slots)


def clear_provider(user_id: str, provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("未知模型提供方")
    existing = ensure_migrated(user_id)
    providers = dict(existing.get("providers") or {})
    providers.pop(provider, None)
    slots = _remap_slots(dict(existing.get("slots") or {}), providers)
    return _write_providers_slots(user_id, providers, slots)


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
    enabled_models: dict[str, list[str]] | None = None,
    slots: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    from .agent.web_tavily import validate_tavily_key

    raw_key = (api_key or "").strip()
    raw_tavily = (tavily_api_key or "").strip()
    has_legacy = bool(raw_key) or model is not None or base_url is not None
    has_web_change = (
        web_research_enabled is not None
        or tavily_enabled is not None
        or bool(raw_tavily)
    )
    has_new = enabled_models is not None or slots is not None
    if not has_legacy and not has_web_change and not has_new:
        raise ValueError("没有可保存的变更")

    if raw_key:
        save_provider_key(user_id, "deepseek", raw_key)

    existing = ensure_migrated(user_id)
    now = _now()
    providers = dict(existing.get("providers") or {})
    current_slots = dict(existing.get("slots") or {})
    sets: dict[str, Any] = {"updated_at": now, "user_id": user_id}

    if enabled_models is not None:
        for pid, chosen in enabled_models.items():
            if pid not in PROVIDERS:
                raise ValueError("未知模型提供方")
            pdata = dict(providers.get(pid) or {})
            if not pdata.get("api_key_enc"):
                raise ValueError("请先配置该模型提供方")
            picked = [str(x).strip() for x in (chosen or []) if str(x).strip()]
            if not picked:
                raise ValueError("至少勾选一个模型")
            avail_ids = [
                str(x.get("id"))
                for x in (pdata.get("available_models") or [])
                if isinstance(x, dict) and x.get("id")
            ]
            if avail_ids:
                if any(mid not in avail_ids for mid in picked):
                    raise ValueError("模型未在可用列表中勾选")
            else:
                sys_default = str(PROVIDERS[pid]["default_model"])
                if any(mid != sys_default for mid in picked):
                    raise ValueError("模型未在可用列表中勾选")
            default_model = compute_default_model(pid, picked)
            pdata["enabled_models"] = picked
            pdata["default_model"] = default_model
            providers[pid] = pdata
            for sid in SLOT_IDS:
                cur = current_slots.get(sid)
                if isinstance(cur, dict) and cur.get("provider") == pid:
                    if cur.get("model") not in picked:
                        current_slots[sid] = {"provider": pid, "model": default_model}
        sets["providers"] = providers
        sets["slots"] = current_slots

    if slots is not None:
        merged_enabled = {
            pid: list((providers.get(pid) or {}).get("enabled_models") or [])
            for pid in PROVIDER_IDS
        }
        for sid, raw in slots.items():
            if sid not in SLOT_IDS:
                raise ValueError("未知槽位")
            if not isinstance(raw, dict):
                raise ValueError("未知槽位")
            pid = str(raw.get("provider") or "").strip()
            mid = str(raw.get("model") or "").strip()
            pdata = providers.get(pid) or {}
            if not pdata.get("api_key_enc"):
                raise ValueError("请先配置该模型提供方")
            allow = merged_enabled.get(pid) or []
            if not allow:
                allow = [str(pdata.get("default_model") or PROVIDERS[pid]["default_model"])]
            if mid not in allow:
                raise ValueError("模型未在可用列表中勾选")
            current_slots[sid] = {"provider": pid, "model": mid}
        sets["slots"] = current_slots
        sets["providers"] = providers

    if model is not None and slots is None:
        pdata = dict(providers.get("deepseek") or {})
        if not pdata.get("api_key_enc"):
            raise ValueError(HTTP_MISSING_KEY_DETAIL)
        mid = model.strip() or DEFAULT_MODEL
        allow = list(pdata.get("enabled_models") or [])
        if allow and mid not in allow:
            raise ValueError("模型未在可用列表中勾选")
        for sid in SLOT_IDS:
            cur = current_slots.get(sid)
            if isinstance(cur, dict) and cur.get("provider") == "deepseek":
                current_slots[sid] = {"provider": "deepseek", "model": mid}
        sets["slots"] = current_slots

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
    """Clear all LLM provider credentials but preserve Tavily / web toggles."""
    existing = ensure_migrated(user_id)
    if not existing:
        return public_llm_settings(user_id)
    providers = dict(existing.get("providers") or {})
    for pid in PROVIDER_IDS:
        if pid in providers:
            providers[pid] = {
                k: v
                for k, v in dict(providers[pid] or {}).items()
                if k
                not in {
                    "api_key_enc",
                    "key_hint",
                    "last_validated_at",
                    "configured_at",
                    "available_models",
                    "enabled_models",
                    "default_model",
                    "models_synced_at",
                }
            }
            if not providers[pid]:
                del providers[pid]
    get_db().user_llm_settings.update_one(
        {"user_id": user_id},
        {
            "$unset": _LEGACY_UNSET,
            "$set": {
                "updated_at": _now(),
                "providers": providers,
                "slots": {sid: None for sid in SLOT_IDS},
            },
        },
    )
    return public_llm_settings(user_id)
