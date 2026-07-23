"""Curated Tushare Pro API catalog (see https://tushare.pro/document/2)."""

from __future__ import annotations

from typing import Any

CATEGORY_LABELS: dict[str, str] = {
    "stock_basic": "股票·基础",
    "stock_quote": "股票·行情",
    "stock_fina": "股票·财务",
    "stock_ref": "股票·参考",
    "stock_flow": "股票·资金流向",
    "stock_board": "股票·打板专题",
    "etf": "ETF",
    "index": "指数",
    "fund": "公募基金",
    "futures": "期货",
    "option": "期权",
    "bond": "债券",
    "fx": "外汇",
    "hk": "港股",
    "us": "美股",
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


# name, category, doc, params, example_params
_RAW: list[tuple[str, str, str, list[dict[str, Any]], dict[str, Any]]] = [
    # —— 基础 ——
    (
        "stock_basic",
        "stock_basic",
        "股票列表",
        [
            _p("ts_code"),
            _p("name"),
            _p("exchange"),
            _p("market"),
            _p("list_status", default="L"),
            _p("is_hs"),
        ],
        {"list_status": "L", "exchange": "SSE"},
    ),
    (
        "trade_cal",
        "stock_basic",
        "交易日历",
        [
            _p("exchange", default="SSE"),
            _p("start_date"),
            _p("end_date"),
            _p("is_open"),
        ],
        {"exchange": "SSE", "start_date": "20260101", "end_date": "20260717"},
    ),
    (
        "namechange",
        "stock_basic",
        "股票曾用名",
        [_p("ts_code"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH"},
    ),
    (
        "stock_company",
        "stock_basic",
        "上市公司基本信息",
        [_p("ts_code"), _p("exchange")],
        {"ts_code": "600519.SH"},
    ),
    (
        "new_share",
        "stock_basic",
        "IPO新股上市",
        [_p("start_date"), _p("end_date")],
        {"start_date": "20260101", "end_date": "20260717"},
    ),
    (
        "stk_managers",
        "stock_basic",
        "上市公司管理层",
        [_p("ts_code"), _p("ann_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH"},
    ),
    # —— 行情 ——
    (
        "daily",
        "stock_quote",
        "历史日线",
        [
            _p("ts_code"),
            _p("trade_date"),
            _p("start_date"),
            _p("end_date"),
        ],
        {"ts_code": "600519.SH", "start_date": "20260101", "end_date": "20260717"},
    ),
    (
        "weekly",
        "stock_quote",
        "周线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH", "start_date": "20250101", "end_date": "20260717"},
    ),
    (
        "monthly",
        "stock_quote",
        "月线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20260717"},
    ),
    (
        "adj_factor",
        "stock_quote",
        "复权因子",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH", "start_date": "20260101", "end_date": "20260717"},
    ),
    (
        "daily_basic",
        "stock_quote",
        "每日指标",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH", "start_date": "20260701", "end_date": "20260717"},
    ),
    (
        "stk_limit",
        "stock_quote",
        "每日涨跌停价格",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"trade_date": "20260717"},
    ),
    (
        "suspend_d",
        "stock_quote",
        "每日停复牌信息",
        [
            _p("ts_code"),
            _p("trade_date"),
            _p("start_date"),
            _p("end_date"),
            _p("suspend_type"),
        ],
        {"trade_date": "20260717"},
    ),
    (
        "pro_bar",
        "stock_quote",
        "通用行情（复权日线等，经 tushare.pro_bar）",
        [
            _p("ts_code", required=True),
            _p("start_date"),
            _p("end_date"),
            _p("asset", default="E"),
            _p("adj", default="qfq"),
            _p("freq", default="D"),
        ],
        {
            "ts_code": "600519.SH",
            "start_date": "20260101",
            "end_date": "20260717",
            "adj": "qfq",
            "freq": "D",
        },
    ),
    # —— 财务 ——
    (
        "income",
        "stock_fina",
        "利润表",
        [
            _p("ts_code"),
            _p("ann_date"),
            _p("start_date"),
            _p("end_date"),
            _p("period"),
            _p("report_type"),
        ],
        {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20260717"},
    ),
    (
        "balancesheet",
        "stock_fina",
        "资产负债表",
        [
            _p("ts_code"),
            _p("ann_date"),
            _p("start_date"),
            _p("end_date"),
            _p("period"),
            _p("report_type"),
        ],
        {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20260717"},
    ),
    (
        "cashflow",
        "stock_fina",
        "现金流量表",
        [
            _p("ts_code"),
            _p("ann_date"),
            _p("start_date"),
            _p("end_date"),
            _p("period"),
            _p("report_type"),
        ],
        {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20260717"},
    ),
    (
        "forecast",
        "stock_fina",
        "业绩预告",
        [_p("ts_code"), _p("ann_date"), _p("start_date"), _p("end_date"), _p("period")],
        {"ann_date": "20260701"},
    ),
    (
        "express",
        "stock_fina",
        "业绩快报",
        [_p("ts_code"), _p("ann_date"), _p("start_date"), _p("end_date"), _p("period")],
        {"ts_code": "600519.SH"},
    ),
    (
        "fina_indicator",
        "stock_fina",
        "财务指标数据",
        [_p("ts_code"), _p("ann_date"), _p("start_date"), _p("end_date"), _p("period")],
        {"ts_code": "600519.SH", "start_date": "20240101", "end_date": "20260717"},
    ),
    (
        "dividend",
        "stock_fina",
        "分红送股数据",
        [_p("ts_code"), _p("ann_date"), _p("record_date"), _p("ex_date"), _p("imp_ann_date")],
        {"ts_code": "600519.SH"},
    ),
    (
        "fina_mainbz",
        "stock_fina",
        "主营业务构成",
        [_p("ts_code"), _p("period"), _p("type"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH", "type": "P"},
    ),
    # —— 参考 ——
    (
        "top10_holders",
        "stock_ref",
        "前十大股东",
        [_p("ts_code"), _p("period"), _p("ann_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH"},
    ),
    (
        "top10_floatholders",
        "stock_ref",
        "前十大流通股东",
        [_p("ts_code"), _p("period"), _p("ann_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH"},
    ),
    (
        "pledge_stat",
        "stock_ref",
        "股权质押统计",
        [_p("ts_code"), _p("end_date")],
        {"ts_code": "600519.SH"},
    ),
    (
        "repurchase",
        "stock_ref",
        "股票回购",
        [_p("ann_date"), _p("start_date"), _p("end_date")],
        {"start_date": "20260101", "end_date": "20260717"},
    ),
    (
        "share_float",
        "stock_ref",
        "限售股解禁",
        [_p("ts_code"), _p("ann_date"), _p("float_date"), _p("start_date"), _p("end_date")],
        {"start_date": "20260701", "end_date": "20260717"},
    ),
    (
        "block_trade",
        "stock_ref",
        "大宗交易",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"trade_date": "20260717"},
    ),
    (
        "stk_holdernumber",
        "stock_ref",
        "股东人数",
        [_p("ts_code"), _p("enddate"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH"},
    ),
    # —— 资金流向 ——
    (
        "moneyflow",
        "stock_flow",
        "个股资金流向",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "600519.SH", "start_date": "20260701", "end_date": "20260717"},
    ),
    (
        "moneyflow_hsgt",
        "stock_flow",
        "沪深港通资金流向",
        [_p("trade_date"), _p("start_date"), _p("end_date")],
        {"start_date": "20260701", "end_date": "20260717"},
    ),
    (
        "hsgt_top10",
        "stock_flow",
        "沪深股通十大成交股",
        [_p("trade_date"), _p("market_type")],
        {"trade_date": "20260717"},
    ),
    # —— 打板 ——
    (
        "top_list",
        "stock_board",
        "龙虎榜每日统计",
        [_p("trade_date"), _p("ts_code")],
        {"trade_date": "20260717"},
    ),
    (
        "top_inst",
        "stock_board",
        "龙虎榜机构交易",
        [_p("trade_date"), _p("ts_code")],
        {"trade_date": "20260717"},
    ),
    (
        "limit_list_d",
        "stock_board",
        "涨跌停和炸板数据",
        [
            _p("trade_date"),
            _p("ts_code"),
            _p("limit_type"),
            _p("exchange"),
            _p("start_date"),
            _p("end_date"),
        ],
        {"trade_date": "20260717"},
    ),
    # —— ETF ——
    (
        "etf_basic",
        "etf",
        "ETF基本信息",
        [_p("ts_code"), _p("list_status", default="L"), _p("exchange")],
        {"list_status": "L"},
    ),
    (
        "fund_daily",
        "etf",
        "ETF/基金日线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "510300.SH", "start_date": "20260101", "end_date": "20260717"},
    ),
    # —— 指数 ——
    (
        "index_basic",
        "index",
        "指数基本信息",
        [_p("ts_code"), _p("name"), _p("market"), _p("publisher"), _p("category")],
        {"market": "SSE"},
    ),
    (
        "index_daily",
        "index",
        "指数日线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "000001.SH", "start_date": "20260101", "end_date": "20260717"},
    ),
    (
        "index_weekly",
        "index",
        "指数周线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "000001.SH", "start_date": "20250101", "end_date": "20260717"},
    ),
    (
        "index_monthly",
        "index",
        "指数月线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "000001.SH", "start_date": "20240101", "end_date": "20260717"},
    ),
    (
        "index_weight",
        "index",
        "指数成分和权重",
        [_p("index_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"index_code": "000300.SH", "start_date": "20260701", "end_date": "20260717"},
    ),
    (
        "index_dailybasic",
        "index",
        "大盘指数每日指标",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "000001.SH", "start_date": "20260701", "end_date": "20260717"},
    ),
    # —— 公募 ——
    (
        "fund_basic",
        "fund",
        "基金列表",
        [_p("ts_code"), _p("market"), _p("status")],
        {"market": "E"},
    ),
    (
        "fund_nav",
        "fund",
        "基金净值",
        [_p("ts_code"), _p("nav_date"), _p("start_date"), _p("end_date"), _p("market")],
        {"ts_code": "000001.OF", "start_date": "20260101", "end_date": "20260717"},
    ),
    (
        "fund_portfolio",
        "fund",
        "基金持仓",
        [_p("ts_code"), _p("symbol"), _p("ann_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "000001.OF"},
    ),
    # —— 期货 ——
    (
        "fut_basic",
        "futures",
        "期货合约信息",
        [_p("exchange"), _p("fut_type"), _p("fut_code")],
        {"exchange": "DCE"},
    ),
    (
        "fut_daily",
        "futures",
        "期货日线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date"), _p("exchange")],
        {"ts_code": "M2509.DCE", "start_date": "20260701", "end_date": "20260717"},
    ),
    # —— 期权 ——
    (
        "opt_basic",
        "option",
        "期权合约信息",
        [_p("ts_code"), _p("exchange"), _p("opt_code"), _p("call_put")],
        {"exchange": "SSE"},
    ),
    (
        "opt_daily",
        "option",
        "期权日线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date"), _p("exchange")],
        {"trade_date": "20260717", "exchange": "SSE"},
    ),
    # —— 债券 / 可转债 ——
    (
        "cb_basic",
        "bond",
        "可转债基本信息",
        [_p("ts_code"), _p("list_date"), _p("exchange")],
        {},
    ),
    (
        "cb_daily",
        "bond",
        "可转债行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"start_date": "20260701", "end_date": "20260717"},
    ),
    # —— 港股 ——
    (
        "hk_basic",
        "hk",
        "港股基础信息",
        [_p("ts_code"), _p("list_status")],
        {"list_status": "L"},
    ),
    (
        "hk_daily",
        "hk",
        "港股日线行情",
        [_p("ts_code"), _p("trade_date"), _p("start_date"), _p("end_date")],
        {"ts_code": "00700.HK", "start_date": "20260101", "end_date": "20260717"},
    ),
    # —— 宏观 ——
    (
        "cn_gdp",
        "macro",
        "国内生产总值（GDP）",
        [_p("q"), _p("start_q"), _p("end_q")],
        {},
    ),
    (
        "cn_cpi",
        "macro",
        "居民消费价格指数（CPI）",
        [_p("m"), _p("start_m"), _p("end_m")],
        {"start_m": "202401", "end_m": "202606"},
    ),
    (
        "cn_ppi",
        "macro",
        "工业生产者出厂价格指数（PPI）",
        [_p("m"), _p("start_m"), _p("end_m")],
        {"start_m": "202401", "end_m": "202606"},
    ),
    (
        "cn_m",
        "macro",
        "货币供应量（月）",
        [_p("m"), _p("start_m"), _p("end_m")],
        {"start_m": "202401", "end_m": "202606"},
    ),
    (
        "shibor",
        "macro",
        "Shibor利率",
        [_p("date"), _p("start_date"), _p("end_date")],
        {"start_date": "20260701", "end_date": "20260717"},
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
                    "Tushare Pro 接口，代码规范见文档：股票用 ts_code（如 600519.SH）。\n"
                    "https://tushare.pro/document/2"
                ),
                "params": params,
                "example_params": example,
            }
        )
    items.sort(key=lambda x: (x["category"], x["name"]))
    return items
