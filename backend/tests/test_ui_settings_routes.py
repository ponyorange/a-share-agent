from fastapi.testclient import TestClient

from app.advisor import routes
from app.advisor.ui_settings import DEFAULT_THEMES
from app.auth import get_current_user
from app.main import app


def test_get_and_put_ui_settings_use_authenticated_user(monkeypatch):
    calls = []
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    monkeypatch.setattr(routes.context, "bind_user", lambda uid: None)
    monkeypatch.setattr(
        routes,
        "get_ui_settings",
        lambda uid: {
            "active_template": "modern_data",
            "colors": DEFAULT_THEMES["modern_data"],
            "updated_at": None,
        },
    )
    monkeypatch.setattr(
        routes,
        "save_ui_settings",
        lambda uid, **body: calls.append((uid, body))
        or {
            **body,
            "updated_at": "2026-07-25T05:30:00+00:00",
        },
    )
    try:
        get_response = TestClient(app).get("/api/advisor/ui/settings")
        put_response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={
                "active_template": "classic_market",
                "colors": DEFAULT_THEMES["classic_market"],
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert get_response.status_code == 200
    assert put_response.status_code == 200
    assert calls[0][0] == "u1"
    assert calls[0][1]["active_template"] == "classic_market"


def test_put_accepts_deep_navy_template(monkeypatch):
    calls = []
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    monkeypatch.setattr(routes.context, "bind_user", lambda uid: None)
    monkeypatch.setattr(
        routes,
        "save_ui_settings",
        lambda uid, **body: calls.append((uid, body))
        or {
            **body,
            "updated_at": "2026-07-25T05:30:00+00:00",
        },
    )
    try:
        response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={
                "active_template": "deep_navy",
                "colors": DEFAULT_THEMES["modern_data"],
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert calls[0][0] == "u1"
    assert calls[0][1]["active_template"] == "deep_navy"


def test_put_rejects_extra_or_invalid_color_fields():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    colors = dict(DEFAULT_THEMES["modern_data"], extra="#FFFFFF")
    try:
        response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={"active_template": "modern_data", "colors": colors},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_put_rejects_unknown_template():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    try:
        response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={
                "active_template": "dark",
                "colors": DEFAULT_THEMES["modern_data"],
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_put_rejects_missing_color_field():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    colors = dict(DEFAULT_THEMES["modern_data"])
    del colors["brand"]
    try:
        response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={"active_template": "modern_data", "colors": colors},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_put_rejects_invalid_hex():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    colors = dict(DEFAULT_THEMES["modern_data"], brand="blue")
    try:
        response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={"active_template": "modern_data", "colors": colors},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_put_accepts_low_contrast_valid_hex(monkeypatch):
    calls = []
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "a"}
    monkeypatch.setattr(routes.context, "bind_user", lambda uid: None)
    monkeypatch.setattr(
        routes,
        "save_ui_settings",
        lambda uid, **body: calls.append((uid, body))
        or {
            **body,
            "updated_at": "2026-07-25T05:30:00+00:00",
        },
    )
    colors = dict(DEFAULT_THEMES["modern_data"], text_primary="#F6F7FB")
    try:
        response = TestClient(app).put(
            "/api/advisor/ui/settings",
            json={"active_template": "modern_data", "colors": colors},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == "u1"
    assert calls[0][1]["colors"]["text_primary"] == "#F6F7FB"