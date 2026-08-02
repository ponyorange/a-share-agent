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


def test_regime_brief_template_route():
    _auth()
    try:
        response = TestClient(app).get("/api/advisor/regime/brief-template")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "run_at"
    assert body["suggested_time"] == "09:05"
    assert "get_market_regime" in body["prompt"]


def test_recommendations_accepts_regime_override(monkeypatch):
    import app.advisor.routes as routes

    seen = {}
    _auth()
    monkeypatch.setattr(routes, "_bind", lambda user: str(user["id"]))
    monkeypatch.setattr(routes, "effective_rec_date", lambda as_of=None: "2026-08-02")
    monkeypatch.setattr(routes, "has_snapshot", lambda trade_date, user_id=None: False)
    monkeypatch.setattr(
        routes,
        "get_recommendations",
        lambda **kwargs: seen.setdefault(
            "result",
            {
                "as_of": "2026-08-02",
                "items": [],
                "boards": {},
                "regime_override": kwargs.get("regime_override"),
            },
        ),
    )
    monkeypatch.setattr(
        routes,
        "save_snapshot",
        lambda payload, trade_date=None, user_id=None: {"saved": True},
    )
    try:
        response = TestClient(app).get(
            "/api/advisor/recommendations?regime_override=true"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["regime_override"] is True
