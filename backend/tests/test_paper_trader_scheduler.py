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
