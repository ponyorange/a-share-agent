"""Schedule helpers for monitor jobs (Asia/Shanghai)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..calendar_util import is_trading_day

SH = ZoneInfo("Asia/Shanghai")
DEFAULT_WATCH_START = "09:15"
DEFAULT_WATCH_END = "15:05"


def _as_sh(now: datetime | None) -> datetime:
    current = now or datetime.now(SH)
    if current.tzinfo is None:
        return current.replace(tzinfo=SH)
    return current.astimezone(SH)


def shanghai_hhmm_on(date_str: str, hhmm: str) -> datetime:
    """Return timezone-aware datetime for date + HH:MM in Shanghai."""
    d = date.fromisoformat(str(date_str)[:10])
    parts = str(hhmm or "00:00").strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=SH)


def compute_watch_end_at(anchor_date: str, end_time: str = DEFAULT_WATCH_END) -> datetime:
    return shanghai_hhmm_on(anchor_date, end_time or DEFAULT_WATCH_END)


def _watch_start_hhmm(job: dict[str, Any]) -> str:
    return str(job.get("run_time") or DEFAULT_WATCH_START)


def _watch_end_hhmm(job: dict[str, Any]) -> str:
    return str(job.get("end_time") or DEFAULT_WATCH_END)


def _day_allowed(d: date, calendar: str) -> bool:
    if calendar == "everyday":
        return True
    return bool(is_trading_day(d))


def _next_allowed_date(start: date, calendar: str, *, inclusive: bool) -> date | None:
    d = start if inclusive else start + timedelta(days=1)
    for _ in range(40):
        if _day_allowed(d, calendar):
            return d
        d += timedelta(days=1)
    return None


def compute_next_run_at(job: dict[str, Any], *, now: datetime | None = None) -> datetime | None:
    """Next activation/fire time in Shanghai tz (stored as aware datetime)."""
    kind = str(job.get("kind") or "watch")
    repeat = str(job.get("repeat") or "recurring")
    calendar = str(job.get("calendar") or "trading_days")
    sh_now = _as_sh(now)

    if kind == "run_at":
        hhmm = str(job.get("run_time") or "09:00")
        if repeat == "once":
            anchor = str(job.get("anchor_date") or "")[:10]
            if not anchor:
                return None
            target = shanghai_hhmm_on(anchor, hhmm)
            return target if target > sh_now else None
        # recurring: next matching day at hhmm strictly after now
        start_day = sh_now.date()
        for offset in range(0, 40):
            d = start_day + timedelta(days=offset)
            if not _day_allowed(d, calendar):
                continue
            cand = shanghai_hhmm_on(d.isoformat(), hhmm)
            if cand > sh_now:
                return cand
        return None

    # watch
    start_hhmm = _watch_start_hhmm(job)
    if repeat == "once":
        anchor = str(job.get("anchor_date") or "")[:10]
        if not anchor:
            return None
        start = shanghai_hhmm_on(anchor, start_hhmm)
        end = compute_watch_end_at(anchor, _watch_end_hhmm(job))
        if sh_now > end:
            return None
        return start
    # recurring watch: next session open
    d0 = sh_now.date()
    # if before today's open on an allowed day → today open
    if _day_allowed(d0, calendar):
        today_open = shanghai_hhmm_on(d0.isoformat(), start_hhmm)
        today_end = shanghai_hhmm_on(d0.isoformat(), _watch_end_hhmm(job))
        if sh_now < today_open:
            return today_open
        if sh_now <= today_end:
            # already in/near session; next_run for "scheduled" after close is tomorrow
            pass
        else:
            pass
    nxt = _next_allowed_date(d0, calendar, inclusive=False)
    if nxt is None:
        return None
    return shanghai_hhmm_on(nxt.isoformat(), start_hhmm)


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_shanghai(dt: datetime | None) -> str:
    """Human-readable Asia/Shanghai time for logs / UI copy."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt
    return aware.astimezone(SH).strftime("%Y-%m-%d %H:%M:%S")
