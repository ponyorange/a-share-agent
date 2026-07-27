from app.advisor.portfolio import _build_mark_row, portfolio_marks


def test_build_mark_row_computes_pnl_and_market_value():
    row = _build_mark_row(
        {"symbol": "510300", "name": "沪深300ETF", "qty": 1000, "cost": 4.0},
        {
            "price": 4.2,
            "pre_close": 4.0,
            "day_chg_pct": 0.05,
            "name": "沪深300ETF",
            "error": None,
        },
    )
    assert row["market_value"] == 4200.0
    assert row["day_pnl"] == 200.0
    assert row["position_pnl"] == 200.0
    assert row["position_pnl_pct"] == 0.05
    assert row["day_chg_pct"] == 0.05


def test_build_mark_row_prefers_quote_name_over_symbol_placeholder():
    row = _build_mark_row(
        {"symbol": "510300", "name": "510300", "qty": 100, "cost": 1.0},
        {"price": 1.1, "pre_close": 1.0, "day_chg_pct": 0.1, "name": "沪深300ETF"},
    )
    assert row["name"] == "沪深300ETF"


def test_portfolio_marks_weights(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.portfolio.load_portfolio",
        lambda user_id: {
            "positions": [
                {"symbol": "510300", "name": "A", "qty": 100, "cost": 1.0},
                {"symbol": "159915", "name": "B", "qty": 100, "cost": 1.0},
            ]
        },
    )
    quotes = {
        "510300": {
            "symbol": "510300",
            "name": "A",
            "price": 2.0,
            "pre_close": 1.9,
            "day_chg_pct": 2.0 / 1.9 - 1,
            "error": None,
        },
        "159915": {
            "symbol": "159915",
            "name": "B",
            "price": 1.0,
            "pre_close": 1.0,
            "day_chg_pct": 0.0,
            "error": None,
        },
    }
    monkeypatch.setattr(
        "app.quote.get_last_quote",
        lambda symbol: quotes[symbol],
    )
    monkeypatch.setattr(
        "app.quote.trading_session",
        lambda: {
            "is_trading": False,
            "now": "2026-07-27T22:00:00+08:00",
            "refresh_recommended": False,
        },
    )
    out = portfolio_marks("u1")
    assert out["count"] == 2
    assert out["total_market_value"] == 300.0
    assert out["total_cost"] == 200.0
    assert out["total_position_pnl"] == 100.0
    assert out["total_return_pct"] == 0.5
    by_sym = {x["symbol"]: x for x in out["items"]}
    assert by_sym["510300"]["weight"] == round(200 / 300, 6)
    assert by_sym["159915"]["weight"] == round(100 / 300, 6)
    assert out["session"]["is_trading"] is False
