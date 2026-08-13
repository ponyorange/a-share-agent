"""Email thresholds and direction labels."""

from __future__ import annotations

from typing import Any

_LOW_CATEGORIES = frozenset({"policy", "regulation", "macro"})
_LABELS = {
    "up": "利好",
    "down": "利空",
    "mixed": "分化",
}


def direction_label(direction: str) -> str:
    return _LABELS.get(str(direction or "").strip(), "影响不明")


def _score(interpretation: dict[str, Any]) -> float:
    try:
        return float(interpretation.get("impact_score"))
    except (TypeError, ValueError):
        return 0.0


def _has_sector(interpretation: dict[str, Any]) -> bool:
    for item in interpretation.get("sectors") or []:
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            return True
    return False


def _has_verified_symbol(interpretation: dict[str, Any]) -> bool:
    for item in interpretation.get("symbols") or []:
        if not isinstance(item, dict):
            continue
        if item.get("verified") is False:
            continue
        if str(item.get("symbol") or "").strip():
            return True
    return False


def should_email(interpretation: dict[str, Any], sensitivity: str) -> bool:
    score = _score(interpretation)
    category = str(interpretation.get("category") or "").strip()
    level = str(sensitivity or "medium").strip() or "medium"
    if level == "low":
        return score >= 0.75 and category in _LOW_CATEGORIES
    if level == "high":
        return score >= 0.30 and (_has_sector(interpretation) or _has_verified_symbol(interpretation))
    return score >= 0.50
