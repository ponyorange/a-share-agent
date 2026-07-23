from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.advisor.committee.models import Freshness, Horizon
from app.advisor.committee.snapshot import (
    CollectorSpec,
    CriticalDataError,
    FutureDataError,
    SnapshotItem,
    SnapshotBuilder,
    _normalize,
    default_collector_specs,
)


AS_OF = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
CAPTURED = AS_OF + timedelta(minutes=1)


def _collector(content, *, data_as_of=AS_OF):
    def collect(**_kwargs):
        return {
            "content": content,
            "data_as_of": data_as_of,
            "captured_at": CAPTURED,
        }

    return collect


def test_snapshot_hash_is_stable_for_mapping_order_floats_and_dates():
    first = SnapshotBuilder(
        (
            CollectorSpec(
                "market",
                "fake.market",
                _collector(
                    {
                        "close": 3.1400,
                        "session": date(2026, 7, 21),
                        "nested": {"b": 2, "a": 1},
                    }
                ),
                critical=True,
            ),
        )
    ).build(
        as_of=AS_OF,
        strategy_version="strategy-7",
        horizon=Horizon.NEXT_DAY,
        universe=("510300", "000001"),
    )
    second = SnapshotBuilder(
        (
            CollectorSpec(
                "market",
                "fake.market",
                _collector(
                    {
                        "nested": {"a": 1, "b": 2},
                        "session": date(2026, 7, 21),
                        "close": 3.14,
                    }
                ),
                critical=True,
            ),
        )
    ).build(
        as_of=AS_OF,
        strategy_version="strategy-7",
        horizon="next_day",
        universe=("000001", "510300"),
    )

    assert first.snapshot_id == second.snapshot_id
    assert first.model_dump(mode="json")["items"][0]["content"]["close"] == 3.14
    with pytest.raises((TypeError, ValidationError, AttributeError, KeyError)):
        first.items[0].content["close"] = 4.0


def test_future_data_is_always_rejected():
    builder = SnapshotBuilder(
        (
            CollectorSpec(
                "news",
                "fake.news",
                _collector({"headline": "future"}, data_as_of=AS_OF + timedelta(seconds=1)),
                critical=False,
            ),
        )
    )

    with pytest.raises(FutureDataError, match="news"):
        builder.build(
            as_of=AS_OF,
            strategy_version="v1",
            horizon="next_day",
            universe=("510300",),
        )


def test_default_account_collector_freezes_injected_account_without_mongo():
    calls = []

    def account_source(*, user_id, as_of):
        calls.append((user_id, as_of))
        return {
            "cash": 10_000,
            "equity": 12_000,
            "version": "v1",
            "data_as_of": AS_OF,
            "positions": [
                {
                    "symbol": "510300",
                    "quantity": 200,
                    "available_quantity": 100,
                    "acquired_at": "2026-07-18T08:00:00+00:00",
                    "cost": 9.5,
                    "last_price": 10,
                    "market_value": 2000,
                    "price_as_of": AS_OF,
                }
            ],
        }

    spec = next(
        item
        for item in default_collector_specs(
            account_source=account_source
        )
        if item.name == "portfolio_account"
    )
    snapshot = SnapshotBuilder((spec,)).build(
        user_id="user-1",
        as_of=AS_OF,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
    )
    assert calls == [("user-1", AS_OF)]
    assert snapshot.items[0].critical is True
    assert snapshot.items[0].content["cash"] == 10_000


def test_default_account_collector_failure_aborts_snapshot():
    def broken(**kwargs):
        raise RuntimeError("account unavailable")

    spec = next(
        item
        for item in default_collector_specs(account_source=broken)
        if item.name == "portfolio_account"
    )
    with pytest.raises(CriticalDataError):
        SnapshotBuilder((spec,)).build(
            user_id="user-1",
            as_of=AS_OF,
            strategy_version="v1",
            horizon="next_day",
            universe=("510300",),
        )


def test_future_timestamp_inside_content_is_rejected():
    builder = SnapshotBuilder(
        (
            CollectorSpec(
                "kline",
                "fake.kline",
                _collector(
                    {
                        "bars": [
                            {
                                "time": "2026-07-22T08:00:00Z",
                                "close": 1.0,
                            }
                        ]
                    }
                ),
                critical=True,
            ),
        )
    )

    with pytest.raises(FutureDataError, match="kline"):
        builder.build(
            as_of=AS_OF,
            strategy_version="v1",
            horizon="next_day",
            universe=("510300",),
        )


