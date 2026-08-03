"""Yahoo Finance (yfinance) data source — explorer + market."""

from __future__ import annotations

from typing import Any

import pandas as pd

from . import yfinance_catalog
from . import yfinance_market
from ..serialize import normalize_result

# ticker_* name → attribute or (method, kwargs-from-params excluding symbol)
_TICKER_ATTRS: dict[str, str] = {
    "ticker_info": "info",
    "ticker_fast_info": "fast_info",
    "ticker_history_metadata": "history_metadata",
    "ticker_actions": "actions",
    "ticker_dividends": "dividends",
    "ticker_splits": "splits",
    "ticker_capital_gains": "capital_gains",
    "ticker_shares": "get_shares",
    "ticker_calendar": "calendar",
    "ticker_sec_filings": "sec_filings",
    "ticker_recommendations": "recommendations",
    "ticker_recommendations_summary": "recommendations_summary",
    "ticker_analyst_price_targets": "analyst_price_targets",
    "ticker_earnings": "earnings",
    "ticker_earnings_dates": "earnings_dates",
    "ticker_earnings_history": "earnings_history",
    "ticker_income_stmt": "income_stmt",
    "ticker_quarterly_income_stmt": "quarterly_income_stmt",
    "ticker_balance_sheet": "balance_sheet",
    "ticker_quarterly_balance_sheet": "quarterly_balance_sheet",
    "ticker_cashflow": "cashflow",
    "ticker_quarterly_cashflow": "quarterly_cashflow",
    "ticker_ttm_income_stmt": "ttm_income_stmt",
    "ticker_ttm_cashflow": "ttm_cashflow",
    "ticker_major_holders": "major_holders",
    "ticker_institutional_holders": "institutional_holders",
    "ticker_mutualfund_holders": "mutualfund_holders",
    "ticker_insider_transactions": "insider_transactions",
    "ticker_insider_purchases": "insider_purchases",
    "ticker_insider_roster_holders": "insider_roster_holders",
    "ticker_sustainability": "sustainability",
    "ticker_news": "news",
    "ticker_options": "options",
    "ticker_quarterly_earnings": "quarterly_earnings",
    "ticker_financials": "financials",
    "ticker_quarterly_financials": "quarterly_financials",
}

_SCREENER_PRESETS: dict[str, str] = {
    "screener_day_gainers": "day_gainers",
    "screener_day_losers": "day_losers",
    "screener_most_actives": "most_actives",
    "screener_growth_technology_stocks": "growth_technology_stocks",
    "screener_undervalued_large_caps": "undervalued_large_caps",
    "screener_aggressive_small_caps": "aggressive_small_caps",
}


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        out[key] = value
    return out


def _materialize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (pd.DataFrame, pd.Series, dict, list, tuple, str, int, float, bool)):
        return value
    if hasattr(value, "items") and not isinstance(value, type):
        try:
            return dict(value)
        except Exception:
            pass
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict()
        except Exception:
            pass
    return value


