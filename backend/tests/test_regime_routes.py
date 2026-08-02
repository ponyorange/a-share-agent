from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app


def _auth():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}


def test_regime_current_requires_auth_and_returns_current(monkeypatch):
    import app.advisor.regime as regime

    app.dependency_overrides.clear()
    assert TestClient(app).get("/api/advisor/regime/current").status_code == 401

    _auth()
    monkeypatch.setattr(
        regime,
        "get_current_regime",
        lambda: {
            "trade_date": "2026-08-02",
            "trend_regime": "range",
            "sentiment_cycle": "ebb",
            "gate_level": "risk_off",
            "position_cap": 0.15,
            "pool_policy": "defense_only",
            "data_quality": "ok",
            "evidence": [],
        },
    )
    try:
        response = TestClient(app).get("/api/advisor/regime/current")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["gate_level"] == "risk_off"


def test_regime_history_and_sentiment_routes(monkeypatch):
    import app.advisor.regime as regime

    _auth()
    monkeypatch.setattr(
        regime,
        "get_regime_history",
        lambda limit=20: [{"trade_date": "2026-08-02", "gate_level": "normal"}],
    )
    monkeypatch.setattr(
        regime,
        "get_sentiment_detail",
        lambda: {"metrics": {"limit_up_count": 12}, "sentiment_cycle": "strengthen"},
    )
    try:
        client = TestClient(app)
        history = client.get("/api/advisor/regime/history?limit=1")
        sentiment = client.get("/api/advisor/regime/sentiment")
    finally:
        app.dependency_overrides.clear()

    assert history.status_code == 200
    assert history.json() == [{"trade_date": "2026-08-02", "gate_level": "normal"}]
    assert sentiment.status_code == 200
    assert sentiment.json()["metrics"]["limit_up_count"] == 12
