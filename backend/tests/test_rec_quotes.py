# backend/tests/test_rec_quotes.py
from __future__ import annotations


def test_iter_rec_quote_events_uses_live_last_quote(monkeypatch):
    from app.advisor import snapshots as snap

    monkeypatch.setattr(
        snap,
        "effective_rec_date",
        lambda trade_date=None: "2026-07-24",
    )
    monkeypatch.setattr(
        snap,
        "get_snapshot",
        lambda td, user_id=None: {
            "trade_date": td,
            "boards": {
                "etf": {
                    "items": [
                        {
                            "symbol": "159518",
                            "name": "归档名",
                            "close": 1.0,
                            "day_chg_pct": 0.99,
                        }
                    ]
                }
            },
        },
    )
    monkeypatch.setattr(
        "app.quote.trading_session",
        lambda: {"is_trading": True, "now": "2026-07-24 10:00:00"},
    )
    monkeypatch.setattr(
        "app.quote.get_last_quote",
        lambda symbol: {
            "symbol": symbol,
            "name": "实时名",
            "price": 1.25,
            "pre_close": 1.20,
            "day_chg_pct": 0.041667,
        },
    )

    events = list(snap.iter_rec_quote_events("2026-07-24", "all", user_id="u1"))
    assert events[0]["event"] == "meta"
    assert events[0]["data"]["is_trading"] is True
    assert events[0]["data"]["live"] is True

    quote_ev = next(e for e in events if e["event"] == "quote")
    assert quote_ev["data"]["symbol"] == "159518"
    assert quote_ev["data"]["close"] == 1.25
    assert quote_ev["data"]["prev_close"] == 1.20
    assert quote_ev["data"]["day_chg_pct"] == 0.041667
    assert quote_ev["data"]["live"] is True
    # 不得沿用归档里的虚假涨跌
    assert quote_ev["data"]["day_chg_pct"] != 0.99
