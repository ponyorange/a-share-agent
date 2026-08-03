"""Curated yfinance API catalog (https://github.com/ranaroussi/yfinance)."""

from __future__ import annotations

from typing import Any

CATEGORY_LABELS: dict[str, str] = {
    "ticker": "Ticker·个股",
    "download": "批量下载",
    "search": "搜索",
    "screener": "选股/榜单",
    "multi": "多标的",
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


_SYM = _p("symbol", required=True)
_AAPL = {"symbol": "AAPL"}


# name, category, doc, params, example_params
_RAW: list[tuple[str, str, str, list[dict[str, Any]], dict[str, Any]]] = [
    # —— Ticker properties / methods ——
    ("ticker_info", "ticker", "公司/标的 info 字典", [_SYM], _AAPL),
    ("ticker_fast_info", "ticker", "快速行情 fast_info", [_SYM], _AAPL),
    (
        "ticker_history",
        "ticker",
        "历史行情 history",
        [
            _SYM,
            _p("period", default="1mo"),
            _p("interval", default="1d"),
            _p("start"),
            _p("end"),
            _p("auto_adjust", default="true", annotation="bool"),
            _p("actions", default="true", annotation="bool"),
        ],
        {"symbol": "AAPL", "period": "1mo", "interval": "1d"},
    ),
    (
        "ticker_history_metadata",
        "ticker",
        "history 元数据",
        [_SYM],
        _AAPL,
    ),
    ("ticker_actions", "ticker", "分红与拆分 actions", [_SYM], _AAPL),
    ("ticker_dividends", "ticker", "分红", [_SYM], _AAPL),
    ("ticker_splits", "ticker", "拆分", [_SYM], _AAPL),
    ("ticker_capital_gains", "ticker", "资本利得（基金等）", [_SYM], {"symbol": "VTSAX"}),
    ("ticker_shares", "ticker", "流通股本 shares", [_SYM], _AAPL),
    (
        "ticker_get_shares_full",
        "ticker",
        "完整股本序列 get_shares_full",
        [_SYM, _p("start"), _p("end")],
        _AAPL,
    ),
    ("ticker_calendar", "ticker", "财报日历", [_SYM], _AAPL),
    ("ticker_sec_filings", "ticker", "SEC 文件", [_SYM], _AAPL),
    ("ticker_recommendations", "ticker", "分析师评级", [_SYM], _AAPL),
    (
        "ticker_recommendations_summary",
        "ticker",
        "评级摘要",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_analyst_price_targets",
        "ticker",
        "分析师目标价",
        [_SYM],
        _AAPL,
    ),
    ("ticker_earnings", "ticker", "盈利 earnings", [_SYM], _AAPL),
    ("ticker_earnings_dates", "ticker", "盈利发布日", [_SYM], _AAPL),
    (
        "ticker_earnings_history",
        "ticker",
        "历史盈利",
        [_SYM],
        _AAPL,
    ),
    ("ticker_income_stmt", "ticker", "利润表（年）", [_SYM], _AAPL),
    (
        "ticker_quarterly_income_stmt",
        "ticker",
        "利润表（季）",
        [_SYM],
        _AAPL,
    ),
    ("ticker_balance_sheet", "ticker", "资产负债表（年）", [_SYM], _AAPL),
    (
        "ticker_quarterly_balance_sheet",
        "ticker",
        "资产负债表（季）",
        [_SYM],
        _AAPL,
    ),
    ("ticker_cashflow", "ticker", "现金流量表（年）", [_SYM], _AAPL),
    (
        "ticker_quarterly_cashflow",
        "ticker",
        "现金流量表（季）",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_ttm_income_stmt",
        "ticker",
        "利润表 TTM",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_ttm_cashflow",
        "ticker",
        "现金流量表 TTM",
        [_SYM],
        _AAPL,
    ),
    ("ticker_major_holders", "ticker", "主要股东", [_SYM], _AAPL),
    (
        "ticker_institutional_holders",
        "ticker",
        "机构持股",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_mutualfund_holders",
        "ticker",
        "共同基金持股",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_insider_transactions",
        "ticker",
        "内部人交易",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_insider_purchases",
        "ticker",
        "内部人买入",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_insider_roster_holders",
        "ticker",
        "内部人名单",
        [_SYM],
        _AAPL,
    ),
    ("ticker_sustainability", "ticker", "ESG / 可持续", [_SYM], _AAPL),
    ("ticker_news", "ticker", "相关新闻", [_SYM], _AAPL),
    ("ticker_options", "ticker", "期权到期日列表", [_SYM], _AAPL),
    (
        "ticker_option_chain",
        "ticker",
        "期权链（需 date）",
        [_SYM, _p("date", required=True)],
        {"symbol": "AAPL", "date": "2026-08-21"},
    ),
    (
        "ticker_quarterly_earnings",
        "ticker",
        "季度盈利（旧接口兼容）",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_financials",
        "ticker",
        "财务 financials（年）",
        [_SYM],
        _AAPL,
    ),
    (
        "ticker_quarterly_financials",
        "ticker",
        "财务 financials（季）",
        [_SYM],
        _AAPL,
    ),
    # —— download ——
    (
        "download",
        "download",
        "yf.download 批量历史行情",
        [
            _p("tickers", required=True),
            _p("period", default="1mo"),
            _p("interval", default="1d"),
            _p("start"),
            _p("end"),
            _p("group_by", default="ticker"),
            _p("auto_adjust", default="true", annotation="bool"),
            _p("threads", default="true", annotation="bool"),
        ],
        {"tickers": "AAPL MSFT", "period": "1mo", "interval": "1d"},
    ),
    # —— search ——
    (
        "search",
        "search",
        "Search 关键词搜索",
        [
            _p("query", required=True),
            _p("max_results", default="8", annotation="int"),
            _p("news_count", default="5", annotation="int"),
            _p("lists_count", default="0", annotation="int"),
        ],
        {"query": "Apple", "max_results": "8"},
    ),
    (
        "search_quotes",
        "search",
        "Search.quotes 报价结果",
        [
            _p("query", required=True),
            _p("max_results", default="8", annotation="int"),
        ],
        {"query": "Tesla", "max_results": "8"},
    ),
    (
        "search_news",
        "search",
        "Search.news 新闻结果",
        [
            _p("query", required=True),
            _p("news_count", default="8", annotation="int"),
        ],
        {"query": "NVIDIA", "news_count": "8"},
    ),
    # —— screener ——
    (
        "screener_day_gainers",
        "screener",
        "日涨幅榜 day_gainers",
        [_p("count", default="25", annotation="int")],
        {"count": "25"},
    ),
    (
        "screener_day_losers",
        "screener",
        "日跌幅榜 day_losers",
        [_p("count", default="25", annotation="int")],
        {"count": "25"},
    ),
    (
        "screener_most_actives",
        "screener",
        "最活跃 most_actives",
        [_p("count", default="25", annotation="int")],
        {"count": "25"},
    ),
    (
        "screener_growth_technology_stocks",
        "screener",
        "成长科技股榜",
        [_p("count", default="25", annotation="int")],
        {"count": "25"},
    ),
    (
        "screener_undervalued_large_caps",
        "screener",
        "低估值大盘股",
        [_p("count", default="25", annotation="int")],
        {"count": "25"},
    ),
    (
        "screener_aggressive_small_caps",
        "screener",
        "进取型小盘股",
        [_p("count", default="25", annotation="int")],
        {"count": "25"},
    ),
    # —— multi ——
    (
        "tickers_info",
        "multi",
        "Tickers 批量 info（空格分隔）",
        [_p("tickers", required=True)],
        {"tickers": "AAPL MSFT GOOG"},
    ),
    (
        "tickers_history",
        "multi",
        "Tickers.history 批量历史",
        [
            _p("tickers", required=True),
            _p("period", default="5d"),
            _p("interval", default="1d"),
        ],
        {"tickers": "AAPL MSFT", "period": "5d"},
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
                    "Yahoo Finance via yfinance。美股代码如 AAPL；指数如 ^GSPC；"
                    "A 股示例 000001.SS / 399001.SZ；港股 0700.HK。\n"
                    "https://github.com/ranaroussi/yfinance"
                ),
                "params": params,
                "example_params": example,
            }
        )
    items.sort(key=lambda x: (x["category"], x["name"]))
    return items
