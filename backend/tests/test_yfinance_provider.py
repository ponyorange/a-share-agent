"""Tests for yfinance provider (catalog + fetch + market)."""

from __future__ import annotations

from types import SimpleNamespace
import sys

import pandas as pd
import pytest


def test_list_sources_includes_yfinance():
    from app import providers

    ids = {s["id"] for s in providers.list_sources()}
    assert "yfinance" in ids
    meta = next(s for s in providers.list_sources() if s["id"] == "yfinance")
    assert "explorer" in meta["features"]
    assert "market" in meta["features"]
    assert "kline" in meta["features"]


def test_catalog_nonempty_and_categories():
    from app.providers import yfinance_catalog as cat
    from app.providers.yfinance_provider import YfinanceProvider

    catalog = cat.build_catalog()
    assert len(catalog) >= 30
    names = {i["name"] for i in catalog}
    assert "ticker_history" in names
    assert "download" in names
    assert "search" in names
    assert "screener_day_gainers" in names

    p = YfinanceProvider()
    cats = p.get_categories()
    ids = {c["id"] for c in cats}
    assert {"ticker", "download", "search", "screener", "multi"} <= ids


def test_fetch_ticker_history_mocked(monkeypatch):
    from app.providers import yfinance_provider as yp

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            return pd.DataFrame(
                {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100]}
            )

    fake_yf = SimpleNamespace(Ticker=FakeTicker)
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)
    monkeypatch.setattr(yp, "_call_ticker", lambda name, params: FakeTicker("AAPL").history())

    p = yp.YfinanceProvider()
    out = p.fetch("ticker_history", {"symbol": "AAPL", "period": "1mo"}, limit=10)
    assert out["name"] == "ticker_history"
    assert out["type"] == "dataframe"
    assert out["returned"] == 1


def test_fetch_download_mocked(monkeypatch):
    from app.providers import yfinance_provider as yp

    def _download(**kwargs):
        return pd.DataFrame({"Close": [10.0, 11.0]})

    monkeypatch.setattr(
        yp,
        "_call_download",
        lambda params: _download(tickers=params.get("tickers")),
    )
    p = yp.YfinanceProvider()
    out = p.fetch("download", {"tickers": "AAPL MSFT", "period": "5d"}, limit=5)
    assert out["name"] == "download"
    assert out["returned"] == 2


def test_fetch_rejects_unknown():
    from app.providers.yfinance_provider import YfinanceProvider

    p = YfinanceProvider()
    with pytest.raises(ValueError, match="not allowed"):
        p.fetch("not_a_real_api", {}, limit=10)


def test_get_market_shape(monkeypatch):
    from app.providers import yfinance_market as ym
    from app.providers.yfinance_provider import YfinanceProvider

    ym._cache["ts"] = 0.0
    ym._cache["payload"] = None
    monkeypatch.setattr(
        ym,
        "_fetch_indices",
        lambda: [
            {
                "symbol": "^GSPC",
                "name": "标普500",
                "price": 5000.0,
                "change": 10.0,
                "change_pct": 0.2,
                "open": 4990.0,
                "high": 5010.0,
                "low": 4980.0,
                "pre_close": 4990.0,
                "volume": 1.0,
                "amount": None,
                "featured": True,
            }
        ],
    )
    monkeypatch.setattr(
        ym,
        "_fetch_boards",
        lambda limit=15: {
            "gainers": [],
            "losers": [],
            "amount": [],
            "source": "yfinance",
            "error": "screener 无数据或当前环境不可用",
        },
    )
    out = YfinanceProvider().get_market()
    assert out["source"] == "yfinance"
    assert out["summary"]["amount_sh"] is None
    assert out["featured"][0]["symbol"] == "^GSPC"
    assert "boards" in out
    assert out["boards"]["error"] is None or isinstance(out["boards"]["error"], str)


