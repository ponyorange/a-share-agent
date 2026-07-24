import pandas as pd

from app.kline import _bar, _parse_trend_row, _safe_avg_price
from app.providers.baostock_kline import _bars_from_min_df


def test_safe_avg_price_accepts_positive():
    assert _safe_avg_price("12.34") == 12.34
    assert _safe_avg_price(12.34) == 12.34


def test_safe_avg_price_rejects_invalid():
    assert _safe_avg_price(None) is None
    assert _safe_avg_price("") is None
    assert _safe_avg_price("0") is None
    assert _safe_avg_price("-1") is None
    assert _safe_avg_price("nan") is None
    assert _safe_avg_price("inf") is None


def test_bar_includes_avg_price_when_valid():
    item = _bar(
        "2026-07-25 09:31",
        10,
        11,
        9,
        10.5,
        1000,
        avg_price=10.2,
    )
    assert item["avg_price"] == 10.2


def test_bar_omits_invalid_avg_price():
    item = _bar(
        "2026-07-25 09:31",
        10,
        11,
        9,
        10.5,
        1000,
        avg_price=0,
    )
    assert "avg_price" not in item


def test_parse_trend_row_reads_eighth_column():
    row = "2026-07-25 09:31,10.0,10.5,10.8,9.9,1000,10500,10.5"
    bar = _parse_trend_row(row)
    assert bar is not None
    assert bar["close"] == 10.5
    assert bar["volume"] == 1000.0
    assert bar["avg_price"] == 10.5


def test_parse_trend_row_without_avg_still_works():
    row = "2026-07-25 09:31,10.0,10.5,10.8,9.9,1000"
    bar = _parse_trend_row(row)
    assert bar is not None
    assert "avg_price" not in bar


def test_baostock_min_bars_cumulative_vwap_resets_by_day():
    df = pd.DataFrame(
        [
            {
                "date": "2026-07-24",
                "time": "20260724093500000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
                "amount": 1000,
            },
            {
                "date": "2026-07-24",
                "time": "20260724094000000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 12,
                "volume": 100,
                "amount": 1400,
            },
            {
                "date": "2026-07-25",
                "time": "20260725093500000",
                "open": 12,
                "high": 13,
                "low": 11,
                "close": 12,
                "volume": 50,
                "amount": 600,
            },
        ]
    )
    bars = _bars_from_min_df(df)
    assert bars[0]["avg_price"] == 10.0
    assert bars[1]["avg_price"] == 12.0
    assert bars[2]["avg_price"] == 12.0


def test_baostock_min_bars_carry_forward_on_zero_volume():
    df = pd.DataFrame(
        [
            {
                "date": "2026-07-25",
                "time": "20260725093500000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 100,
                "amount": 1050,
            },
            {
                "date": "2026-07-25",
                "time": "20260725094000000",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.2,
                "volume": 0,
                "amount": 0,
            },
        ]
    )
    bars = _bars_from_min_df(df)
    assert bars[0]["avg_price"] == 10.5
    assert bars[1]["avg_price"] == 10.5
