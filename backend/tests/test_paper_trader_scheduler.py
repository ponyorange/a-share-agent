from datetime import datetime
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")


def test_run_due_invokes_cycle(monkeypatch):
    import app.advisor.paper_trader.scheduler as sch

    monkeypatch.setattr(
        sch,
        "list_due_sessions",
        lambda now, limit: [{"user_id": "u1", "status": "running", "id": "s1"}],
    )
    calls = []
    monkeypatch.setattr(
        sch,
        "run_paper_trader_cycle",
        lambda s, now=None: calls.append(s) or {"halted": False},
    )
    monkeypatch.setattr(sch, "trading_is_open", lambda now=None: True)
    stats = sch.run_due_paper_traders()
    assert stats["ran"] == 1 and len(calls) == 1


def test_run_due_skips_when_closed(monkeypatch):
    import app.advisor.paper_trader.scheduler as sch

    monkeypatch.setattr(sch, "trading_is_open", lambda now=None: False)
    called = []
    monkeypatch.setattr(
        sch, "list_due_sessions", lambda *a, **k: called.append(1) or []
    )
    stats = sch.run_due_paper_traders()
    assert stats["ran"] == 0 and called == []


def _closed_trading_day(_now=None):
    return {"is_trading": False, "is_trading_day": True}


def test_day_end_skips_lunch_break(monkeypatch):
    import app.advisor.paper_trader.scheduler as sch

    queried = []

    class _Fake:
        paper_trader_sessions = type(
            "C",
            (),
            {"find": staticmethod(lambda *a, **k: queried.append(1) or [])},
        )()

    monkeypatch.setattr(sch, "trading_session", _closed_trading_day)
    monkeypatch.setattr(sch, "get_db", lambda: _Fake())
    now = datetime(2026, 8, 13, 11, 31, tzinfo=SH)
    assert sch.finalize_paper_trader_day_ends(now=now) == 0
    assert queried == []


def test_day_end_sends_after_close(monkeypatch):
    import app.advisor.paper_trader.scheduler as sch

    docs = [
        {
            "user_id": "u1",
            "notify_email": "a@b.c",
            "status": "running",
            "day_anchor": "2026-08-13",
            "stats_today": {"rounds": 2, "trades": 1, "buys": 1, "sells": 0, "blocked": 0},
            "equity_day_open": 100000,
        }
    ]

    class _Fake:
        paper_trader_sessions = type(
            "C", (), {"find": staticmethod(lambda *a, **k: docs)}
        )()

    sent: list[dict] = []
    monkeypatch.setattr(sch, "trading_session", _closed_trading_day)
    monkeypatch.setattr(sch, "get_db", lambda: _Fake())
    monkeypatch.setattr(
        sch, "send_day_end_email", lambda session, summary: sent.append(summary)
    )
    monkeypatch.setattr(sch, "touch_session", lambda *a, **k: None)
    now = datetime(2026, 8, 13, 15, 10, tzinfo=SH)
    assert sch.finalize_paper_trader_day_ends(now=now) == 1
    assert sent and sent[0]["day"] == "2026-08-13"
