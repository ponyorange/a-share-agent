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
    assert stats["jobs"] == 0
    assert stats["quotes"] == 0
    assert stats["alerts"] == 0
    assert stats["errors"] == 0
    assert called == []


def test_tick_flow_rule_sends(monkeypatch):
    sent: list[dict] = []
    job = {
        "id": "jid2",
        "user_id": "u1",
        "title": "资金",
        "scope": "symbols",
        "symbols": ["600519"],
        "rules": [{"id": "f1", "type": "flow_spike_in", "value": 0.10, "mult": 3}],
        "notify_email": "a@example.com",
        "cooldown_sec": 1800,
        "alert_cooldowns": {},
        "llm_enabled": False,
    }
    monkeypatch.setattr(engine_mod, "list_running_jobs", lambda: [job])
    monkeypatch.setattr(
        engine_mod, "resolve_symbols", lambda j: list(j.get("symbols") or [])
    )
    monkeypatch.setattr(engine_mod, "touch_job_run", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.quote.trading_session", lambda: {"is_trading": True}
    )
    monkeypatch.setattr(
        "app.quote.get_last_quote",
        lambda symbol: {
            "symbol": symbol,
            "name": "茅台",
            "price": 1600,
            "day_chg_pct": 0.01,
        },
    )
    monkeypatch.setattr(
        engine_mod,
        "get_flow_snapshot",
        lambda symbol, window_days=5: {
            "ok": True,
            "net_inflow": 5e7,
            "avg_net_inflow": 1e7,
            "ratio": 0.12,
        },
    )
    monkeypatch.setattr(
        engine_mod, "send_monitor_alert", lambda **kw: sent.append(kw)
    )
    stats = engine_mod.run_monitor_tick()
    assert stats["alerts"] == 1
    assert sent and sent[0].get("flow")


def test_tick_llm_channel(monkeypatch):
    touches: list[dict] = []
    job = {
        "id": "jid3",
        "user_id": "u1",
        "title": "看盘",
        "scope": "symbols",
        "symbols": ["510300"],
        "rules": [{"id": "r1", "type": "price_below", "value": 0.01}],
        "notify_email": "a@example.com",
        "cooldown_sec": 1800,
        "alert_cooldowns": {},
        "llm_enabled": True,
        "llm_interval_sec": 900,
        "llm_anomaly_abs_chg": 0.03,
        "llm_symbol_baselines": {},
        "last_llm_at": None,
    }
    monkeypatch.setattr(engine_mod, "list_running_jobs", lambda: [job])
    monkeypatch.setattr(
        engine_mod, "resolve_symbols", lambda j: list(j.get("symbols") or [])
    )
    monkeypatch.setattr(
        engine_mod,
        "touch_job_run",
        lambda job_id, **fields: touches.append(fields),
    )
    monkeypatch.setattr(
        "app.quote.trading_session", lambda: {"is_trading": True}
    )
    monkeypatch.setattr(
        "app.quote.get_last_quote",
        lambda symbol: {
            "symbol": symbol,
            "name": "ETF",
            "price": 4.0,
            "day_chg_pct": 0.01,
        },
    )
    monkeypatch.setattr(
        engine_mod, "send_monitor_alert", lambda **kw: None
    )
    monkeypatch.setattr(
        engine_mod,
        "should_run_llm_watch",
        lambda job, quotes, now: (True, ["510300"]),
    )
    monkeypatch.setattr(
        engine_mod,
        "run_llm_watch",
        lambda *a, **k: {
            "ok": True,
            "notified": 1,
            "error": None,
            "alert_cooldowns": {},
        },
    )
    stats = engine_mod.run_monitor_tick()
    assert stats["llm_runs"] == 1
    assert stats["llm_notified"] == 1
    assert any(t.get("last_llm_at") for t in touches)
