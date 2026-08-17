from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_get_brief_idle_when_missing(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(hb, "_load_brief", lambda uid, day: None)
    out = hb.get_home_news_brief("u1")
    assert out["status"] == "idle"
    assert out["bullets"] == []


def test_generate_brief_parses_llm_json(monkeypatch):
    from app.advisor import home_news_brief as hb

    class FakeModel:
        def invoke(self, messages):
            class R:
                content = json.dumps(
                    {
                        "summary": "政策偏暖，成长占优",
                        "bullets": ["联播提及科技创新", "流动性边际改善"],
                        "sectors": [{"name": "人工智能", "reason": "题材活跃"}],
                        "symbols": [
                            {
                                "symbol": "600519",
                                "name": "贵州茅台",
                                "reason": "示例",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

            return R()

    monkeypatch.setattr(hb, "resolve_llm_credentials", lambda *a, **k: {"api_key": "x"})
    monkeypatch.setattr(hb, "build_chat_model", lambda uid, **k: FakeModel())
    monkeypatch.setattr(hb, "_optional_knowledge_titles", lambda uid: [])
    monkeypatch.setattr(hb, "_maybe_fetch_web_items", lambda uid: [])
    monkeypatch.setattr(hb, "_set_progress", lambda *a, **k: None)
    monkeypatch.setattr(
        hb,
        "run_home_news_stock_picks",
        lambda uid, news, sectors: {
            "symbols": [{"symbol": "600519", "name": "贵州茅台", "reason": "示例"}],
            "symbols_note": None,
        },
    )

    news = {
        "trade_date": "2026-08-01",
        "as_of": "t0",
        "groups": {
            "cctv": {
                "ok": True,
                "source": "c",
                "error": None,
                "items": [
                    {
                        "title": "联播",
                        "summary": None,
                        "published_at": None,
                        "url": None,
                        "tags": None,
                    }
                ],
            },
            "macro": {"ok": True, "source": "m", "error": None, "items": []},
            "index_sentiment": {
                "ok": False,
                "source": None,
                "error": "x",
                "items": [],
            },
            "sectors": {
                "ok": True,
                "source": "s",
                "error": None,
                "items": [
                    {
                        "title": "人工智能",
                        "summary": "+5%",
                        "published_at": None,
                        "url": None,
                        "tags": ["sector"],
                    }
                ],
            },
            "web": {"ok": False, "source": None, "error": None, "items": []},
        },
    }
    out = hb.generate_home_news_brief("u1", news)
    assert out["summary"].startswith("政策")
    assert len(out["bullets"]) == 2
    assert out["sectors"][0]["name"] == "人工智能"
    assert out["symbols"][0]["symbol"] == "600519"


def test_generate_brief_runs_stock_picks_after_summary(monkeypatch):
    from app.advisor import home_news_brief as hb

    class FakeModel:
        def invoke(self, messages):
            class R:
                content = json.dumps(
                    {
                        "summary": "政策偏暖",
                        "bullets": ["要点"],
                        "sectors": [{"name": "人工智能", "reason": "题材"}],
                        "symbols": [{"symbol": "999999", "name": "应忽略", "reason": "x"}],
                    },
                    ensure_ascii=False,
                )

            return R()

    monkeypatch.setattr(hb, "resolve_llm_credentials", lambda *a, **k: {"api_key": "x"})
    monkeypatch.setattr(hb, "build_chat_model", lambda uid, **k: FakeModel())
    monkeypatch.setattr(hb, "_optional_knowledge_titles", lambda uid: [])
    monkeypatch.setattr(hb, "_maybe_fetch_web_items", lambda uid: [])
    monkeypatch.setattr(hb, "_set_progress", lambda *a, **k: None)
    monkeypatch.setattr(
        hb,
        "run_home_news_stock_picks",
        lambda uid, news, sectors: {
            "symbols": [
                {
                    "symbol": "600519",
                    "name": "贵州茅台",
                    "reason": "消费预期",
                    "horizon": "3-5d",
                }
            ],
            "symbols_note": None,
        },
    )
    news = {
        "trade_date": "2026-08-01",
        "as_of": "t0",
        "groups": {
            "cctv": {"ok": True, "source": "c", "error": None, "items": []},
            "macro": {"ok": True, "source": "m", "error": None, "items": []},
            "index_sentiment": {"ok": False, "source": None, "error": "x", "items": []},
            "sectors": {"ok": True, "source": "s", "error": None, "items": []},
            "web": {"ok": False, "source": None, "error": None, "items": []},
        },
    }
    out = hb.generate_home_news_brief("u1", news)
    assert out["summary"] == "政策偏暖"
    assert out["symbols"][0]["symbol"] == "600519"
    assert out["symbols"][0]["symbol"] != "999999"


def test_public_includes_symbols_note(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(
        hb,
        "_load_brief",
        lambda uid, day: {
            "trade_date": "2026-08-01",
            "status": "ready",
            "summary": "s",
            "bullets": [],
            "sectors": [],
            "symbols": [],
            "symbols_note": "暂无足够证据的观察股",
            "updated_at": "t",
            "error": None,
            "news_as_of": "t0",
        },
    )
    out = hb.get_home_news_brief("u1")
    assert out["symbols_note"] == "暂无足够证据的观察股"
    assert out["progress"] is None


def test_public_includes_progress_when_running(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(
        hb,
        "_load_brief",
        lambda uid, day: {
            "trade_date": "2026-08-01",
            "status": "running",
            "summary": "",
            "bullets": [],
            "sectors": [],
            "symbols": [],
            "progress": {"phase": "picks", "message": "筛选资讯驱动观察股…"},
            "updated_at": "t",
            "error": None,
            "news_as_of": None,
        },
    )
    out = hb.get_home_news_brief("u1")
    assert out["progress"]["phase"] == "picks"
    assert "观察股" in out["progress"]["message"]


def test_set_progress_writes_phase(monkeypatch):
    from app.advisor import home_news_brief as hb

    saved: dict = {}
    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(
        hb,
        "_load_brief",
        lambda uid, day: {
            "status": "running",
            "summary": "old",
            "bullets": [],
            "sectors": [],
            "symbols": [],
        },
    )

    def _save(uid, day, fields):
        saved.update(fields)
        return {"status": "running", **fields, "trade_date": day}

    monkeypatch.setattr(hb, "_save_brief", _save)
    hb._set_progress("u1", "2026-08-01", "brief")
    assert saved["progress"]["phase"] == "brief"
    assert saved["progress"]["message"] == "撰写市场解读…"


def test_refresh_rejects_without_llm_key(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")

    def _boom(*_a, **_k):
        raise ValueError("尚未配置 API Key，请先在模型配置中填写")

    monkeypatch.setattr(hb, "resolve_llm_credentials", _boom)
    try:
        hb.start_home_news_brief_refresh("u1")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "模型配置" in str(exc)


def test_refresh_reuses_running(monkeypatch):
    from app.advisor import home_news_brief as hb

    monkeypatch.setattr(hb, "last_trading_day", lambda: "2026-08-01")
    monkeypatch.setattr(hb, "resolve_llm_credentials", lambda *a, **k: {"api_key": "x"})
    existing = {
        "user_id": "u1",
        "trade_date": "2026-08-01",
        "status": "running",
        "summary": "",
        "bullets": [],
        "sectors": [],
        "symbols": [],
        "updated_at": "t",
        "error": None,
        "news_as_of": None,
    }
    monkeypatch.setattr(hb, "_load_brief", lambda uid, day: existing)
    monkeypatch.setattr(hb, "_thread_alive_for", lambda uid, day: True)
    started = {"n": 0}
    monkeypatch.setattr(
        hb,
        "_spawn_refresh_thread",
        lambda *a, **k: started.__setitem__("n", started["n"] + 1),
    )
    out = hb.start_home_news_brief_refresh("u1")
    assert out["status"] == "running"
    assert started["n"] == 0


def test_brief_routes(monkeypatch):
    from app.main import app
    from app.advisor import routes

    def _user():
        return {"id": "u1", "username": "t"}

    app.dependency_overrides[routes._user] = _user
    monkeypatch.setattr(
        routes,
        "get_home_news_brief",
        lambda uid: {
            "trade_date": "2026-08-01",
            "status": "idle",
            "summary": "",
            "bullets": [],
            "sectors": [],
            "symbols": [],
            "updated_at": None,
            "error": None,
            "news_as_of": None,
        },
    )

    def _refresh(uid):
        raise ValueError("尚未配置 API Key，请先在模型配置中填写")

    monkeypatch.setattr(routes, "start_home_news_brief_refresh", _refresh)
    try:
        client = TestClient(app)
        g = client.get("/api/advisor/home/news-brief")
        assert g.status_code == 200
        assert g.json()["status"] == "idle"
        p = client.post("/api/advisor/home/news-brief/refresh")
        assert p.status_code == 400
        assert "模型配置" in p.json()["detail"]
    finally:
        app.dependency_overrides.clear()
