# backend/tests/test_monitor_schedule.py
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.advisor.monitor import schedule as sch

SH = ZoneInfo("Asia/Shanghai")


def test_once_watch_next_trading_morning(monkeypatch):
    monkeypatch.setattr(sch, "is_trading_day", lambda d: True)
    now = datetime(2026, 7, 28, 20, 0, tzinfo=SH)
    job = {
        "kind": "watch",
        "repeat": "once",
        "calendar": "trading_days",
        "anchor_date": "2026-07-29",
        "run_time": None,
        "end_time": "15:05",
        "status": "scheduled",
    }
    nxt = sch.compute_next_run_at(job, now=now)
    assert nxt is not None
    assert nxt.astimezone(SH).strftime("%Y-%m-%d %H:%M") == "2026-07-29 09:15"


def test_run_at_recurring_everyday_after_fire(monkeypatch):
    monkeypatch.setattr(sch, "is_trading_day", lambda d: True)
    now = datetime(2026, 7, 29, 9, 5, tzinfo=SH)
    job = {
        "kind": "run_at",
        "repeat": "recurring",
        "calendar": "everyday",
        "run_time": "09:00",
        "status": "scheduled",
    }
    nxt = sch.compute_next_run_at(job, now=now)
    assert nxt is not None
    assert nxt.astimezone(SH).strftime("%Y-%m-%d %H:%M") == "2026-07-30 09:00"


def test_compute_watch_end_at():
    end = sch.compute_watch_end_at("2026-07-29", "15:05")
    assert end.astimezone(SH).strftime("%Y-%m-%d %H:%M") == "2026-07-29 15:05"
