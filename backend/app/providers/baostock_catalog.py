"""Curated BaoStock API catalog (http://baostock.com)."""

from __future__ import annotations

from typing import Any

CATEGORY_LABELS: dict[str, str] = {
    "basic": "基础信息",
    "quote": "行情 K 线",
    "index": "指数成分",
    "finance": "财务数据",
    "report": "公司报告",
    "macro": "宏观经济",
}


def _p(
    name: str,
    required: bool = False,
    default: Any = None,
    annotation: str = "str",
) -> dict[str, Any]:
    return {
        "name": name,
        "required": required,
        "default": default,
        "annotation": annotation,
    }


_DEFAULT_K_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,"
    "adjustflag,turn,tradestatus,pctChg,isST"
)

# name, category, doc, params, example_params
_RAW: list[tuple[str, str, str, list[dict[str, Any]], dict[str, Any]]] = [
    (
        "query_history_k_data_plus",
        "quote",
        "历史 K 线（日/周/月/分钟，可复权）",
        [
            _p("code", required=True),
            _p("fields", default=_DEFAULT_K_FIELDS),
            _p("start_date"),
            _p("end_date"),
            _p("frequency", default="d"),
            _p("adjustflag", default="2"),
        ],
        {
            "code": "sh.600519",
            "fields": "date,code,open,high,low,close,volume,amount,pctChg",
            "start_date": "2026-01-01",
            "end_date": "2026-07-17",
            "frequency": "d",
            "adjustflag": "2",
        },
    ),
    (
        "query_adjust_factor",
        "quote",
        "复权因子",
        [_p("code", required=True), _p("start_date"), _p("end_date")],
        {"code": "sh.600519", "start_date": "2026-01-01", "end_date": "2026-07-17"},
    ),
    (
        "query_daily_adjust_factor",
        "quote",
        "某日全部复权因子",
        [_p("date")],
        {"date": "2026-07-17"},
    ),
    (
        "query_trade_dates",
        "basic",
        "交易日历",
        [_p("start_date"), _p("end_date")],
        {"start_date": "2026-07-01", "end_date": "2026-07-17"},
    ),
    (
        "query_all_stock",
        "basic",
        "指定交易日证券列表",
        [_p("day")],
        {"day": "2026-07-17"},
    ),
    (
        "query_stock_basic",
        "basic",
        "证券基本资料",
        [_p("code"), _p("code_name")],
        {"code": "sh.600519"},
    ),
    (
        "query_stock_industry",
        "basic",
        "行业分类",
        [_p("code"), _p("date")],
        {"code": "sh.600519"},
    ),
    (
        "query_hs300_stocks",
        "index",
        "沪深300成分股",
        [_p("date")],
        {"date": "2026-07-17"},
    ),
    (
        "query_sz50_stocks",
        "index",
        "上证50成分股",
        [_p("date")],
        {"date": "2026-07-17"},
    ),
    (
        "query_zz500_stocks",
        "index",
        "中证500成分股",
        [_p("date")],
        {"date": "2026-07-17"},
    ),
    (
        "query_profit_data",
        "finance",
        "季频盈利能力",
        [
            _p("code", required=True),
            _p("year", annotation="int"),
            _p("quarter", annotation="int"),
        ],
        {"code": "sh.600519", "year": 2025, "quarter": 4},
    ),
    (
        "query_operation_data",
        "finance",
        "季频营运能力",
        [
            _p("code", required=True),
            _p("year", annotation="int"),
            _p("quarter", annotation="int"),
        ],
        {"code": "sh.600519", "year": 2025, "quarter": 4},
    ),
    (
        "query_growth_data",
        "finance",
        "季频成长能力",
        [
            _p("code", required=True),
            _p("year", annotation="int"),
            _p("quarter", annotation="int"),
        ],
        {"code": "sh.600519", "year": 2025, "quarter": 4},
    ),
    (
        "query_balance_data",
        "finance",
        "季频偿债能力",
        [
            _p("code", required=True),
            _p("year", annotation="int"),
            _p("quarter", annotation="int"),
        ],
        {"code": "sh.600519", "year": 2025, "quarter": 4},
    ),
    (
        "query_cash_flow_data",
        "finance",
        "季频现金流量",
        [
            _p("code", required=True),
            _p("year", annotation="int"),
            _p("quarter", annotation="int"),
        ],
        {"code": "sh.600519", "year": 2025, "quarter": 4},
    ),
    (
        "query_dupont_data",
        "finance",
        "季频杜邦指标",
        [
            _p("code", required=True),
            _p("year", annotation="int"),
            _p("quarter", annotation="int"),
        ],
        {"code": "sh.600519", "year": 2025, "quarter": 4},
    ),
    (
        "query_dividend_data",
        "finance",
        "分红数据",
        [
            _p("code", required=True),
            _p("year", annotation="int"),
            _p("yearType", default="report"),
        ],
        {"code": "sh.600519", "year": 2025, "yearType": "report"},
    ),
    (
        "query_forecast_report",
        "report",
        "业绩预告",
        [_p("code", required=True), _p("start_date"), _p("end_date")],
        {"code": "sh.600519", "start_date": "2025-01-01", "end_date": "2026-07-17"},
    ),
    (
        "query_performance_express_report",
        "report",
        "业绩快报",
        [_p("code", required=True), _p("start_date"), _p("end_date")],
        {"code": "sh.600519", "start_date": "2025-01-01", "end_date": "2026-07-17"},
    ),
    (
        "query_deposit_rate_data",
        "macro",
        "存款利率",
        [_p("start_date"), _p("end_date")],
        {"start_date": "2024-01-01", "end_date": "2026-07-17"},
    ),
    (
        "query_loan_rate_data",
        "macro",
        "贷款利率",
        [_p("start_date"), _p("end_date")],
        {"start_date": "2024-01-01", "end_date": "2026-07-17"},
    ),
    (
        "query_required_reserve_ratio_data",
        "macro",
        "存款准备金率",
        [_p("start_date"), _p("end_date"), _p("yearType", default="0")],
        {"start_date": "2020-01-01", "end_date": "2026-07-17", "yearType": "0"},
    ),
    (
        "query_money_supply_data_month",
        "macro",
        "货币供应量（月）",
        [_p("start_date"), _p("end_date")],
        {"start_date": "2024-01", "end_date": "2026-06"},
    ),
    (
        "query_money_supply_data_year",
        "macro",
        "货币供应量（年）",
        [_p("start_date"), _p("end_date")],
        {"start_date": "2018", "end_date": "2025"},
    ),
]


def build_catalog() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, category, doc, params, example in _RAW:
        items.append(
            {
                "name": name,
                "category": category,
                "category_label": CATEGORY_LABELS.get(category, category),
                "doc": doc,
                "docstring": (
                    f"{doc}\n\n"
                    "BaoStock 免费 A 股数据。证券代码形如 sh.600519 / sz.000001。\n"
                    "K 线 frequency: d/w/m/5/15/30/60；adjustflag: 1后复权 2前复权 3不复权。\n"
                    "http://baostock.com/baostock/index.php"
                ),
                "params": params,
                "example_params": example,
            }
        )
    items.sort(key=lambda x: (x["category"], x["name"]))
    return items
