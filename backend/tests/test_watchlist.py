# backend/tests/test_watchlist.py
from __future__ import annotations

import pytest

from app.advisor import watchlist as wl


class _FakeColl:
    def __init__(self):
        self.doc = None

    def find_one(self, q):
        if self.doc and self.doc.get("user_id") == q.get("user_id"):
            return self.doc
        return None

    def update_one(self, q, update, upsert=False):
        body = dict(self.doc or {"user_id": q["user_id"], "items": []})
        body.update(update.get("$set") or {})
        self.doc = body


class _FakeDB:
    def __init__(self):
        self.watchlists = _FakeColl()


def test_add_remove_idempotent(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(wl, "get_db", lambda: db)
    monkeypatch.setattr(wl, "_lookup_name", lambda symbol: f"N-{symbol}")

    out = wl.add_symbol("u1", "510300")
    assert len(out["items"]) == 1
    assert out["items"][0]["symbol"] == "510300"
    assert out["items"][0]["name"] == "N-510300"

    out2 = wl.add_symbol("u1", "510300")
    assert len(out2["items"]) == 1

    out3 = wl.remove_symbol("u1", "510300")
    assert out3["items"] == []
    out4 = wl.remove_symbol("u1", "510300")
    assert out4["items"] == []


def test_max_limit(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(wl, "get_db", lambda: db)
    monkeypatch.setattr(wl, "_lookup_name", lambda symbol: symbol)
    db.watchlists.doc = {
        "user_id": "u1",
        "items": [
            {"symbol": f"{i:06d}", "name": f"{i:06d}", "added_at": "t"}
            for i in range(100)
        ],
    }
    with pytest.raises(ValueError, match="100"):
        wl.add_symbol("u1", "510300")


def test_status_and_marks(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(wl, "get_db", lambda: db)
    monkeypatch.setattr(wl, "_lookup_name", lambda symbol: "沪深300ETF")
    wl.add_symbol("u1", "510300")

    st = wl.watchlist_status("u1", ["510300", "159915"])
    assert st["starred"]["510300"] is True
    assert st["starred"]["159915"] is False

    monkeypatch.setattr(
        "app.quote.get_last_quote",
        lambda symbol: {
            "symbol": symbol,
            "name": "沪深300ETF",
            "price": 4.2,
            "pre_close": 4.0,
            "day_chg_pct": 0.05,
            "error": None,
        },
    )
    monkeypatch.setattr(
        "app.quote.trading_session",
        lambda: {"is_trading": False, "now": "2026-07-28T22:00:00+08:00"},
    )
    marks = wl.watchlist_marks("u1")
    assert marks["count"] == 1
    assert marks["items"][0]["price"] == 4.2
    assert marks["items"][0]["day_chg_pct"] == 0.05
