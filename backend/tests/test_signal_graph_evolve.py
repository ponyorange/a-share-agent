from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import app.advisor.signal_graph.evolve as ev

SH = ZoneInfo("Asia/Shanghai")


def test_completed_trade_date_skips_while_trading():
    now = datetime(2026, 8, 13, 10, 30, tzinfo=SH)
    day = ev.completed_trade_date(
        now, session={"is_trading": True, "is_trading_day": True}
    )
    assert day is None


def test_completed_trade_date_after_close_is_today():
    now = datetime(2026, 8, 13, 15, 10, tzinfo=SH)
    day = ev.completed_trade_date(
        now, session={"is_trading": False, "is_trading_day": True}
    )
    assert day == "2026-08-13"


def test_completed_trade_date_before_open_is_previous_session(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.calendar_util.last_trading_day",
        lambda on_or_before=None: on_or_before.isoformat(),
    )
    now = datetime(2026, 8, 13, 8, 0, tzinfo=SH)
    day = ev.completed_trade_date(
        now, session={"is_trading": False, "is_trading_day": True}
    )
    assert day == "2026-08-12"


def test_run_daily_evolve_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(
        ev,
        "signal_graph_config",
        lambda: {"enabled": True, "auto_evolve": False, "owner": "default"},
    )
    out = ev.run_daily_evolve(now=datetime(2026, 8, 13, 16, 0, tzinfo=SH))
    assert out["skipped"] == "disabled"


def test_run_daily_evolve_skips_when_open(monkeypatch):
    monkeypatch.setattr(
        ev,
        "signal_graph_config",
        lambda: {
            "enabled": True,
            "auto_evolve": True,
            "owner": "default",
            "evolve_settle_limit": 200,
            "evolve_generate": True,
            "evolve_generate_limit": 40,
        },
    )
    monkeypatch.setattr(
        ev,
        "trading_session",
        lambda now=None: {"is_trading": True, "is_trading_day": True},
    )
    out = ev.run_daily_evolve(now=datetime(2026, 8, 13, 10, 0, tzinfo=SH))
    assert out["skipped"] == "trading"


def test_run_daily_evolve_skips_when_already_done(monkeypatch):
    monkeypatch.setattr(
        ev,
        "signal_graph_config",
        lambda: {
            "enabled": True,
            "auto_evolve": True,
            "owner": "default",
            "evolve_settle_limit": 200,
            "evolve_generate": True,
            "evolve_generate_limit": 40,
        },
    )
    monkeypatch.setattr(
        ev,
        "trading_session",
        lambda now=None: {"is_trading": False, "is_trading_day": True},
    )
    monkeypatch.setattr(ev, "_last_evolve_date", lambda owner: "2026-08-13")
    called = []
    monkeypatch.setattr(ev, "settle_due", lambda **kw: called.append("settle"))
    out = ev.run_daily_evolve(now=datetime(2026, 8, 13, 16, 0, tzinfo=SH))
    assert out["skipped"] == "already"
    assert called == []


def test_run_daily_evolve_settles_then_generates(monkeypatch):
    monkeypatch.setattr(
        ev,
        "signal_graph_config",
        lambda: {
            "enabled": True,
            "auto_evolve": True,
            "owner": "default",
            "evolve_settle_limit": 200,
            "evolve_generate": True,
            "evolve_generate_limit": 40,
        },
    )
    monkeypatch.setattr(
        ev,
        "trading_session",
        lambda now=None: {"is_trading": False, "is_trading_day": True},
    )
    monkeypatch.setattr(ev, "_last_evolve_date", lambda owner: None)
    steps: list[str] = []
    monkeypatch.setattr(
        ev,
        "settle_due",
        lambda **kw: steps.append("settle")
        or {
            "settled": [{"prediction_id": "p1"}],
            "unresolved": [],
            "skipped": [],
        },
    )
    monkeypatch.setattr(
        ev, "collect_evolve_symbols", lambda trade_date, limit: ["600519", "000001"]
    )
    monkeypatch.setattr(
        ev,
        "generate_signals_batch",
        lambda symbols, **kw: steps.append("generate")
        or {"count": len(symbols), "errors": [], "items": symbols},
    )
    marked: list[str] = []
    monkeypatch.setattr(
        ev, "_mark_evolved", lambda owner, day: marked.append(f"{owner}:{day}")
    )

    out = ev.run_daily_evolve(now=datetime(2026, 8, 13, 16, 0, tzinfo=SH))
    assert steps == ["settle", "generate"]
    assert out["ok"] is True
    assert out["trade_date"] == "2026-08-13"
    assert out["settled_count"] == 1
    assert out["generated_count"] == 2
    assert marked == ["default:2026-08-13"]


def test_run_daily_evolve_retries_if_generate_fails(monkeypatch):
    monkeypatch.setattr(
        ev,
        "signal_graph_config",
        lambda: {
            "enabled": True,
            "auto_evolve": True,
            "owner": "default",
            "evolve_settle_limit": 200,
            "evolve_generate": True,
            "evolve_generate_limit": 40,
        },
    )
    monkeypatch.setattr(
        ev,
        "trading_session",
        lambda now=None: {"is_trading": False, "is_trading_day": True},
    )
    monkeypatch.setattr(ev, "_last_evolve_date", lambda owner: None)
    monkeypatch.setattr(
        ev, "settle_due", lambda **kw: {"settled": [], "unresolved": [], "skipped": []}
    )
    monkeypatch.setattr(ev, "collect_evolve_symbols", lambda *a, **k: ["600519"])

    def boom(*_a, **_k):
        raise RuntimeError("kline down")

    monkeypatch.setattr(ev, "generate_signals_batch", boom)
    marked: list[str] = []
    monkeypatch.setattr(ev, "_mark_evolved", lambda *a, **k: marked.append("x"))

    out = ev.run_daily_evolve(now=datetime(2026, 8, 13, 16, 0, tzinfo=SH))
    assert out["ok"] is False
    assert "kline down" in out["error"]
    assert marked == []


def test_monitor_tick_records_evolve(monkeypatch):
    from app.advisor.monitor import engine as engine_mod

    monkeypatch.setattr(
        engine_mod, "activate_due_jobs", lambda now=None: {"activated": 0, "run_at": 0}
    )
    monkeypatch.setattr(
        engine_mod, "finalize_watch_windows", lambda now=None: {"finalized": 0}
    )
    monkeypatch.setattr(
        "app.quote.trading_session",
        lambda now=None: {"is_trading": False, "is_trading_day": True},
    )
    monkeypatch.setattr(
        "app.advisor.signal_graph.evolve.run_daily_evolve",
        lambda **_kw: {
            "ok": True,
            "trade_date": "2026-08-13",
            "settled_count": 2,
            "generated_count": 4,
        },
    )
    stats = engine_mod.run_monitor_tick()
    assert stats["signal_graph_evolve"] == 1
