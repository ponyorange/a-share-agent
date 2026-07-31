from __future__ import annotations

from app.limitup import build_ladder, merge_today_rows, normalize_pool_row


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
