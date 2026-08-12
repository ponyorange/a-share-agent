from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app


def _auth():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u_pt", "username": "a"}


def test_paper_trader_requires_auth():
    app.dependency_overrides.clear()
    assert TestClient(app).get("/api/advisor/paper-trader").status_code == 401


def test_paper_trader_start_get_stop(monkeypatch):
    import app.advisor.paper_trader.store as store_mod

    app.dependency_overrides.clear()
    _auth()

    state: dict = {}

    def fake_start(uid, body=None):
        state["sess"] = {
            "id": "s1",
            "user_id": uid,
            "status": "running",
            "mode": "signal_first",
            "interval_sec": 600,
        }
        return state["sess"]

    def fake_get(uid):
        return state.get("sess")

    def fake_stop(uid):
        state["sess"] = {**state["sess"], "status": "stopped", "next_run_at": None}
        return state["sess"]

    monkeypatch.setattr(store_mod, "start_session", fake_start)
    monkeypatch.setattr(store_mod, "get_session", fake_get)
    monkeypatch.setattr(store_mod, "stop_session", fake_stop)

    client = TestClient(app)
    try:
        r = client.post("/api/advisor/paper-trader/start", json={})
        assert r.status_code == 200
        assert r.json()["status"] == "running"
        r2 = client.get("/api/advisor/paper-trader")
        assert r2.json()["status"] == "running"
        r3 = client.post("/api/advisor/paper-trader/stop")
        assert r3.json()["status"] == "stopped"
    finally:
        app.dependency_overrides.clear()
