import pytest


def test_upsert_and_get(monkeypatch):
    mem = {}

    class _Col:
        def update_one(self, flt, upd, upsert=False):
            mem[flt["trade_date"]] = {**flt, **upd.get("$set", {})}

        def find_one(self, flt):
            return mem.get(flt.get("trade_date"))

    monkeypatch.setattr(
        "app.advisor.regime.store.get_db",
        lambda: type("D", (), {"market_regime_daily": _Col()})(),
    )
    from app.advisor.regime.store import get_daily, upsert_daily

    upsert_daily("2026-08-01", {"limit_up_count": 40})
    assert get_daily("2026-08-01")["limit_up_count"] == 40


def test_list_daily_sorts_latest_first(monkeypatch):
    rows = [
        {"trade_date": "2026-08-01", "limit_up_count": 40},
        {"trade_date": "2026-08-02", "limit_up_count": 50},
    ]

    class _Cursor:
        def __init__(self, docs):
            self.docs = list(docs)

        def sort(self, key, direction):
            self.docs.sort(key=lambda x: x[key], reverse=direction < 0)
            return self

        def limit(self, n):
            self.docs = self.docs[:n]
            return self

        def __iter__(self):
            return iter(self.docs)

    class _Col:
        def find(self, flt):
            assert flt == {}
            return _Cursor(rows)

    monkeypatch.setattr(
        "app.advisor.regime.store.get_db",
        lambda: type("D", (), {"market_regime_daily": _Col()})(),
    )
    from app.advisor.regime.store import list_daily

    assert [x["trade_date"] for x in list_daily(limit=1)] == ["2026-08-02"]


def test_collect_raw_aggregates_fetch_errors():
    from app.advisor.regime.collector import collect_raw

    def fetch_sealed(date_yyyymmdd):
        assert date_yyyymmdd == "20260801"
        return [{"代码": "000001", "名称": "样本A", "连板数": 2, "涨跌幅": 10}]

    def fetch_broken(date_yyyymmdd):
        raise RuntimeError("broken unavailable")

    def fetch_limit_down(date_yyyymmdd):
        return [{"代码": "000002"}]

    out = collect_raw(
        "2026-08-01",
        fetch_sealed=fetch_sealed,
        fetch_broken=fetch_broken,
        fetch_limit_down=fetch_limit_down,
        fetch_trend_features=lambda trade_date: {"ma_stack": "above"},
    )

    assert out["trade_date"] == "2026-08-01"
    assert len(out["sealed"]) == 1
    assert out["broken"] == []
    assert out["limit_down_count"] == 1
    assert out["ladder_max"] == 2
    assert out["trend_features"] == {"ma_stack": "above"}
    assert out["errors"] == ["broken: RuntimeError: broken unavailable"]


def test_collect_raw_defaults_trade_date(monkeypatch):
    from app.advisor.regime import collector

    monkeypatch.setattr(collector, "_today_trade_date", lambda: "2026-08-02")
    out = collector.collect_raw(
        fetch_sealed=lambda date: [],
        fetch_broken=lambda date: [],
        fetch_limit_down=lambda date: [],
        fetch_trend_features=lambda trade_date: {},
    )

    assert out["trade_date"] == "2026-08-02"
    assert out["sealed"] == []
    assert out["broken"] == []
    assert out["errors"] == []


def test_default_trend_features_uses_market_and_index_ma(monkeypatch):
    import app.kline
    import app.market
    from app.advisor.regime.collector import _fetch_trend_features_default

    monkeypatch.setattr(
        app.kline,
        "fetch_symbol_daily_ma",
        lambda symbol, recent: {
            "symbol": symbol,
            "latest": {"close": 105.0, "ma5": 103.0, "ma10": 101.0, "ma20": 99.0},
            "recent": [
                {"high": 100.0, "volume": 100.0},
                {"high": 110.0, "volume": 200.0},
            ],
        },
    )
    monkeypatch.setattr(
        app.market,
        "get_market",
        lambda: {
            "featured": [
                {"change_pct": 1.0},
                {"change_pct": -0.5},
            ]
        },
    )

    out = _fetch_trend_features_default("2026-08-01")

    assert out["ma_stack"] == "above"
    assert abs(out["drawdown_from_high"] - (1 - 105.0 / 110.0)) < 1e-9
    assert out["breadth"] == 0.5
    assert abs(out["volume_vs_ma20"] - (200.0 / 150.0)) < 1e-9
