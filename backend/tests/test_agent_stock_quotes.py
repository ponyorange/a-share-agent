import json

from app.advisor.agent import tools as tools_mod
from app.advisor.agent.tools import build_tools
from app.advisor import context as advisor_context


def test_get_stock_quotes_formats_live_chg(monkeypatch):
    monkeypatch.setattr(advisor_context, "bind_user", lambda _uid: None)
    monkeypatch.setattr(
        "app.quote.get_last_quote",
        lambda symbol: {
            "symbol": symbol,
            "name": "测",
            "price": 10.5,
            "pre_close": 10.0,
            "day_chg_pct": 0.05,
            "error": None,
        },
    )
    by_name = {t.name: t for t in build_tools("u1")}
    assert "get_stock_quotes" in by_name
    payload = json.loads(by_name["get_stock_quotes"].invoke({"symbols": "600000"}))
    assert payload["ok"] is True
    q = payload["quotes"][0]
    assert q["day_chg_is_live"] is True
    assert q["day_chg"] == "+5.00%"
    assert q["day_chg_pct"] == 0.05


def test_slim_rec_marks_archive_chg():
    rows = tools_mod._slim_rec_items(
        [{"symbol": "600000", "name": "测", "score": 0.5, "day_chg_pct": 0.02, "close": 10}]
    )
    assert rows[0]["day_chg_is_live"] is False
    assert "archive_day_chg" in rows[0]
    assert "day_chg" not in rows[0]


def test_system_prompt_requires_live_quotes():
    from app.advisor.agent.graph import SYSTEM_PROMPT

    assert "get_stock_quotes" in SYSTEM_PROMPT
    assert "数据时效校验" in SYSTEM_PROMPT
    assert "盘中先拉实时" in SYSTEM_PROMPT
    assert "标注截止日期" in SYSTEM_PROMPT or "截至" in SYSTEM_PROMPT
    assert "多源交叉验证" in SYSTEM_PROMPT
    assert "暂缺实时数据" in SYSTEM_PROMPT
    assert "立即纠正" in SYSTEM_PROMPT


def test_current_time_section_includes_trading_hint():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.advisor.agent import graph as agent_graph

    tz = ZoneInfo("Asia/Shanghai")
    # 周一上午盘中
    text = agent_graph._current_time_section(
        now=datetime(2026, 7, 27, 10, 30, tzinfo=tz),
    )
    assert "交易时段" in text
    assert "盘中" in text or "实时" in text
