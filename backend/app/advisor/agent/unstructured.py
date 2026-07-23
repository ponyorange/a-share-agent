"""AKShare unstructured / soft-data fetchers for the agent."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd


def _df_records(df: pd.DataFrame | None, limit: int = 12) -> list[dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    work = df.head(limit).copy()
    # stringify timestamps / nans
    records = []
    for row in work.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for k, v in row.items():
            key = str(k)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                clean[key] = None
            elif hasattr(v, "isoformat"):
                clean[key] = v.isoformat()
            else:
                s = str(v)
                clean[key] = s if len(s) <= 800 else s[:800] + "…"
        records.append(clean)
    return records


def fetch_stock_news(symbol: str, limit: int = 10) -> dict[str, Any]:
    """东方财富个股新闻 stock_news_em。"""
    import akshare as ak

    sym = str(symbol).zfill(6)
    try:
        df = ak.stock_news_em(symbol=sym)
    except Exception as exc:
        return {"symbol": sym, "source": "akshare.stock_news_em", "error": str(exc), "items": []}
    return {
        "symbol": sym,
        "source": "akshare.stock_news_em",
        "count": int(len(df)) if df is not None else 0,
        "items": _df_records(df, limit),
    }


def fetch_stock_notices(symbol: str, limit: int = 8) -> dict[str, Any]:
    """个股公告（优先 stock_individual_notice_report / stock_notice_report）。"""
    import akshare as ak

    sym = str(symbol).zfill(6)
    errors: list[str] = []
    for name, caller in (
        (
            "stock_individual_notice_report",
            lambda: ak.stock_individual_notice_report(symbol=sym),
        ),
        (
            "stock_notice_report",
            lambda: ak.stock_notice_report(symbol=sym),
        ),
    ):
        if not hasattr(ak, name):
            continue
        try:
            df = caller()
            return {
                "symbol": sym,
                "source": f"akshare.{name}",
                "count": int(len(df)) if df is not None else 0,
                "items": _df_records(df, limit),
            }
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    return {
        "symbol": sym,
        "source": "akshare.notice",
        "error": "; ".join(errors) or "no notice API",
        "items": [],
    }


def fetch_research_reports(symbol: str, limit: int = 6) -> dict[str, Any]:
    """东方财富研报 stock_research_report_em。"""
    import akshare as ak

    sym = str(symbol).zfill(6)
    if not hasattr(ak, "stock_research_report_em"):
        return {"symbol": sym, "error": "API 不可用", "items": []}
    try:
        df = ak.stock_research_report_em(symbol=sym)
    except Exception as exc:
        return {
            "symbol": sym,
            "source": "akshare.stock_research_report_em",
            "error": str(exc),
            "items": [],
        }
    return {
        "symbol": sym,
        "source": "akshare.stock_research_report_em",
        "count": int(len(df)) if df is not None else 0,
        "items": _df_records(df, limit),
    }


def fetch_market_cctv_news(date: str | None = None, limit: int = 12) -> dict[str, Any]:
    """CCTV 新闻联播（大盘/宏观情绪参考）。"""
    import akshare as ak

    day = (date or datetime.now().strftime("%Y%m%d"))[:8]
    # try recent days if empty
    errors: list[str] = []
    for i in range(0, 5):
        d = (datetime.strptime(day, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = ak.news_cctv(date=d)
            if df is not None and not df.empty:
                return {
                    "date": d,
                    "source": "akshare.news_cctv",
                    "count": int(len(df)),
                    "items": _df_records(df, limit),
                }
        except Exception as exc:
            errors.append(f"{d}: {exc}")
    return {
        "date": day,
        "source": "akshare.news_cctv",
        "error": "; ".join(errors)[:500] or "empty",
        "items": [],
    }


def fetch_index_news_sentiment(limit: int = 15) -> dict[str, Any]:
    """指数新闻情绪（若可用）。"""
    import akshare as ak

    if not hasattr(ak, "index_news_sentiment_scope"):
        return {"source": "akshare.index_news_sentiment_scope", "error": "API 不可用", "items": []}
    try:
        df = ak.index_news_sentiment_scope()
    except Exception as exc:
        return {
            "source": "akshare.index_news_sentiment_scope",
            "error": str(exc),
            "items": [],
        }
    return {
        "source": "akshare.index_news_sentiment_scope",
        "count": int(len(df)) if df is not None else 0,
        "items": _df_records(df, limit),
    }


def fetch_macro_china_snapshot(limit: int = 5) -> dict[str, Any]:
    """中国宏观快照：CPI、LPR、货币供应等（财经/政策相关）。"""
    import akshare as ak

    out: dict[str, Any] = {"source": "akshare.macro_china_*", "blocks": {}}
    errors: list[str] = []

    # CPI 同比（旧→新，取尾部）
    try:
        df = ak.macro_china_cpi_yearly()
        out["blocks"]["cpi_yearly"] = {
            "source": "akshare.macro_china_cpi_yearly",
            "items": _df_records(df.tail(limit) if df is not None else None, limit),
        }
    except Exception as exc:
        errors.append(f"cpi_yearly: {exc}")

    # LPR（旧→新）
    try:
        df = ak.macro_china_lpr()
        out["blocks"]["lpr"] = {
            "source": "akshare.macro_china_lpr",
            "items": _df_records(df.tail(limit) if df is not None else None, limit),
        }
    except Exception as exc:
        errors.append(f"lpr: {exc}")

    # M2 等货币供应（新→旧，取头部）
    try:
        df = ak.macro_china_money_supply()
        out["blocks"]["money_supply"] = {
            "source": "akshare.macro_china_money_supply",
            "items": _df_records(df.head(limit) if df is not None else None, limit),
        }
    except Exception as exc:
        errors.append(f"money_supply: {exc}")

    # 央行利率决议（偏政策，旧→新）
    try:
        df = ak.macro_bank_china_interest_rate()
        out["blocks"]["cb_interest_rate"] = {
            "source": "akshare.macro_bank_china_interest_rate",
            "items": _df_records(df.tail(limit) if df is not None else None, limit),
        }
    except Exception as exc:
        errors.append(f"cb_interest_rate: {exc}")

    if errors:
        out["errors"] = errors
    out["note"] = (
        "覆盖财经宏观与货币政策相关指标；无独立「政治」数据源。"
        "政策/政治相关信息可结合新闻联播与经济日历间接参考。"
    )
    return out


def fetch_economic_calendar(date: str | None = None, limit: int = 20) -> dict[str, Any]:
    """百度经济日历（财经事件/数据公布）。date=YYYYMMDD，空则今天起回溯数日。"""
    import akshare as ak

    day = (date or datetime.now().strftime("%Y%m%d"))[:8]
    errors: list[str] = []
    for i in range(0, 5):
        d = (datetime.strptime(day, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = ak.news_economic_baidu(date=d)
            if df is not None and not df.empty:
                return {
                    "date": d,
                    "source": "akshare.news_economic_baidu",
                    "count": int(len(df)),
                    "items": _df_records(df, limit),
                }
        except Exception as exc:
            errors.append(f"{d}: {exc}")
    return {
        "date": day,
        "source": "akshare.news_economic_baidu",
        "error": "; ".join(errors)[:500] or "empty",
        "items": [],
    }


def list_unstructured_capabilities() -> dict[str, Any]:
    """告诉 Agent 可用的非结构化数据工具。"""
    return {
        "tools": [
            {
                "name": "fetch_stock_news",
                "desc": "个股东方财富新闻",
            },
            {
                "name": "fetch_stock_notices",
                "desc": "个股公告",
            },
            {
                "name": "fetch_research_reports",
                "desc": "个股研报摘要列表",
            },
            {
                "name": "fetch_market_cctv_news",
                "desc": "新闻联播（宏观/政策/政治相关公开报道）",
            },
            {
                "name": "fetch_index_news_sentiment",
                "desc": "指数新闻情绪（若接口可用）",
            },
            {
                "name": "fetch_macro_china_snapshot",
                "desc": "中国宏观：CPI、LPR、货币供应、央行利率等",
            },
            {
                "name": "fetch_economic_calendar",
                "desc": "经济日历（财经数据公布日程）",
            },
        ],
        "coverage": {
            "finance": "个股新闻/公告/研报、经济日历、宏观指标",
            "policy": "LPR/央行利率、货币供应、新闻联播中的政策条目",
            "politics": "无专用政治数据源；可通过新闻联播等公开报道间接参考",
        },
        "note": "数据来自 AKShare，可能受网络/源站限制；失败时请说明并改用已有行情工具。",
    }
