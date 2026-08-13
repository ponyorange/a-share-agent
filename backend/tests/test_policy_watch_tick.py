from app.advisor.policy_watch import tick as tick_mod


def test_tick_order_and_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(tick_mod, "policy_watch_config", lambda: {
        "max_tick_seconds": 8,
        "max_fetch_per_tick": 5,
    })
    monkeypatch.setattr(
        tick_mod,
        "collect_due_source_keys",
        lambda now=None: calls.append("collect") or [
            {"source_key": "gov_zhengce", "seeding": False}
        ],
    )
    monkeypatch.setattr(
        tick_mod,
        "ingest_source",
        lambda spec, now=None: calls.append("ingest") or {"new_articles": 1, "seeded": 0, "error": None},
    )
    monkeypatch.setattr(
        tick_mod,
        "interpret_pending",
        lambda limit=None: calls.append("interpret") or {"ok": 1, "failed": 0},
    )
    monkeypatch.setattr(
        tick_mod,
        "fanout_due_users",
        lambda now=None: calls.append("fanout") or {"items": 1, "emailed": 1},
    )
    out = tick_mod.run_policy_watch_tick()
    assert calls == ["collect", "ingest", "interpret", "fanout"]
    assert out["emailed"] == 1


def test_engine_swallows_policy_watch(monkeypatch):
    from app.advisor.monitor import engine as engine_mod

    monkeypatch.setattr(engine_mod, "activate_due_jobs", lambda now=None: {})
    monkeypatch.setattr(engine_mod, "finalize_watch_windows", lambda now=None: {})
    monkeypatch.setattr(
        "app.quote.trading_session", lambda: {"is_trading": False}
    )
    monkeypatch.setattr(
        "app.advisor.paper_trader.scheduler.run_due_paper_traders",
        lambda now=None: {},
    )
    monkeypatch.setattr(
        "app.advisor.paper_trader.scheduler.finalize_paper_trader_day_ends",
        lambda now=None: 0,
    )
    monkeypatch.setattr(
        "app.advisor.signal_graph.evolve.run_daily_evolve",
        lambda now=None: {"ok": True, "skipped": "test"},
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.run_policy_watch_tick",
        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    stats = engine_mod.run_monitor_tick()
    assert stats["errors"] >= 1
    assert stats.get("policy_watch", {}).get("errors") == 1
