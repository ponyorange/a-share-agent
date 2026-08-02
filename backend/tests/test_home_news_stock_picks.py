from __future__ import annotations

import json


def test_stock_pick_tools_exclude_recommendations(monkeypatch):
    from app.advisor import home_news_stock_picks as picks
    from app.advisor.agent import tools as agent_tools

    class FakeTool:
        def __init__(self, name: str):
            self.name = name

    def fake_build(user_id, *, exclude=None):
        names = [
            "get_stock_quotes",
            "get_leaderboard_brief",
            "fetch_stock_news",
            "fetch_symbol_daily_ma",
            "delegate_data_task",
            "run_python_script",
            "register_tool_dataset",
            "get_today_recommendations",
            "get_recommendation_archive",
            "list_recommendation_dates",
            "web_research",
        ]
        # simulate exclude applied upstream
        blocked = set(exclude or [])
        return [FakeTool(n) for n in names if n not in blocked]

    monkeypatch.setattr(agent_tools, "build_tools", fake_build)
    out = picks.build_home_news_stock_pick_tools("u1")
    names = {t.name for t in out}
    assert "get_today_recommendations" not in names
    assert "get_recommendation_archive" not in names
    assert "list_recommendation_dates" not in names
    assert "get_stock_quotes" in names
    assert "delegate_data_task" in names
    assert "web_research" in names


def test_parse_stock_pick_payload_filters_and_caps():
    from app.advisor.home_news_stock_picks import parse_stock_pick_payload

    raw = json.dumps(
        {
            "symbols": [
                {"symbol": "600519", "name": "贵州茅台", "reason": "联播提消费"},
                {"symbol": "ABC", "name": "坏码", "reason": "x"},
                {"symbol": "000001", "name": "平安银行", "reason": "金融政策"},
                {"symbol": "000002", "name": "万科A", "reason": "地产"},
                {"symbol": "000003", "name": "三", "reason": "r"},
                {"symbol": "000004", "name": "四", "reason": "r"},
                {"symbol": "000005", "name": "五", "reason": "r"},
                {"symbol": "000006", "name": "六", "reason": "应被截断"},
            ],
            "symbols_note": "",
        },
        ensure_ascii=False,
    )
    out = parse_stock_pick_payload(raw)
    assert len(out["symbols"]) == 5
    assert out["symbols"][0]["symbol"] == "600519"
    assert out["symbols"][0]["horizon"] == "3-5d"
    assert all(x["symbol"] != "ABC" for x in out["symbols"])


def test_parse_stock_pick_empty_note():
    from app.advisor.home_news_stock_picks import parse_stock_pick_payload

    out = parse_stock_pick_payload('{"symbols":[],"symbols_note":"证据不足"}')
    assert out["symbols"] == []
    assert out["symbols_note"] == "证据不足"


def test_run_stock_picks_uses_agent_and_parses(monkeypatch):
    from app.advisor import home_news_stock_picks as picks

    monkeypatch.setattr(picks, "build_home_news_stock_pick_tools", lambda uid: [])
    monkeypatch.setattr(picks, "build_chat_model", lambda *a, **k: object())

    class FakeAgent:
        def invoke(self, payload, config=None):
            assert config and config.get("recursion_limit") == 16
            return {
                "messages": [
                    type(
                        "M",
                        (),
                        {
                            "content": json.dumps(
                                {
                                    "symbols": [
                                        {
                                            "symbol": "600519",
                                            "name": "贵州茅台",
                                            "reason": "消费政策预期",
                                        }
                                    ],
                                    "symbols_note": "",
                                },
                                ensure_ascii=False,
                            )
                        },
                    )()
                ]
            }

    monkeypatch.setattr(
        picks,
        "create_react_agent",
        lambda model, tools, prompt=None: FakeAgent(),
    )
    out = picks.run_home_news_stock_picks(
        "u1",
        news={"trade_date": "2026-08-01", "groups": {}},
        sectors=[{"name": "白酒", "reason": "政策"}],
    )
    assert out["symbols"][0]["symbol"] == "600519"


def test_run_stock_picks_degrades_on_error(monkeypatch):
    from app.advisor import home_news_stock_picks as picks

    monkeypatch.setattr(picks, "build_home_news_stock_pick_tools", lambda uid: [])
    monkeypatch.setattr(
        picks, "build_chat_model", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = picks.run_home_news_stock_picks("u1", news={}, sectors=[])
    assert out["symbols"] == []
    assert out["symbols_note"]
