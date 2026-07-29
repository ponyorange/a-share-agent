from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.advisor.monitor import engine as engine_mod

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
