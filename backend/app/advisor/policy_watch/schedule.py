"""Per-user scan window and interval helpers (Asia/Shanghai)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..calendar_util import is_trading_day
from .config import policy_watch_config

SH = ZoneInfo("Asia/Shanghai")


def _as_sh(now: datetime | None) -> datetime:
    current = now or datetime.now(SH)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc).astimezone(SH)
    return current.astimezone(SH)


def _hhmm(value: str) -> str:
    parts = str(value or "00:00").strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return f"{hour:02d}:{minute:02d}"


def _in_trading_session(now: datetime) -> bool:
    cfg = policy_watch_config()
    sh = _as_sh(now)
    if not is_trading_day(sh.date()):
        return False
    stamp = sh.strftime("%H:%M")
    start = _hhmm(str(cfg.get("trading_start") or "09:15"))
    end = _hhmm(str(cfg.get("trading_end") or "15:05"))
    return start <= stamp <= end


def clamp_interval(value: Any, *, kind: str) -> int:
    try:
        if isinstance(value, bool) or (isinstance(value, str) and not str(value).strip().lstrip("-").isdigit()):
            raise ValueError
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("间隔必须是整数") from exc
    cfg = policy_watch_config()
    if kind == "offhours":
        lo = int(cfg.get("interval_offhours_min") or 15)
        hi = int(cfg.get("interval_offhours_max") or 360)
    else:
        lo = int(cfg.get("interval_trading_min") or 5)
        hi = int(cfg.get("interval_trading_max") or 180)
    return max(lo, min(hi, number))


def in_user_scan_window(
    settings: dict[str, Any], *, now: datetime | None = None
) -> bool:
    current = now or datetime.now(timezone.utc)
    mode = str(settings.get("scan_mode") or "always")
    trading = _in_trading_session(current)
    if mode == "trading_only":
        return trading
    if mode == "offhours_only":
        return not trading
    return True


def current_interval_minutes(
    settings: dict[str, Any], *, now: datetime | None = None
) -> int:
    current = now or datetime.now(timezone.utc)
    cfg = policy_watch_config()
    if _in_trading_session(current):
        raw = settings.get("interval_trading_min")
        if raw is None:
            raw = cfg.get("default_interval_trading") or 15
        return clamp_interval(raw, kind="trading")
    raw = settings.get("interval_offhours_min")
    if raw is None:
        raw = cfg.get("default_interval_offhours") or 60
    return clamp_interval(raw, kind="offhours")


def _as_aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def user_interval_elapsed(
    settings: dict[str, Any], *, now: datetime | None = None
) -> bool:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    last = _as_aware(settings.get("last_fanout_at"))
    if last is None:
        return True
    minutes = current_interval_minutes(settings, now=current)
    return (current - last).total_seconds() >= float(minutes) * 60.0
