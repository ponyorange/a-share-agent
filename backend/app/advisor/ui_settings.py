from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from ..db import get_db

ThemeId = Literal["modern_data", "classic_market", "deep_navy"]

COLOR_KEYS = (
    "page_bg",
    "surface",
    "text_primary",
    "text_muted",
    "border",
    "brand",
    "market_up",
    "market_down",
    "success",
    "error",
)
THEME_IDS: tuple[ThemeId, ...] = ("modern_data", "classic_market", "deep_navy")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

DEFAULT_THEMES: dict[str, dict[str, str]] = {
    "modern_data": {
        "page_bg": "#F6F7FB",
        "surface": "#FFFFFF",
        "text_primary": "#273247",
        "text_muted": "#778195",
        "border": "#E5E8F1",
        "brand": "#6673D9",
        "market_up": "#3568B8",
        "market_down": "#A96918",
        "success": "#377659",
        "error": "#A84C5B",
    },
    "classic_market": {
        "page_bg": "#F7F8FA",
        "surface": "#FFFFFF",
        "text_primary": "#2A3140",
        "text_muted": "#6F7A8C",
        "border": "#E4E7ED",
        "brand": "#526FC1",
        "market_up": "#C24B5A",
        "market_down": "#328268",
        "success": "#2F7A5B",
        "error": "#B54759",
    },
    "deep_navy": {
        "page_bg": "#101724",
        "surface": "#192335",
        "text_primary": "#F2F5FA",
        "text_muted": "#99A7BB",
        "border": "#303E55",
        "brand": "#8793FF",
        "market_up": "#70A9F8",
        "market_down": "#F1B85B",
        "success": "#61C28F",
        "error": "#F17C8E",
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_ui_settings() -> dict[str, Any]:
    return {
        "active_template": "modern_data",
        "colors": dict(DEFAULT_THEMES["modern_data"]),
        "updated_at": None,
    }


def normalize_colors(colors: Mapping[str, Any]) -> dict[str, str]:
    if set(colors) != set(COLOR_KEYS):
        raise ValueError("配色字段必须完整且不能包含额外字段")
    normalized: dict[str, str] = {}
    for key in COLOR_KEYS:
        value = colors[key]
        if not isinstance(value, str) or not HEX_RE.fullmatch(value):
            raise ValueError(f"{key} 必须是 #RRGGBB")
        normalized[key] = value.upper()
    return normalized


def _public(doc: Mapping[str, Any]) -> dict[str, Any]:
    updated = doc.get("updated_at")
    return {
        "active_template": doc["active_template"],
        "colors": normalize_colors(doc["colors"]),
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
    }


def get_ui_settings(user_id: str) -> dict[str, Any]:
    doc = get_db().user_ui_settings.find_one({"user_id": user_id}, {"_id": 0})
    return _public(doc) if doc else default_ui_settings()


def save_ui_settings(
    user_id: str, *, active_template: str, colors: Mapping[str, Any]
) -> dict[str, Any]:
    if active_template not in THEME_IDS:
        raise ValueError("未知配色模板")
    normalized = normalize_colors(colors)
    now = _now()
    get_db().user_ui_settings.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "active_template": active_template,
                "colors": normalized,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return get_ui_settings(user_id)
