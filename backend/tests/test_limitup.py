from __future__ import annotations

from app import limitup as lim
from app.limitup import (
    apply_fund_flow,
    build_ladder,
    enrich_fund_flow,
    merge_today_rows,
    normalize_pool_row,
    parse_flow_num,
)


def test_normalize_converts_percent_to_ratio():
    row = normalize_pool_row(
        {"代码": "002827", "名称": "高争民爆", "涨跌幅": 10.0, "连板数": 2},
        status="sealed",
    )
    assert row["symbol"] == "002827"
    assert row["name"] == "高争民爆"
    assert row["day_chg_pct"] == 0.10
    assert row["board_count"] == 2
    assert row["status"] == "sealed"


def test_merge_prefers_sealed():
    sealed = [
        normalize_pool_row(
            {"代码": "000001", "名称": "平安银行", "涨跌幅": 10, "连板数": 1},
            status="sealed",
        )
    ]
    broken = [
        normalize_pool_row(
            {"代码": "000001", "名称": "平安银行", "涨跌幅": 5, "涨停统计": "2/1"},
            status="broken",
        ),
        normalize_pool_row(
            {"代码": "000428", "名称": "华天酒店", "涨跌幅": -5, "涨停统计": "4/3"},
            status="broken",
        ),
    ]
    today = merge_today_rows(sealed, broken)
    by_sym = {r["symbol"]: r for r in today}
    assert by_sym["000001"]["status"] == "sealed"
    assert by_sym["000001"]["board_count"] == 1
    assert by_sym["000428"]["status"] == "broken"


def test_ladder_groups_descending():
    today = [
        {
            "symbol": "a",
            "name": "A",
            "day_chg_pct": 0.2,
            "board_count": 3,
            "status": "sealed",
            "limit_up_price": None,
        },
        {
            "symbol": "b",
            "name": "B",
            "day_chg_pct": 0.1,
            "board_count": 1,
            "status": "sealed",
            "limit_up_price": None,
        },
        {
            "symbol": "c",
            "name": "C",
            "day_chg_pct": 0.2,
            "board_count": 3,
            "status": "sealed",
            "limit_up_price": None,
        },
        {
            "symbol": "d",
            "name": "D",
            "day_chg_pct": 0.05,
            "board_count": 2,
            "status": "broken",
            "limit_up_price": None,
        },
    ]
    ladder = build_ladder(today)
    assert [x["board_count"] for x in ladder] == [3, 1]
    assert {i["symbol"] for i in ladder[0]["items"]} == {"a", "c"}
    assert ladder[1]["items"][0]["symbol"] == "b"


def test_parse_flow_num():
    assert parse_flow_num(30412008.0) == 30412008.0
    assert parse_flow_num("-") is None
    assert parse_flow_num(None) is None
    assert parse_flow_num("") is None


def test_apply_fund_flow_merges_into_today_and_ladder():
    today = [
        {
            "symbol": "000593",
            "name": "德龙汇能",
            "day_chg_pct": 0.1,
            "board_count": 1,
            "status": "sealed",
            "limit_up_price": None,
        }
    ]
    flow = {
        "000593": {
            "main_inflow": 58_759_776.0,
            "main_outflow": 28_347_768.0,
            "main_net_inflow": 30_412_008.0,
        }
    }
    apply_fund_flow(today, flow)
    assert today[0]["main_inflow"] == 58_759_776.0
    assert today[0]["main_outflow"] == 28_347_768.0
    assert today[0]["main_net_inflow"] == 30_412_008.0
    ladder = build_ladder(today)
    assert ladder[0]["items"][0]["main_net_inflow"] == 30_412_008.0


def test_enrich_fund_flow_maps_ulist_and_stock_get(monkeypatch):
    lim._flow_cache["ts"] = 0.0
    lim._flow_cache["by_symbol"] = {}

    def fake_ulist(symbols):
        return {"000593": {"main_net_inflow": 30_412_008.0}}

    def fake_stock(symbol):
        if symbol == "000593":
            return {
                "main_inflow": 58_759_776.0,
                "main_outflow": 28_347_768.0,
                "main_net_inflow": 30_412_008.0,
            }
        raise RuntimeError("boom")

    monkeypatch.setattr(lim, "_fetch_ulist_net", fake_ulist)
    monkeypatch.setattr(lim, "_fetch_stock_flow", fake_stock)

    out = enrich_fund_flow(["000593", "999999"], force=True)
    assert out["000593"]["main_inflow"] == 58_759_776.0
    assert out["000593"]["main_outflow"] == 28_347_768.0
    assert out["000593"]["main_net_inflow"] == 30_412_008.0
    # stock/get 失败时仍保留 ulist 净流入
    assert out["999999"]["main_net_inflow"] is None
    assert out["999999"]["main_inflow"] is None


def test_enrich_fund_flow_keeps_net_when_stock_fails(monkeypatch):
    lim._flow_cache["ts"] = 0.0
    lim._flow_cache["by_symbol"] = {}

    monkeypatch.setattr(
        lim,
        "_fetch_ulist_net",
        lambda symbols: {"000001": {"main_net_inflow": 1e6}},
    )
    monkeypatch.setattr(
        lim,
        "_fetch_stock_flow",
        lambda symbol: (_ for _ in ()).throw(RuntimeError("x")),
    )
    out = enrich_fund_flow(["000001"], force=True)
    assert out["000001"]["main_net_inflow"] == 1e6
    assert out["000001"]["main_inflow"] is None
    assert out["000001"]["main_outflow"] is None


def test_get_limit_up_force_bypasses_cache(monkeypatch):
    lim._cache["ts"] = 0.0
    lim._cache["payload"] = None
    calls = {"n": 0}

    monkeypatch.setattr(
        lim,
        "_fetch_pools",
        lambda date_yyyymmdd: (
            [{"代码": "000001", "名称": "平安银行", "涨跌幅": 10, "连板数": 1}],
            [],
        ),
    )
    monkeypatch.setattr(
        lim,
        "enrich_fund_flow",
        lambda symbols, force=False: {},
    )

    def fake_session():
        return {"is_trading": False, "is_trading_day": True}

    monkeypatch.setattr("app.quote.trading_session", fake_session)
    monkeypatch.setattr(lim, "_pool_date_yyyymmdd", lambda: "20260803")

    first = lim.get_limit_up()
    calls["n"] = 0

    def counting_fetch(date_yyyymmdd):
        calls["n"] += 1
        return (
            [{"代码": "000002", "名称": "万科A", "涨跌幅": 10, "连板数": 1}],
            [],
        )

    monkeypatch.setattr(lim, "_fetch_pools", counting_fetch)
    cached = lim.get_limit_up(force=False)
    assert calls["n"] == 0
    assert cached["as_of"] == first["as_of"]

    forced = lim.get_limit_up(force=True)
    assert calls["n"] == 1
    assert forced["today"][0]["symbol"] == "000002"
