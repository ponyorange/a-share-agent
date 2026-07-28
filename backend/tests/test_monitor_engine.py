from __future__ import annotations

from datetime import datetime, timezone

from app.advisor.monitor import engine as engine_mod


def test_tick_sends_once_then_cooldown(monkeypatch):
    sent: list[tuple] = []
    job = {
        "id": "jid1",
        "user_id": "u1",
        "title": "跌破",
        "scope": "symbols",
        "symbols": ["510300"],
        "rules": [{"id": "r1", "type": "price_below", "value": 4.5}],
        "notify_email": "a@example.com",
        "cooldown_sec": 1800,
        "alert_cooldowns": {},
    }
    state = {"job": dict(job)}

    monkeypatch.setattr(
        engine_mod,
        "list_running_jobs",
        lambda: [dict(state["job"])],
    )
    monkeypatch.setattr(
        engine_mod,
        "resolve_symbols",
        lambda j: list(j.get("symbols") or []),
    )

    def touch(job_id, **fields):
        state["job"].update(fields)
        if "alert_cooldowns" in fields:
            state["job"]["alert_cooldowns"] = fields["alert_cooldowns"]

    monkeypatch.setattr(engine_mod, "touch_job_run", touch)
    monkeypatch.setattr(
        "app.quote.trading_session",
        lambda: {"is_trading": True},
    )
    monkeypatch.setattr(
        "app.quote.get_last_quote",
        lambda symbol: {
            "symbol": symbol,
            "name": "沪深300ETF",
            "price": 4.0,
            "day_chg_pct": -0.02,
        },
    )
    monkeypatch.setattr(
        engine_mod,
        "send_monitor_alert",
        lambda **kw: sent.append(kw),
    )

    s1 = engine_mod.run_monitor_tick()
    assert s1["alerts"] == 1
    assert len(sent) == 1
    assert state["job"]["alert_cooldowns"]

    s2 = engine_mod.run_monitor_tick()
    assert s2["alerts"] == 0
    assert len(sent) == 1


def test_tick_skips_when_not_trading(monkeypatch):
    monkeypatch.setattr(
        "app.quote.trading_session",
        lambda: {"is_trading": False},
    )
    called = []
    monkeypatch.setattr(
        engine_mod, "list_running_jobs", lambda: called.append(1) or []
    )
    stats = engine_mod.run_monitor_tick()
    assert stats == {"jobs": 0, "quotes": 0, "alerts": 0, "errors": 0}
    assert called == []