def _call_ticker(name: str, call_params: dict[str, Any]) -> Any:
    import yfinance as yf

    symbol = str(call_params.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("缺少必填参数 symbol")
    ticker = yf.Ticker(symbol)

    if name == "ticker_history":
        kwargs: dict[str, Any] = {}
        for key in ("period", "interval", "start", "end"):
            if key in call_params:
                kwargs[key] = call_params[key]
        if "auto_adjust" in call_params:
            kwargs["auto_adjust"] = _as_bool(call_params["auto_adjust"], True)
        if "actions" in call_params:
            kwargs["actions"] = _as_bool(call_params["actions"], True)
        return ticker.history(**kwargs)

    if name == "ticker_get_shares_full":
        start = call_params.get("start")
        end = call_params.get("end")
        return ticker.get_shares_full(start=start, end=end)

    if name == "ticker_option_chain":
        date = str(call_params.get("date") or "").strip()
        if not date:
            raise ValueError("缺少必填参数 date")
        chain = ticker.option_chain(date)
        # namedtuple(calls, puts) → concat with side column
        calls = chain.calls.copy()
        calls["side"] = "call"
        puts = chain.puts.copy()
        puts["side"] = "put"
        return pd.concat([calls, puts], ignore_index=True)

    if name == "ticker_shares":
        # prefer property/method get_shares if present
        getter = getattr(ticker, "get_shares", None)
        if callable(getter):
            return getter()
        return getattr(ticker, "shares", None)

    attr = _TICKER_ATTRS.get(name)
    if not attr:
        raise ValueError(f"未知 Ticker 接口: {name}")
    raw = getattr(ticker, attr, None)
    if callable(raw) and attr.startswith("get_"):
        return raw()
    return _materialize(raw)


def _call_download(call_params: dict[str, Any]) -> Any:
    import yfinance as yf

    tickers = str(call_params.get("tickers") or "").strip()
    if not tickers:
        raise ValueError("缺少必填参数 tickers")
    kwargs: dict[str, Any] = {"tickers": tickers}
    for key in ("period", "interval", "start", "end", "group_by"):
        if key in call_params:
            kwargs[key] = call_params[key]
    if "auto_adjust" in call_params:
        kwargs["auto_adjust"] = _as_bool(call_params["auto_adjust"], True)
    if "threads" in call_params:
        kwargs["threads"] = _as_bool(call_params["threads"], True)
    return yf.download(**kwargs)


def _call_search(name: str, call_params: dict[str, Any]) -> Any:
    import yfinance as yf

    query = str(call_params.get("query") or "").strip()
    if not query:
        raise ValueError("缺少必填参数 query")
    max_results = _as_int(call_params.get("max_results"), 8) or 8
    news_count = _as_int(call_params.get("news_count"), 5) or 5
    lists_count = _as_int(call_params.get("lists_count"), 0) or 0

    search_cls = getattr(yf, "Search", None)
    if search_cls is None:
        raise RuntimeError("当前 yfinance 版本不支持 Search")

    s = search_cls(
        query,
        max_results=max_results,
        news_count=news_count,
        lists_count=lists_count,
    )
    if name == "search_quotes":
        return s.quotes
    if name == "search_news":
        return s.news
    return {
        "quotes": s.quotes,
        "news": s.news,
        "lists": getattr(s, "lists", None),
        "research": getattr(s, "research", None),
    }


def _call_screener(name: str, call_params: dict[str, Any]) -> Any:
    import yfinance as yf

    preset = _SCREENER_PRESETS.get(name)
    if not preset:
        raise ValueError(f"未知 screener: {name}")
    count = _as_int(call_params.get("count"), 25) or 25
    screen_fn = getattr(yf, "screen", None)
    if screen_fn is None:
        raise RuntimeError("当前 yfinance 版本不支持 screen()")
    return screen_fn(preset, count=count)


def _call_multi(name: str, call_params: dict[str, Any]) -> Any:
    import yfinance as yf

    tickers = str(call_params.get("tickers") or "").strip()
    if not tickers:
        raise ValueError("缺少必填参数 tickers")
    bundle = yf.Tickers(tickers)
    if name == "tickers_info":
        rows = []
        for sym, t in (bundle.tickers or {}).items():
            try:
                info = _materialize(t.info) or {}
                if isinstance(info, dict):
                    rows.append({"symbol": sym, **{str(k): v for k, v in info.items()}})
                else:
                    rows.append({"symbol": sym, "info": info})
            except Exception as exc:
                rows.append({"symbol": sym, "error": f"{type(exc).__name__}: {exc}"})
        return rows
    if name == "tickers_history":
        kwargs: dict[str, Any] = {}
        for key in ("period", "interval"):
            if key in call_params:
                kwargs[key] = call_params[key]
        return bundle.history(**kwargs)
    raise ValueError(f"未知 multi 接口: {name}")


class YfinanceProvider:
    id = "yfinance"
    label = "yfinance"
    features = ("explorer", "market")
    docs_url = "https://github.com/ranaroussi/yfinance"

    def describe(self) -> dict[str, Any]:
        ready = True
        message = None
        try:
            import yfinance  # noqa: F401
        except ImportError:
            ready = False
            message = "未安装 yfinance，请 pip install yfinance"
        return {
            "id": self.id,
            "label": self.label,
            "features": list(self.features),
            "docs_url": self.docs_url,
            "ready": ready,
            "message": message,
        }

    def health(self) -> dict[str, Any]:
        version = "unknown"
        ready = False
        message = None
        try:
            import yfinance as yf

            version = getattr(yf, "__version__", "installed")
            ready = True
        except ImportError:
            version = "not_installed"
            message = "未安装 yfinance"
        return {
            "id": self.id,
            "label": self.label,
            "ready": ready,
            "version": version,
            "interface_count": len(yfinance_catalog.build_catalog()),
            "features": list(self.features),
            "message": message,
        }

    def get_categories(self) -> list[dict[str, Any]]:
        catalog = yfinance_catalog.build_catalog()
        counts: dict[str, int] = {}
        for item in catalog:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        result = []
        for key in yfinance_catalog.CATEGORY_LABELS:
            if key in counts:
                result.append(
                    {
                        "id": key,
                        "label": yfinance_catalog.CATEGORY_LABELS[key],
                        "count": counts[key],
                    }
                )
        for key, count in sorted(counts.items()):
            if key not in yfinance_catalog.CATEGORY_LABELS:
                result.append({"id": key, "label": key, "count": count})
        return result

    def list_interfaces(
        self, category: str | None = None, keyword: str | None = None
    ) -> list[dict[str, Any]]:
        catalog = yfinance_catalog.build_catalog()
        result = catalog
        if category:
            result = [i for i in result if i["category"] == category]
        if keyword:
            kw = keyword.lower().strip()
            result = [
                i
                for i in result
                if kw in i["name"].lower() or kw in (i["doc"] or "").lower()
            ]
        return [
            {
                "name": i["name"],
                "category": i["category"],
                "category_label": i["category_label"],
                "doc": i["doc"],
                "param_count": len(i["params"]),
            }
            for i in result
        ]

    def get_interface(self, name: str) -> dict[str, Any] | None:
        for item in yfinance_catalog.build_catalog():
            if item["name"] == name:
                return item
        return None

    def fetch(
        self, name: str, params: dict[str, Any], limit: int
    ) -> dict[str, Any]:
        item = self.get_interface(name)
        if not item:
            raise ValueError(f"Interface not allowed: {name}")

        call_params = _clean_params(params)
        try:
            if name.startswith("ticker_"):
                result = _call_ticker(name, call_params)
            elif name == "download":
                result = _call_download(call_params)
            elif name.startswith("search"):
                result = _call_search(name, call_params)
            elif name.startswith("screener_"):
                result = _call_screener(name, call_params)
            elif name.startswith("tickers_"):
                result = _call_multi(name, call_params)
            else:
                raise ValueError(f"Interface not allowed: {name}")
        except (ValueError, LookupError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

        normalized = normalize_result(_materialize(result), limit)
        return {"name": name, "params": call_params, **normalized}

    def get_market(self) -> dict[str, Any]:
        return yfinance_market.get_market()
