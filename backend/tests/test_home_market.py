from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app


def test_list_hot_sectors_sorts_by_change_pct_and_caps_top(monkeypatch):
    from app.advisor import home_market

    monkeypatch.setattr(
        home_market,
        "_raw_industry_rows",
        lambda trade_date=None: [
            {"name": "固态电池", "change_pct": 3.2},
            {"name": "银行", "change_pct": -0.5},
            {"name": "人工智能", "change_pct": 5.1},
            {"name": "煤炭", "change_pct": 1.0},
        ],
    )
    out = home_market.list_hot_sectors(top=2)
    assert out["ok"] is True
    assert [x["name"] for x in out["items"]] == ["人工智能", "固态电池"]
    assert out["items"][0]["rank"] == 1
    assert out["items"][0]["change_pct"] == 5.1
    assert out["items"][0]["strength"] == 1.0


def test_list_hot_sectors_empty(monkeypatch):
    from app.advisor import home_market

    monkeypatch.setattr(home_market, "_raw_industry_rows", lambda trade_date=None: [])
    monkeypatch.setattr(
        home_market,
        "fetch_industry_strength_map",
        lambda day: {"by_name": {}, "ok": False, "source": "t", "error": "empty"},
    )
    out = home_market.list_hot_sectors(top=8)
    assert out["ok"] is False
    assert out["items"] == []


def test_sectors_and_summary_routes(monkeypatch):
    from app.advisor import routes
    import app.advisor.regime as regime

    app.dependency_overrides.clear()
    assert TestClient(app).get("/api/advisor/market/sectors").status_code == 401

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u1",
        "username": "t",
    }
    monkeypatch.setattr(
        routes,
        "list_hot_sectors",
        lambda top=8: {
            "trade_date": "2026-08-01",
            "ok": True,
            "source": "test",
            "items": [
                {
                    "rank": 1,
                    "name": "人工智能",
                    "change_pct": 5.1,
                    "strength": 1.0,
                }
            ],
        },
    )
    monkeypatch.setattr(
        regime,
        "get_regime_for_gate",
        lambda allow_stale=True: {
            "gate_level": "normal",
            "trend_regime": "range",
            "sentiment_cycle": "strengthen",
            "position_cap": 0.7,
            "data_quality": "ok",
            "metrics": {
                "breadth": 0.55,
                "max_board": 9,
                "promotion_rate": 0.14,
                "limit_up_count": 80,
            },
        },
    )
    try:
        client = TestClient(app)
        s = client.get("/api/advisor/market/sectors?top=3")
        assert s.status_code == 200
        assert s.json()["items"][0]["name"] == "人工智能"
        r = client.get("/api/advisor/regime/summary")
        assert r.status_code == 200
        assert r.json()["gate_level"] == "normal"
    finally:
        app.dependency_overrides.clear()
