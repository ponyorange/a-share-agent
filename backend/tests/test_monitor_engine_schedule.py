from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.advisor.monitor import engine as engine_mod

SH = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def _skip_signal_graph_evolve(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.signal_graph.evolve.run_daily_evolve",
        lambda **_kw: {"ok": True, "skipped": "test"},
    )

SH = ZoneInfo("Asia/Shanghai")


def test_activate_watch_at_open(monkeypatch):
    touches: list[dict] = []
    logs: list[tuple] = []
    now = datetime(2026, 7, 30, 9, 20, tzinfo=SH).astimezone(timezone.utc)
    job = {
        "id": "w1",
        "user_id": "u1",
        "kind": "watch",
        "repeat": "once",
        "title": "明天盯盘",
        "status": "scheduled",
        "next_run_at": datetime(2026, 7, 30, 9, 15, tzinfo=SH).astimezone(timezone.utc),
        "end_at": datetime(2026, 7, 30, 15, 5, tzinfo=SH).astimezone(timezone.utc),
    }

    monkeypatch.setattr(engine_mod, "list_due_scheduled_jobs", lambda _n: [job])
    monkeypatch.setattr(
        engine_mod,
        "touch_job_run",
        lambda jid, **fields: touches.append({"id": jid, **fields}),
    )
    monkeypatch.setattr(
        engine_mod,
        "append_job_log",
        lambda uid, jid, **kw: logs.append((uid, jid, kw.get("event"))),
    )

    stats = engine_mod.activate_due_jobs(now=now)
    assert stats["activated"] == 1
    assert touches[0]["status"] == "running"
    assert ("u1", "w1", "activated") in logs


def test_finalize_once_watch_after_end(monkeypatch):
    touches: list[dict] = []
    logs: list[tuple] = []
    now = datetime(2026, 7, 30, 15, 10, tzinfo=SH).astimezone(timezone.utc)
    job = {
        "id": "w2",
        "user_id": "u1",
        "kind": "watch",
        "repeat": "once",
        "status": "running",
        "anchor_date": "2026-07-30",
        "end_time": "15:05",
        "end_at": datetime(2026, 7, 30, 15, 5, tzinfo=SH).astimezone(timezone.utc),
    }
    monkeypatch.setattr(engine_mod, "list_running_jobs", lambda: [job])
    monkeypatch.setattr(
        engine_mod,
        "touch_job_run",
        lambda jid, **fields: touches.append({"id": jid, **fields}),
    )
    monkeypatch.setattr(
        engine_mod,
        "append_job_log",
        lambda uid, jid, **kw: logs.append((uid, jid, kw.get("event"))),
    )

    stats = engine_mod.finalize_watch_windows(now=now)
    assert stats["finalized"] == 1
    assert touches[0]["status"] == "completed"
    assert ("u1", "w2", "completed") in logs


def test_activate_recurring_after_close_reschedules_not_running(monkeypatch):
    """周五盘后补跑时，不应先 activate 再 finalize 把整天跳过。"""
    touches: list[dict] = []
    logs: list[dict] = []
    now = datetime(2026, 7, 31, 22, 4, tzinfo=SH).astimezone(timezone.utc)
    job = {
        "id": "w-fri",
        "user_id": "u1",
        "kind": "watch",
        "repeat": "recurring",
        "calendar": "trading_days",
        "run_time": "09:15",
        "end_time": "15:05",
        "title": "收藏盯盘",
        "status": "scheduled",
        "next_run_at": datetime(2026, 7, 31, 9, 15, tzinfo=SH).astimezone(
            timezone.utc
        ),
    }

    monkeypatch.setattr(engine_mod, "list_due_scheduled_jobs", lambda _n: [job])
    monkeypatch.setattr(
        engine_mod,
        "touch_job_run",
        lambda jid, **fields: touches.append({"id": jid, **fields}),
    )
    monkeypatch.setattr(
        engine_mod,
        "append_job_log",
        lambda uid, jid, **kw: logs.append(kw),
    )
    monkeypatch.setattr(engine_mod, "is_trading_day", lambda d: d.weekday() < 5)

    stats = engine_mod.activate_due_jobs(now=now)
    assert stats.get("activated", 0) == 0
    assert stats.get("missed", 0) == 1
    assert touches[0]["status"] == "scheduled"
    assert touches[0]["next_run_at"] is not None
    nxt = touches[0]["next_run_at"]
    assert nxt.astimezone(SH).strftime("%Y-%m-%d %H:%M") == "2026-08-03 09:15"
    assert logs[0]["event"] == "missed"
    assert "错过" in (logs[0].get("message") or "")


def test_activate_recurring_in_session_still_runs(monkeypatch):
    touches: list[dict] = []
    now = datetime(2026, 7, 31, 10, 30, tzinfo=SH).astimezone(timezone.utc)
    job = {
        "id": "w-mid",
        "user_id": "u1",
        "kind": "watch",
        "repeat": "recurring",
        "calendar": "trading_days",
        "run_time": "09:15",
        "end_time": "15:05",
        "status": "scheduled",
        "next_run_at": datetime(2026, 7, 31, 9, 15, tzinfo=SH).astimezone(
            timezone.utc
        ),
    }
    monkeypatch.setattr(engine_mod, "list_due_scheduled_jobs", lambda _n: [job])
    monkeypatch.setattr(
        engine_mod,
        "touch_job_run",
        lambda jid, **fields: touches.append({"id": jid, **fields}),
    )
    monkeypatch.setattr(engine_mod, "append_job_log", lambda *a, **k: None)
    monkeypatch.setattr(engine_mod, "is_trading_day", lambda d: True)

    stats = engine_mod.activate_due_jobs(now=now)
    assert stats["activated"] == 1
    assert touches[0]["status"] == "running"


def test_tick_activates_when_not_trading(monkeypatch):
    called = {"activate": 0, "finalize": 0, "eval": 0}
    monkeypatch.setattr(
        "app.quote.trading_session",
        lambda: {"is_trading": False},
    )
    monkeypatch.setattr(
        engine_mod,
        "activate_due_jobs",
        lambda now=None: called.__setitem__("activate", called["activate"] + 1)
        or {"activated": 0, "run_at": 0},
    )
    monkeypatch.setattr(
        engine_mod,
        "finalize_watch_windows",
        lambda now=None: called.__setitem__("finalize", called["finalize"] + 1)
        or {"finalized": 0},
    )
    monkeypatch.setattr(
        engine_mod,
        "_evaluate_running_watches",
        lambda **kw: called.__setitem__("eval", called["eval"] + 1),
    )
    stats = engine_mod.run_monitor_tick()
    assert called["activate"] == 1
    assert called["finalize"] == 1
    assert called["eval"] == 0
    assert stats["jobs"] == 0
