from __future__ import annotations

import pandas as pd
import pytest

from app import fund as fund_mod


@pytest.fixture(autouse=True)
def _clear_cache():
    fund_mod.clear_fund_name_cache()
    yield
    fund_mod.clear_fund_name_cache()


def _names_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "基金代码": "025857",
                "拼音缩写": "HXZZDWSBZTETFFQSLJC",
                "基金简称": "华夏中证电网设备主题ETF发起式联接C",
                "基金类型": "指数型-股票",
                "拼音全称": "HUAXIA...",
            },
            {
                "基金代码": "000001",
                "拼音缩写": "HXCZHH",
                "基金简称": "华夏成长混合",
                "基金类型": "混合型-灵活",
                "拼音全称": "HUAXIACHENGZHANGHUNHE",
            },
        ]
    )


def test_search_by_code_prefix(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_name_em", _names_df)
    items = fund_mod.search_funds("025", limit=20)
    assert len(items) == 1
    assert items[0]["symbol"] == "025857"
    assert items[0]["name"].startswith("华夏中证电网")
    assert items[0]["pinyin"] == "HXZZDWSBZTETFFQSLJC"


def test_search_by_name_substring(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_name_em", _names_df)
    items = fund_mod.search_funds("电网", limit=20)
    assert [i["symbol"] for i in items] == ["025857"]


def test_search_by_pinyin_prefix_case_insensitive(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_name_em", _names_df)
    items = fund_mod.search_funds("hxzz", limit=20)
    assert [i["symbol"] for i in items] == ["025857"]


def test_search_empty_q_returns_empty(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_name_em", _names_df)
    assert fund_mod.search_funds("  ", limit=20) == []
    assert fund_mod.search_funds("", limit=20) == []


def test_search_respects_limit(monkeypatch):
    rows = [
        {
            "基金代码": f"{i:06d}",
            "拼音缩写": f"P{i}",
            "基金简称": f"测试基金{i}",
            "基金类型": "混合型",
            "拼音全称": f"CESHI{i}",
        }
        for i in range(5)
    ]
    monkeypatch.setattr(
        fund_mod.ak, "fund_name_em", lambda: pd.DataFrame(rows)
    )
    items = fund_mod.search_funds("测试", limit=2)
    assert len(items) == 2


def _overview_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "基金全称": "华夏中证电网设备主题交易型开放式指数证券投资基金发起式联接基金",
                "基金简称": "华夏中证电网设备主题ETF发起式联接C",
                "基金代码": "025857（前端）",
                "基金类型": "指数型-股票",
                "发行日期": "2025年10月27日",
                "成立日期/规模": "2025年11月25日 / 4.451亿份",
                "净资产规模": "85.78亿元（截止至：2026年06月30日）",
                "份额规模": "59.9097亿份（截止至：2026年06月30日）",
                "基金管理人": "华夏基金",
                "基金托管人": "招商证券",
                "基金经理人": "单宽之",
                "成立来分红": "每份累计0.00元（0次）",
                "管理费率": "0.50%（每年）",
                "托管费率": "0.10%（每年）",
                "销售服务费率": "0.30%（每年）",
                "最高认购费率": "0.00%（前端）",
                "最高申购费率": "0.00%（前端）",
                "最高赎回费率": "1.50%（前端）",
                "业绩比较基准": "中证电网设备主题指数收益率*95%+人民币活期存款税后利率*5%",
                "跟踪标的": "中证电网设备主题指数",
            }
        ]
    )


def _nav_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"净值日期": "2026-07-22", "单位净值": 1.0960, "日增长率": -2.07},
            {"净值日期": "2026-07-23", "单位净值": 1.1474, "日增长率": 4.69},
            {"净值日期": "2026-07-24", "单位净值": 1.1087, "日增长率": -3.37},
        ]
    )


def test_get_fund_detail_maps_overview_and_nav(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_overview_em", lambda symbol: _overview_df())
    monkeypatch.setattr(
        fund_mod.ak,
        "fund_open_fund_info_em",
        lambda symbol, indicator="单位净值走势", period="成立来": _nav_df(),
    )
    out = fund_mod.get_fund_detail("025857")
    assert out["symbol"] == "025857"
    assert out["name"] == "华夏中证电网设备主题ETF发起式联接C"
    assert out["overview"]["company"] == "华夏基金"
    assert out["overview"]["fees"]["management"] == "0.50%（每年）"
    assert out["nav"]["latest"]["date"] == "2026-07-24"
    assert out["nav"]["latest"]["nav"] == 1.1087
    assert len(out["nav"]["series"]) == 3


def test_get_fund_detail_invalid_symbol():
    with pytest.raises(ValueError):
        fund_mod.get_fund_detail("25857")
    with pytest.raises(ValueError):
        fund_mod.get_fund_detail("abc")


def test_get_fund_detail_empty_raises_lookup(monkeypatch):
    monkeypatch.setattr(
        fund_mod.ak, "fund_overview_em", lambda symbol: pd.DataFrame()
    )

    def _boom(**_kwargs):
        raise RuntimeError("upstream")

    monkeypatch.setattr(fund_mod.ak, "fund_open_fund_info_em", _boom)
    with pytest.raises(LookupError):
        fund_mod.get_fund_detail("025857")


def test_get_fund_detail_nav_failure_degrades(monkeypatch):
    monkeypatch.setattr(fund_mod.ak, "fund_overview_em", lambda symbol: _overview_df())

    def _boom(**_kwargs):
        raise RuntimeError("nav down")

    monkeypatch.setattr(fund_mod.ak, "fund_open_fund_info_em", _boom)
    out = fund_mod.get_fund_detail("025857")
    assert out["overview"]["manager"] == "单宽之"
    assert out["nav"] is None
    assert "nav_error" in out and out["nav_error"]


def test_provider_fund_wrappers(monkeypatch):
    from app.providers.akshare_provider import AkshareProvider

    monkeypatch.setattr(
        fund_mod,
        "search_funds",
        lambda q, limit=20: [
            {"symbol": "025857", "name": "x", "type": "t", "pinyin": "p"}
        ],
    )
    monkeypatch.setattr(
        fund_mod,
        "get_fund_detail",
        lambda symbol: {
            "symbol": symbol,
            "name": "n",
            "overview": {},
            "nav": None,
            "nav_error": "x",
        },
    )
    p = AkshareProvider()
    assert "fund" in p.features
    assert p.get_fund_search("025", 10)["items"][0]["symbol"] == "025857"
    assert p.get_fund_detail("025857")["source"] == "akshare"
