def test_cockpit_stopped_without_session(monkeypatch):
    import app.advisor.paper_trader.cockpit as cp

    monkeypatch.setattr(cp, "get_session", lambda uid: None)
    monkeypatch.setattr(
        cp,
        "get_account",
        lambda uid, **k: {
            "cash": 100000,
            "equity": 100000,
            "market_value": 0,
            "positions": [],
        },
    )
    monkeypatch.setattr(cp, "build_candidates", lambda uid, limit=None: [])
    monkeypatch.setattr(
        cp,
        "list_decisions",
        lambda uid, **k: {"page": 1, "page_size": 20, "total": 0, "items": []},
    )
    monkeypatch.setattr(
        cp,
        "trading_session",
        lambda: {"is_trading": False, "is_trading_day": True},
    )
    out = cp.build_cockpit("u1")
    assert out["session"]["status"] == "stopped"
    assert "candidates" in out and "decisions" in out
    assert "meta" in out


def test_cockpit_candidate_error_isolated(monkeypatch):
    import app.advisor.paper_trader.cockpit as cp

    monkeypatch.setattr(
        cp, "get_session", lambda uid: {"status": "running", "id": "s1"}
    )
    monkeypatch.setattr(
        cp,
        "get_account",
        lambda uid, **k: {
            "cash": 1,
            "equity": 1,
            "market_value": 0,
            "positions": [],
        },
    )

    def boom(*a, **k):
        raise RuntimeError("candidates down")

    monkeypatch.setattr(cp, "build_candidates", boom)
    monkeypatch.setattr(
        cp,
        "list_decisions",
        lambda uid, **k: {"page": 1, "page_size": 20, "total": 0, "items": []},
    )
    monkeypatch.setattr(
        cp,
        "trading_session",
        lambda: {"is_trading": True, "is_trading_day": True},
    )
    out = cp.build_cockpit("u1")
    assert out["session"]["status"] == "running"
    assert out["candidates"] == []
    assert "candidates" in (out.get("errors") or {})
