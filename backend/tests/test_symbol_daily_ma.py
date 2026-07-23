from unittest.mock import patch

import json

from app.advisor.agent.tools import build_tools
from app.kline import _sma, fetch_symbol_daily_ma


def test_sma_basic():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert _sma(closes, 3) == [None, None, 2.0, 3.0, 4.0, 5.0]


def test_fetch_symbol_daily_ma_from_kline():
    fake = {
        "symbol": "600519",
        "name": "贵州茅台",
        "source": "test",
        "bars": [
            {
                "time": f"2026-01-{i:02d}",
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.0 + i,
                "volume": 1000,
            }
            for i in range(1, 26)
        ],
    }
    with patch("app.kline.get_kline", return_value=fake):
        out = fetch_symbol_daily_ma("600519", recent=10)
    assert out["symbol"] == "600519"
    assert out["latest"]["ma5"] is not None
    assert out["latest"]["ma10"] is not None
    assert out["latest"]["ma20"] is not None
    assert len(out["recent"]) == 10
    assert out["recent"][-1]["ma5"] == out["latest"]["ma5"]


def test_fetch_symbol_daily_ma_tool_registered():
    tools = {t.name: t for t in build_tools("u")}
    assert "fetch_symbol_daily_ma" in tools
    fake = {
        "symbol": "600519",
        "latest": {"close": 1, "ma5": 1, "ma10": 1, "ma20": 1},
        "recent": [],
    }
    with patch("app.kline.fetch_symbol_daily_ma", return_value=fake):
        raw = tools["fetch_symbol_daily_ma"].invoke({"symbol": "600519"})
    assert json.loads(raw)["symbol"] == "600519"