def test_quote_from_batched_history():
    from app.providers import yfinance_market as ym

    idx = pd.DatetimeIndex(["2026-07-31", "2026-08-01"])
    hist = pd.DataFrame(
        {
            "Open": [10.0, 11.0],
            "High": [12.0, 13.0],
            "Low": [9.0, 10.5],
            "Close": [11.0, 12.0],
            "Volume": [100, 200],
        },
        index=idx,
    )
    multi = pd.concat({"^GSPC": hist}, axis=1)
    multi.columns = pd.MultiIndex.from_tuples(
        [(sym, col) for sym, col in multi.columns]
    )
    frame = ym._hist_frame_for_symbol(multi, "^GSPC")
    row = ym._quote_from_history("^GSPC", "标普500", True, frame)
    assert row["price"] == 12.0
    assert row["pre_close"] == 11.0
    assert abs(row["change_pct"] - (100.0 / 11.0)) < 1e-6


def test_fetch_indices_raises_when_all_empty(monkeypatch):
    from app.providers import yfinance_market as ym

    def _download(*a, **k):
        cols = pd.MultiIndex.from_product(
            [["^GSPC", "^DJI"], ["Open", "High", "Low", "Close", "Volume"]]
        )
        return pd.DataFrame(columns=cols)

    fake_yf = SimpleNamespace(download=_download)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    with pytest.raises(RuntimeError, match="未能获取|限流"):
        ym._fetch_indices()


def test_describe_not_ready_without_package(monkeypatch):
    from app.providers.yfinance_provider import YfinanceProvider
    import builtins

    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "yfinance" or name.startswith("yfinance."):
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)
    desc = YfinanceProvider().describe()
    assert desc["ready"] is False
    assert "yfinance" in (desc["message"] or "")


def test_normalize_yfinance_symbol():
    from app.providers.yfinance_kline import normalize_symbol

    assert normalize_symbol("aapl") == "AAPL"
    assert normalize_symbol(" ^GSPC ") == "^GSPC"
    assert normalize_symbol("0700.HK") == "0700.HK"
    with pytest.raises(ValueError):
        normalize_symbol("")
    with pytest.raises(ValueError):
        normalize_symbol("bad symbol!")


def test_get_kline_from_history_df(monkeypatch):
    from app.providers import yfinance_kline as yk

    yk._cache.clear()
    idx = pd.date_range("2026-07-01", periods=5, freq="D")
    hist = pd.DataFrame(
        {
            "Open": [1, 2, 3, 4, 5],
            "High": [2, 3, 4, 5, 6],
            "Low": [0.5, 1, 2, 3, 4],
            "Close": [1.5, 2.5, 3.5, 4.5, 5.5],
            "Volume": [10, 20, 30, 40, 50],
        },
        index=idx,
    )

    def _download(*a, **k):
        return hist

    class FakeTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        def history(self, **kwargs):
            return hist

    fake_yf = SimpleNamespace(Ticker=FakeTicker, download=_download)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    out = yk.get_kline("AAPL", "daily")
    assert out["symbol"] == "AAPL"
    assert out["name"] == "AAPL"
    assert out["chart_type"] == "candle"
    assert out["count"] == 5
    assert out["last"]["close"] == 5.5
    assert out["pre_close"] == 4.5


def test_kline_route_surfaces_runtime_message(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.providers import yfinance_provider as yp

    monkeypatch.setattr(
        yp.YfinanceProvider,
        "get_kline",
        lambda self, symbol, range_: (_ for _ in ()).throw(
            RuntimeError("Yahoo Finance 限流，请稍后再试")
        ),
    )
    # patch registry instance
    from app import providers

    providers._PROVIDERS["yfinance"] = yp.YfinanceProvider()
    client = TestClient(app)
    res = client.get("/api/yfinance/kline", params={"symbol": "AAPL", "range": "daily"})
    assert res.status_code == 502
    assert "限流" in res.json()["detail"]
