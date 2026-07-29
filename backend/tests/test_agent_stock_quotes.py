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
    assert "day_chg_is_live" in SYSTEM_PROMPT or "实时" in SYSTEM_PROMPT
