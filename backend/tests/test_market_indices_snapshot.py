import json
from unittest.mock import patch

import pandas as pd

from app.advisor.agent.tools import build_tools
from app.market import (
    featured_indices_snapshot,
    fetch_index_extremes,
    resolve_index,
)


def test_featured_indices_snapshot_keeps_star50_and_slim_fields():
    market = {
        "updated_at": "2026-07-21T12:00:00+08:00",
        "source": "eastmoney.ulist",
        "featured": [
            {
                "symbol": "000688",
                "name": "科创50",
                "price": 1001.23,
                "change": 10.5,
                "change_pct": 1.06,
                "open": 990.0,
                "high": 1010.0,
                "low": 980.0,
                "pre_close": 990.73,
                "volume": 1e9,
                "amount": 2e10,
                "featured": True,
            }
        ],
        "boards": {"gainers": [{"symbol": "600000"}]},
        "summary": {"amount_total": 1},
    }
    out = featured_indices_snapshot(market)
    assert out["updated_at"] == "2026-07-21T12:00:00+08:00"
    assert out["source"] == "eastmoney.ulist"
    assert "boards" not in out
    assert "summary" not in out
    assert len(out["indices"]) == 1
    row = out["indices"][0]
    assert row == {
        "symbol": "000688",
        "name": "科创50",
        "price": 1001.23,
        "change": 10.5,
        "change_pct": 1.06,
        "amount": 2e10,
    }


def test_featured_indices_snapshot_empty_featured():
    out = featured_indices_snapshot(
        {"updated_at": "t", "source": "s", "featured": []}
    )
    assert out["indices"] == []


def test_fetch_market_indices_tool_uses_snapshot():
    tools = build_tools("test-user")
    by_name = {t.name: t for t in tools}
    assert "fetch_market_indices" in by_name
    fake = {
        "updated_at": "t",
        "source": "eastmoney.ulist",
        "featured": [
            {
                "symbol": "000688",
                "name": "科创50",
                "price": 1.0,
                "change": 0.1,
                "change_pct": 0.2,
                "amount": 3.0,
                "featured": True,
            }
        ],
        "boards": {},
    }
    with patch("app.market.get_market", return_value=fake):
        raw = by_name["fetch_market_indices"].invoke({})
    data = json.loads(raw)
    assert data["indices"][0]["name"] == "科创50"
    assert "boards" not in data


def test_fetch_market_indices_tool_on_error():
    tools = {t.name: t for t in build_tools("u")}
    with patch("app.market.get_market", side_effect=RuntimeError("down")):
        raw = tools["fetch_market_indices"].invoke({})
    data = json.loads(raw)
    assert data["indices"] == []
    assert "error" in data


def test_resolve_index_by_name_and_code():
    assert resolve_index("科创50")["symbol"] == "000688"
    assert resolve_index("000688")["name"] == "科创50"
    assert resolve_index("sh000688")["symbol"] == "000688"
    assert resolve_index("不存在的指数") is None


def test_fetch_index_extremes_computes_ath_from_daily():
    fake = pd.DataFrame(
        [
            {"date": "2020-07-14", "open": 1, "high": 1726.19, "low": 1, "close": 1700.0},
            {"date": "2026-06-30", "open": 1, "high": 2210.0, "low": 1, "close": 2207.865},
            {"date": "2026-07-01", "open": 1, "high": 2255.248, "low": 1, "close": 2200.0},
            {"date": "2026-07-20", "open": 1, "high": 1776.0, "low": 1, "close": 1718.69},
        ]
    )
    with patch("app.market.ak.stock_zh_index_daily", return_value=fake):
        out = fetch_index_extremes("科创50")
    assert out["symbol"] == "000688"
    assert out["ath_intraday"]["price"] == 2255.248
    assert out["ath_intraday"]["date"] == "2026-07-01"
    assert out["ath_close"]["price"] == 2207.865
    assert out["ath_close"]["date"] == "2026-06-30"
    assert out["latest"]["close"] == 1718.69


def test_fetch_index_extremes_tool_registered():
    tools = {t.name: t for t in build_tools("u")}
    assert "fetch_index_extremes" in tools
    fake = {
        "query": "科创50",
        "symbol": "000688",
        "name": "科创50",
        "ath_intraday": {"price": 2255.248, "date": "2026-07-01"},
    }
    with patch("app.market.fetch_index_extremes", return_value=fake):
        raw = tools["fetch_index_extremes"].invoke({"query": "科创50"})
    data = json.loads(raw)
    assert data["ath_intraday"]["price"] == 2255.248
