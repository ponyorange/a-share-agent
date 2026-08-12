from app.advisor.paper_trader.candidates import build_candidates


def test_union_and_direction(monkeypatch):
    import app.advisor.paper_trader.candidates as c

    monkeypatch.setattr(
        c,
        "_recommendation_rows",
        lambda uid, allow_live=False: [
            {"symbol": "600000", "score": 0.7, "name": "A"},
            {"symbol": "600001", "score": 0.5, "name": "B"},
        ],
    )
    monkeypatch.setattr(c, "_watchlist_symbols", lambda uid: ["600001", "600002"])
    monkeypatch.setattr(
        c,
        "_paper_positions",
        lambda uid: [{"symbol": "600003", "qty": 100, "name": "持仓票"}],
    )
    monkeypatch.setattr(c, "_rule_score", lambda sym: 0.2)
    monkeypatch.setattr(
        c,
        "_graph_action",
        lambda sym: "SELL" if sym == "600002" else "HOLD",
    )
    monkeypatch.setattr(c, "_buy_sell_thresholds", lambda: (0.55, 0.35))

    rows = build_candidates("u1", limit=40)
    by = {r["symbol"]: r for r in rows}
    assert set(by) >= {"600000", "600001", "600002", "600003"}
    assert by["600000"]["direction"] == "buy"
    assert by["600002"]["direction"] == "sell"
    assert by["600003"]["held_qty"] == 100


def test_light_skips_live_enrich(monkeypatch):
    import app.advisor.paper_trader.candidates as c

    monkeypatch.setattr(
        c,
        "_recommendation_rows",
        lambda uid, allow_live=False: (
            []
            if allow_live
            else [{"symbol": "600000", "score": 0.8, "name": "快"}]
        ),
    )
    monkeypatch.setattr(c, "_watchlist_symbols", lambda uid: [])
    monkeypatch.setattr(c, "_paper_positions", lambda uid: [])
    called = {"advice": 0}

    def boom(sym):
        called["advice"] += 1
        raise AssertionError("light must not call advice")

    monkeypatch.setattr(c, "_rule_score", boom)
    monkeypatch.setattr(c, "_graph_action", boom)
    monkeypatch.setattr(c, "_buy_sell_thresholds", lambda: (0.55, 0.35))

    rows = build_candidates("u1", light=True)
    assert len(rows) == 1
    assert rows[0]["direction"] == "buy"
    assert called["advice"] == 0