def test_critical_failure_aborts_but_noncritical_failure_is_recorded():
    def fail(**_kwargs):
        raise RuntimeError("provider unavailable")

    with pytest.raises(CriticalDataError, match="kline"):
        SnapshotBuilder(
            (CollectorSpec("kline", "fake.kline", fail, critical=True),)
        ).build(
            as_of=AS_OF,
            strategy_version="v1",
            horizon="next_day",
            universe=("510300",),
        )

    snapshot = SnapshotBuilder(
        (
            CollectorSpec("market", "fake.market", _collector({"close": 1}), True),
            CollectorSpec("news", "fake.news", fail, False),
        )
    ).build(
        as_of=AS_OF,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
    )
    news = snapshot.items[1]
    assert news.freshness is Freshness.ERROR
    assert news.degraded is True
    assert news.error == "RuntimeError"


def test_noncritical_provider_error_payload_is_marked_degraded():
    snapshot = SnapshotBuilder(
        (
            CollectorSpec(
                "news",
                "fake.news",
                _collector({"items": [], "error": "upstream timeout"}),
                False,
            ),
        )
    ).build(
        as_of=AS_OF,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
    )

    item = snapshot.items[0]
    assert item.freshness is Freshness.DEGRADED
    assert item.degraded is True
    assert item.error == "upstream_error"


@pytest.mark.parametrize(
    "metadata",
    [
        {"freshness": Freshness.STALE},
        {"degraded": True},
        {"error": "bad"},
    ],
)
def test_critical_source_requires_clean_fresh_result(metadata):
    def collect(**_kwargs):
        return {
            "content": {"close": 1.0},
            "captured_at": CAPTURED,
            "data_as_of": AS_OF,
            **metadata,
        }

    with pytest.raises(CriticalDataError, match="market"):
        SnapshotBuilder(
            (CollectorSpec("market", "fake.market", collect, True),)
        ).build(
            as_of=AS_OF,
            strategy_version="v1",
            horizon="next_day",
            universe=("510300",),
        )
    with pytest.raises(ValidationError):
        SnapshotItem(
            name="market",
            source="fake.market",
            critical=True,
            captured_at=CAPTURED,
            data_as_of=AS_OF,
            content={"close": 1.0},
            **metadata,
        )


def test_snapshot_item_direct_construction_deep_freezes_content():
    original = {"nested": [{"score": 0.5}]}
    item = SnapshotItem(
        name="news",
        source="fake",
        critical=False,
        captured_at=CAPTURED,
        content=original,
    )
    original["nested"][0]["score"] = float("nan")

    assert item.content["nested"][0]["score"] == 0.5
    with pytest.raises(TypeError):
        item.content["nested"][0]["score"] = float("nan")
    assert item.model_dump(mode="json")["content"]["nested"][0]["score"] == 0.5


def test_set_normalization_uses_canonical_json_order():
    assert _normalize({"z", "a", "m"}) == ["a", "m", "z"]
    assert _normalize(frozenset((3, 1, 2))) == [1, 2, 3]


@pytest.mark.parametrize(
    "content",
    [
        {"publishedAt": "2026-07-22T00:00:00Z"},
        {"eventTimestamp": 1784707200000},
        {"tradeDate": "not-a-date"},
    ],
)
def test_future_guard_handles_camelcase_epoch_and_parse_failures(content):
    with pytest.raises(FutureDataError):
        SnapshotBuilder(
            (
                CollectorSpec(
                    "news",
                    "fake.news",
                    _collector(content),
                    False,
                ),
            )
        ).build(
            as_of=AS_OF,
            strategy_version="v1",
            horizon="next_day",
            universe=("510300",),
        )


def test_default_fundamentals_does_not_claim_requested_historical_as_of(
    monkeypatch,
):
    from app.advisor.agent import unstructured

    monkeypatch.setattr(
        unstructured,
        "fetch_macro_china_snapshot",
        lambda: {"blocks": {"cpi": {"items": [{"value": "1.2"}]}}},
    )
    fundamentals = next(
        spec for spec in default_collector_specs() if spec.name == "fundamentals"
    )

    result = fundamentals.collector(
        as_of=AS_OF,
        universe=("510300",),
        horizon=Horizon.NEXT_DAY,
        strategy_version="v1",
    )

    assert result.get("data_as_of") is None


@pytest.mark.parametrize(
    "content",
    [
        {"发布时间": "2026-07-22 09:30:00"},
        {"交易日期": "2026年07月22日"},
        {"更新时间": 1784707200000},
    ],
)
def test_future_guard_recognizes_chinese_temporal_fields(content):
    with pytest.raises(FutureDataError):
        SnapshotBuilder(
            (
                CollectorSpec(
                    "macro",
                    "fake.macro",
                    _collector(content),
                    False,
                ),
            )
        ).build(
            as_of=AS_OF,
            strategy_version="v1",
            horizon="next_day",
            universe=("510300",),
        )


