from __future__ import annotations

RISK = {
    "max_single_position": 0.25,
    "max_total_exposure": 0.90,
    "max_positions": 10,
    "max_trades_per_day": 30,
    "max_daily_loss_pct": 0.05,
    "lot_size": 100,
    "block_limit_board": True,
}


def test_cycle_places_order_and_logs(monkeypatch):
    import app.advisor.paper_trader.cycle as cycle

    session = {
        "id": "s1",
        "user_id": "u1",
        "status": "running",
        "mode": "signal_first",
        "interval_sec": 600,
        "risk": RISK,
        "stats_today": {
            "trades": 0,
            "buys": 0,
            "sells": 0,
            "blocked": 0,
            "llm_calls": 0,
            "rounds": 0,
        },
        "equity_day_open": 100_000,
        "day_anchor": "2099-01-01",
        "consecutive_zero_fill": 0,
        "consecutive_llm_fail": 0,
    }
    monkeypatch.setattr(
        cycle,
        "trading_session",
        lambda now=None: {"is_trading": True},
    )
    monkeypatch.setattr(
        cycle,
        "build_candidates",
        lambda uid, limit=None: [
            {"symbol": "600000", "direction": "buy", "held_qty": 0, "name": "测"}
        ],
    )
    monkeypatch.setattr(
        cycle,
        "_quotes_for",
        lambda syms: {"600000": {"price": 10.0, "name": "测", "day_chg_pct": 0.01}},
    )
    monkeypatch.setattr(
        cycle,
        "run_llm_decide",
        lambda *a, **kw: {
            "actions": [
                {"symbol": "600000", "side": "buy", "qty": 100, "reason": "ok"}
            ],
            "raw": "{}",
        },
    )
    placed = []

    def fake_place(uid, body, **kw):
        assert kw.get("source") == "paper_trader"
        placed.append(body)
        return {
            "trade": {
                "_id": "t1",
                "symbol": body.symbol,
                "side": body.side,
                "qty": body.qty,
                "price": 10.0,
            },
            "account": {"cash": 99000},
        }

    monkeypatch.setattr(cycle, "place_order", fake_place)
    monkeypatch.setattr(
        cycle,
        "get_account",
        lambda uid, **k: {"cash": 100000, "equity": 100000, "positions": []},
    )
    saved: dict = {"touch": {}}

    def fake_insert(doc):
        saved["d"] = {**doc, "id": "d1"}
        return saved["d"]

    def fake_touch(uid, **f):
        saved["touch"].update(f)
        return f

    monkeypatch.setattr(cycle, "insert_decision", fake_insert)
    monkeypatch.setattr(cycle, "touch_session", fake_touch)
    monkeypatch.setattr(cycle, "_ensure_day_fields", lambda *a, **k: {})

    out = cycle.run_paper_trader_cycle(session)
    assert placed and placed[0].qty == 100
    assert saved["d"]["orders_placed"]
    assert saved["touch"]["next_run_at"] is not None
    assert out["halted"] is False


def test_cycle_skips_outside_trading(monkeypatch):
    import app.advisor.paper_trader.cycle as cycle

    session = {
        "id": "s1",
        "user_id": "u1",
        "status": "running",
        "mode": "signal_first",
        "interval_sec": 600,
        "risk": RISK,
    }
    monkeypatch.setattr(
        cycle, "trading_session", lambda now=None: {"is_trading": False}
    )
    placed = []
    monkeypatch.setattr(
        cycle, "place_order", lambda *a, **k: placed.append(1)
    )
    monkeypatch.setattr(
        cycle,
        "insert_decision",
        lambda doc: {**doc, "id": "d1"},
    )
    monkeypatch.setattr(cycle, "touch_session", lambda uid, **f: f)
    out = cycle.run_paper_trader_cycle(session)
    assert out["skip_reason"] == "not_trading"
    assert placed == []


def test_halt_on_daily_loss(monkeypatch):
    import app.advisor.paper_trader.cycle as cycle

    session = {
        "id": "s1",
        "user_id": "u1",
        "status": "running",
        "mode": "signal_first",
        "interval_sec": 600,
        "risk": RISK,
        "equity_day_open": 100_000,
        "day_anchor": "2099-01-01",
        "stats_today": {},
    }
    monkeypatch.setattr(
        cycle, "trading_session", lambda now=None: {"is_trading": True}
    )
    monkeypatch.setattr(
        cycle,
        "get_account",
        lambda uid, **k: {"cash": 94000, "equity": 94000, "positions": []},
    )
    monkeypatch.setattr(cycle, "_ensure_day_fields", lambda *a, **k: {})
    touches = []
    monkeypatch.setattr(
        cycle, "touch_session", lambda uid, **f: touches.append(f) or f
    )
    monkeypatch.setattr(
        cycle, "insert_decision", lambda doc: {**doc, "id": "d1"}
    )
    out = cycle.run_paper_trader_cycle(session)
    assert out["halted"] is True
    assert any(t.get("status") == "halted" for t in touches)
