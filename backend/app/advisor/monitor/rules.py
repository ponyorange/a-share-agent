"""Pure rule evaluation and alert cooldown helpers for monitor jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

FLOW_TYPES = frozenset({"flow_spike_in", "flow_spike_out"})
RULE_TYPES = frozenset(
    {
        "price_below",
        "price_above",
        "day_chg_below",
        "day_chg_above",
        *FLOW_TYPES,
    }
)
MEAN_ABS_FLOOR = 1e4


def evaluate_rule(rule: dict[str, Any], quote: dict[str, Any]) -> bool:
    rtype = str(rule.get("type") or "")
    if rtype in FLOW_TYPES:
        return False
    if rtype not in RULE_TYPES:
        return False
    try:
        threshold = float(rule.get("value"))
    except (TypeError, ValueError):
        return False

    if rtype in ("price_below", "price_above"):
        price = quote.get("price")
        if price is None:
            return False
        try:
            px = float(price)
        except (TypeError, ValueError):
            return False
        if rtype == "price_below":
            return px <= threshold
        return px >= threshold

    chg = quote.get("day_chg_pct")
    if chg is None:
        return False
    try:
        pct = float(chg)
    except (TypeError, ValueError):
        return False
    if rtype == "day_chg_below":
        return pct <= threshold
    return pct >= threshold


def evaluate_flow_rule(rule: dict[str, Any], flow: dict[str, Any] | None) -> bool:
    if not flow or not flow.get("ok"):
        return False
    rtype = str(rule.get("type") or "")
    if rtype not in FLOW_TYPES:
        return False
    try:
        value = (
            float(rule["value"])
            if rule.get("value") is not None
            else 0.10
        )
    except (TypeError, ValueError):
        value = 0.10
    try:
        mult = (
            float(rule["mult"])
            if rule.get("mult") is not None
            else 3.0
        )
    except (TypeError, ValueError):
        mult = 3.0

    net = flow.get("net_inflow")
    if net is None:
        return False
    try:
        net_f = float(net)
    except (TypeError, ValueError):
        return False

    if rtype == "flow_spike_in" and net_f <= 0:
        return False
    if rtype == "flow_spike_out" and net_f >= 0:
        return False

    rel = False
    avg = flow.get("avg_net_inflow")
    if avg is not None:
        try:
            avg_f = float(avg)
        except (TypeError, ValueError):
            avg_f = None
        if avg_f is not None and abs(avg_f) >= MEAN_ABS_FLOOR:
            rel = abs(net_f) >= mult * abs(avg_f)

    pct = False
    ratio = flow.get("ratio")
    if ratio is not None:
        try:
            pct = abs(float(ratio)) >= value
        except (TypeError, ValueError):
            pct = False

    return rel or pct


def cooldown_key(symbol: str, rule_id: str) -> str:
    return f"{symbol}:{rule_id}"


def llm_cooldown_key(symbol: str) -> str:
    return f"llm:{symbol}"


def _parse_ts(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw
    if isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def is_cooled_down(
    cooldowns: dict[str, Any] | None,
    key: str,
    now: datetime,
    cooldown_sec: int,
) -> bool:
    """True if enough time has passed since last alert (or never alerted)."""
    if cooldown_sec <= 0:
        return True
    cds = cooldowns or {}
    last = _parse_ts(cds.get(key))
    if last is None:
        return True
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() >= float(cooldown_sec)


def mark_cooldown(
    cooldowns: dict[str, Any] | None, key: str, now: datetime
) -> dict[str, Any]:
    out = dict(cooldowns or {})
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out[key] = now.isoformat()
    return out
