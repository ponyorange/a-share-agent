from __future__ import annotations

from fastapi.testclient import TestClient


def test_build_home_news_partial_source_failure(monkeypatch):
    from app.advisor import home_news

    monkeypatch.setattr(home_news, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(
        home_news,
        "_fetch_cctv_group",
        lambda day: {
            "ok": True,
            "source": "cctv",
            "error": None,
            "items": [
                {
                    "title": "联播一条",
                    "summary": None,
                    "published_at": None,
                    "url": None,
                    "tags": None,
                }
            ],
        },
    )
    monkeypatch.setattr(
        home_news,
        "_fetch_macro_group",
        lambda day: {"ok": False, "source": "macro", "error": "down", "items": []},
    )
    monkeypatch.setattr(
        home_news,
        "_fetch_index_sentiment_group",
        lambda day: {
            "ok": False,
            "source": "idx",
            "error": "API 不可用",
            "items": [],
        },
    )
    monkeypatch.setattr(
        home_news,
        "_fetch_sectors_group",
        lambda day: {
            "ok": True,
            "source": "sectors",
            "error": None,
            "items": [
                {
                    "title": "人工智能",
                    "summary": "+5.1%",
                    "published_at": None,
                    "url": None,
                    "tags": ["sector"],
                }
            ],
        },
    )
    monkeypatch.setattr(home_news, "_load_news_doc", lambda day: None)
    saved = {}

    def _save(doc):
        saved["doc"] = doc

    monkeypatch.setattr(home_news, "_save_news_doc", _save)

    out = home_news.get_or_build_home_news()
    assert out["trade_date"] == "2026-08-01"
    assert out["groups"]["cctv"]["ok"] is True
    assert out["groups"]["macro"]["ok"] is False
    assert out["groups"]["web"]["items"] == []
    assert saved["doc"]["groups"]["cctv"]["items"][0]["title"] == "联播一条"


def test_get_or_build_returns_cache(monkeypatch):
    from app.advisor import home_news

    cached = {
        "trade_date": "2026-08-01",
        "as_of": "2026-08-01T01:00:00+00:00",
        "groups": {
            "cctv": {"ok": True, "source": "c", "error": None, "items": []},
            "macro": {"ok": True, "source": "m", "error": None, "items": []},
            "index_sentiment": {
                "ok": False,
                "source": None,
                "error": "x",
                "items": [],
            },
            "sectors": {"ok": True, "source": "s", "error": None, "items": []},
            "web": {"ok": False, "source": None, "error": None, "items": []},
        },
    }
    monkeypatch.setattr(home_news, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(home_news, "_load_news_doc", lambda day: cached)
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not rebuild")

    monkeypatch.setattr(home_news, "_build_news_groups", boom)
    out = home_news.get_or_build_home_news()
    assert out["as_of"] == cached["as_of"]
    assert called["n"] == 0


def test_home_news_route(monkeypatch):
    from app.main import app
    from app.advisor import routes

    def _user():
        return {"id": "u1", "username": "t"}

    app.dependency_overrides[routes._user] = _user
    monkeypatch.setattr(
        routes,
        "get_or_build_home_news",
        lambda: {
            "trade_date": "2026-08-01",
            "as_of": "t",
            "groups": {
                "cctv": {"ok": True, "source": "c", "error": None, "items": []},
                "macro": {"ok": True, "source": "m", "error": None, "items": []},
                "index_sentiment": {
                    "ok": False,
                    "source": None,
                    "error": "x",
                    "items": [],
                },
                "sectors": {"ok": True, "source": "s", "error": None, "items": []},
                "web": {"ok": False, "source": None, "error": None, "items": []},
            },
        },
    )
    try:
        r = TestClient(app).get("/api/advisor/home/news")
        assert r.status_code == 200
        assert r.json()["trade_date"] == "2026-08-01"
    finally:
        app.dependency_overrides.clear()


def test_home_news_requires_auth():
    from app.main import app

    assert TestClient(app).get("/api/advisor/home/news").status_code == 401