def test_decimal_normalization_is_lossless_and_hashes_large_values_distinctly():
    left = Decimal("12345678901234567890.12345678901234567890")
    right = Decimal("12345678901234567890.12345678901234567891")

    assert _normalize(Decimal("1.2300")).value == "1.23"
    first = SnapshotBuilder(
        (CollectorSpec("market", "fake", _collector({"value": left}), True),)
    ).build(
        as_of=AS_OF,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
    )
    second = SnapshotBuilder(
        (CollectorSpec("market", "fake", _collector({"value": right}), True),)
    ).build(
        as_of=AS_OF,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
    )

    assert first.snapshot_id != second.snapshot_id

    typed_decimal = SnapshotBuilder(
        (
            CollectorSpec(
                "market",
                "fake",
                _collector({"value": Decimal("1.23")}),
                True,
            ),
        )
    ).build(
        as_of=AS_OF,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
    )
    ordinary_dict = SnapshotBuilder(
        (
            CollectorSpec(
                "market",
                "fake",
                _collector({"value": {"$decimal": "1.23"}}),
                True,
            ),
        )
    ).build(
        as_of=AS_OF,
        strategy_version="v1",
        horizon="next_day",
        universe=("510300",),
    )
    assert typed_decimal.snapshot_id != ordinary_dict.snapshot_id


def test_default_macro_filters_future_rows_and_marks_unknown_history_degraded(
    monkeypatch,
):
    from app.advisor.agent import unstructured

    monkeypatch.setattr(
        unstructured,
        "fetch_macro_china_snapshot",
        lambda: {
            "blocks": {
                "cpi": {
                    "items": [
                        {"日期": "2026-07-20", "value": "1.2"},
                        {"日期": "2026-07-22", "value": "9.9"},
                    ]
                }
            },
            "errors": ["money_supply: timeout"],
        },
    )
    fundamentals = next(
        spec for spec in default_collector_specs() if spec.name == "fundamentals"
    )
    result = fundamentals.collector(
        as_of=AS_OF,
        universe=("510300",),
        horizon=Horizon.NEXT_DAY,
        strategy_version="v1",
    )

    items = result["content"]["blocks"]["cpi"]["items"]
    assert items == [{"日期": "2026-07-20", "value": "1.2"}]
    assert result["data_as_of"] == datetime(
        2026, 7, 20, tzinfo=timezone.utc
    )
    assert result["degraded"] is True
    assert result["freshness"] is Freshness.DEGRADED
    assert result["error"] == "upstream_errors"

    monkeypatch.setattr(
        unstructured,
        "fetch_macro_china_snapshot",
        lambda: {"blocks": {"cpi": {"items": [{"value": "unknown"}]}}},
    )
    unknown = fundamentals.collector(
        as_of=datetime(2020, 1, 1, tzinfo=timezone.utc),
        universe=("510300",),
        horizon=Horizon.NEXT_DAY,
        strategy_version="v1",
    )
    assert unknown["degraded"] is True
    assert unknown["error"] == "unknown_data_as_of"
    assert unknown["content"] == {}

    monkeypatch.setattr(
        unstructured,
        "fetch_macro_china_snapshot",
        lambda: {
            "blocks": {
                "cpi": {
                    "items": [
                        {"日期": "2019-12-31", "value": "known"},
                        {"value": "unknown"},
                    ]
                }
            }
        },
    )
    mixed = fundamentals.collector(
        as_of=datetime(2020, 1, 1, tzinfo=timezone.utc),
        universe=("510300",),
        horizon=Horizon.NEXT_DAY,
        strategy_version="v1",
    )
    assert mixed["degraded"] is True
    assert mixed["error"] == "unknown_data_as_of"
    assert mixed["content"] == {}


def test_unknown_source_time_degrades_intraday_historical_snapshot(monkeypatch):
    from app.advisor.agent import unstructured
    from app.advisor.committee import snapshot as snapshot_module

    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(snapshot_module, "utc_now", lambda: now)
    monkeypatch.setattr(
        unstructured,
        "fetch_macro_china_snapshot",
        lambda: {"blocks": {"cpi": {"items": [{"value": "unknown"}]}}},
    )
    fundamentals = next(
        spec for spec in default_collector_specs() if spec.name == "fundamentals"
    )

    result = fundamentals.collector(
        as_of=now - timedelta(hours=1),
        universe=("510300",),
        horizon=Horizon.NEXT_DAY,
        strategy_version="v1",
    )

    assert result["degraded"] is True
    assert result["error"] == "unknown_data_as_of"
    assert result["content"] == {}
