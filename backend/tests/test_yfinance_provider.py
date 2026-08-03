"""Tests for yfinance provider (catalog + fetch + market)."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest


def test_list_sources_includes_yfinance():
    from app import providers

    ids = {s["id"] for s in providers.list_sources()}
    assert "yfinance" in ids
    meta = next(s for s in providers.list_sources() if s["id"] == "yfinance")
    assert "explorer" in meta["features"]
    assert "market" in meta["features"]


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
        },
    )
    out = YfinanceProvider().get_market()
    assert out["source"] == "yfinance"
    assert out["summary"]["amount_sh"] is None
    assert out["featured"][0]["symbol"] == "^GSPC"
    assert "boards" in out
    assert out["boards"]["error"] is None or isinstance(out["boards"]["error"], str)


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
