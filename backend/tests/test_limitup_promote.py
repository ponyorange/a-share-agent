from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.advisor import limitup_promote as promote


def test_filter_picks_drops_hallucinated_symbols():
    candidates = [
        {"symbol": "000001", "name": "平安银行", "board_count": 2},
        {"symbol": "600000", "name": "浦发银行", "board_count": 1},
    ]
    raw = [
        {"symbol": "000001", "name": "平安银行", "board_count": 2, "score": 5, "reason": "早封"},
        {"symbol": "999999", "name": "幻觉", "board_count": 3, "score": 5, "reason": "编造"},
        {"symbol": "600000", "score": 9, "reason": "过强"},
    ]
    picks = promote.filter_picks_against_context(raw, candidates)
    assert [p["symbol"] for p in picks] == ["000001", "600000"]
    assert picks[0]["score"] == 5
    assert picks[1]["score"] == 5  # clamped


def test_generate_promote_picks_uses_cache(monkeypatch):
    promote.clear_promote_cache()
    calls = {"n": 0}

    monkeypatch.setattr(
        promote,
        "resolve_llm_credentials",
        lambda uid: {"api_key": "k", "base_url": "u", "model": "m"},
    )
    monkeypatch.setattr(
        promote,
        "build_promote_context",
        lambda force_pool=False: {
            "date": "2026-08-08",
            "as_of": "2026-08-08T15:00:00+08:00",
            "session": {"is_trading": False},
            "candidates": [
                {"symbol": "000001", "name": "平安银行", "board_count": 2},
            ],
            "candidate_count": 1,
            "context_count": 1,
        },
    )

    class _Model:
        def stream(self, _msgs):
            calls["n"] += 1
            yield SimpleNamespace(
                content='{"summary":"测试","picks":[{"symbol":"000001","name":"平安银行","board_count":2,"score":4,"reason":"封单强"}]}',
                additional_kwargs={},
                response_metadata={},
            )

        def invoke(self, _msgs):
            calls["n"] += 1
            return SimpleNamespace(
                content='{"summary":"测试","picks":[{"symbol":"000001","name":"平安银行","board_count":2,"score":4,"reason":"封单强"}]}'
            )

    monkeypatch.setattr(promote, "build_chat_model", lambda *a, **k: _Model())
    monkeypatch.setattr(
        promote,
        "build_theme_context",
        lambda uid: {
            "news_groups": {},
            "hot_sectors": [],
            "brief": None,
            "used": {"news": False, "hot_sectors": False, "brief": False},
        },
    )

    first = promote.generate_promote_picks("u1", force=False)
    second = promote.generate_promote_picks("u1", force=False)
    assert calls["n"] == 1
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert first["picks"][0]["symbol"] == "000001"

    promote.generate_promote_picks("u1", force=True)
    assert calls["n"] == 2


def test_generate_requires_llm_key(monkeypatch):
    promote.clear_promote_cache()

    def _raise(_uid):
        raise ValueError("尚未配置 DeepSeek API Key，请先在 Agent 设置中填写")

    monkeypatch.setattr(promote, "resolve_llm_credentials", _raise)
    with pytest.raises(ValueError, match="DeepSeek"):
        promote.generate_promote_picks("u1")


def test_iter_promote_events_streams_thinking(monkeypatch):
    promote.clear_promote_cache()
    monkeypatch.setattr(
        promote,
        "resolve_llm_credentials",
        lambda uid: {"api_key": "k", "base_url": "u", "model": "m"},
    )
    monkeypatch.setattr(
        promote,
        "build_promote_context",
        lambda force_pool=False: {
            "date": "2026-08-08",
            "as_of": "2026-08-08T15:00:00+08:00",
            "session": {},
            "candidates": [
                {"symbol": "000001", "name": "平安银行", "board_count": 2},
            ],
            "candidate_count": 1,
            "context_count": 1,
        },
    )
    monkeypatch.setattr(
        promote,
        "build_theme_context",
        lambda uid: {
            "news_groups": {"联播": ["某政策加码"]},
            "hot_sectors": [{"name": "银行", "change_pct": 2.1}],
            "brief": {"summary": "银行偏强", "bullets": [], "sectors": []},
            "used": {"news": True, "hot_sectors": True, "brief": True},
        },
    )

    captured: dict[str, str] = {}

    class _Model:
        def stream(self, msgs):
            captured["human"] = str(msgs[1].content)
            yield SimpleNamespace(
                content="",
                additional_kwargs={"reasoning_content": "先看连板"},
                response_metadata={},
            )
            yield SimpleNamespace(
                content='{"summary":"ok","picks":[{"symbol":"000001","name":"平安银行","board_count":2,"score":4,"reason":"稳"}]}',
                additional_kwargs={},
                response_metadata={},
            )

    monkeypatch.setattr(promote, "build_chat_model", lambda *a, **k: _Model())
    events = list(promote.iter_promote_events("u1", force=True))
    thinking = "".join(
        e["data"]["delta"] for e in events if e["event"] == "thinking"
    )
    assert "先看连板" in thinking
    assert "某政策加码" in captured["human"]
    assert "银行" in captured["human"]
    done = next(e for e in events if e["event"] == "done")
    assert done["data"]["picks"][0]["symbol"] == "000001"
    assert done["data"]["theme_used"] == {
        "news": True,
        "hot_sectors": True,
        "brief": True,
    }


def test_build_theme_context_compacts_sources(monkeypatch):
    monkeypatch.setattr(
        promote,
        "get_or_build_home_news",
        lambda: {
            "groups": {
                "cctv": {
                    "ok": True,
                    "items": [{"title": "联播一条很长的标题" + "x" * 100}],
                },
                "macro": {"ok": True, "items": [{"title": "宏观政策要点"}]},
                "index_sentiment": {"ok": False, "items": []},
                "sectors": {"ok": True, "items": [{"title": "题材A"}]},
                "web": {"ok": False, "items": []},
            }
        },
    )
    monkeypatch.setattr(
        promote,
        "list_hot_sectors",
        lambda top=8: {
            "items": [{"name": "半导体", "change_pct": 3.2}],
        },
    )
    monkeypatch.setattr(
        promote,
        "get_home_news_brief",
        lambda uid: {
            "status": "ready",
            "summary": "情绪修复",
            "bullets": ["政策托底"],
            "sectors": [{"name": "半导体", "reason": "景气"}],
        },
    )
    theme = promote.build_theme_context("u1")
    assert theme["used"]["news"] is True
    assert theme["used"]["hot_sectors"] is True
    assert theme["used"]["brief"] is True
    assert "联播" in theme["news_groups"]
    assert len(theme["news_groups"]["联播"][0]) <= 80
    assert theme["hot_sectors"][0]["name"] == "半导体"
    assert theme["brief"]["summary"] == "情绪修复"


def test_normalize_exposes_seal_fields():
    from app.limitup import normalize_pool_row

    row = normalize_pool_row(
        {
            "代码": "002792",
            "名称": "通宇通讯",
            "涨跌幅": 10.0,
            "连板数": 2,
            "封板资金": 305090470,
            "首次封板时间": "092500",
            "最后封板时间": "092500",
            "炸板次数": 0,
            "换手率": 2.45,
            "所属行业": "通信设备",
        },
        status="sealed",
    )
    assert row is not None
    assert row["seal_funds"] == 305090470
    assert row["first_seal_time"] == "09:25:00"
    assert row["break_count"] == 0
    assert row["industry"] == "通信设备"
