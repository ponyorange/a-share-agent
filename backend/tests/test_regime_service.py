from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


CFG = {
    "cache_ttl_seconds": 30,
    "history_default_limit": 20,
    "position_cap": {
        "aggressive": 0.85,
        "normal": 0.70,
        "defensive": 0.35,
        "risk_off": 0.15,
    },
    "pool_policy": {
        "aggressive": "full",
        "normal": "full",
        "defensive": "shrink",
        "risk_off": "defense_only",
    },
    "sentiment_weights": {
        "seal_rate": 0.25,
        "height": 0.25,
        "promotion": 0.25,
        "limit_up_count": 0.15,
        "limit_down_penalty": 0.10,
    },
    "cycle_thresholds": {
        "ice": 0.20,
        "repair": 0.35,
        "strengthen": 0.55,
        "climax": 0.75,
    },
    "cycle_hysteresis": 0.05,
    "trend_rules": {
        "uptrend_breadth_min": 0.55,
        "uptrend_drawdown_max": 0.12,
        "downtrend_drawdown_min": 0.18,
    },
    "matrix": {
        "uptrend": {
            "ice": "normal",
            "repair": "normal",
            "strengthen": "aggressive",
            "climax": "normal",
            "ebb": "defensive",
        },
        "range": {
            "ice": "defensive",
            "repair": "defensive",
            "strengthen": "normal",
            "climax": "defensive",
            "ebb": "risk_off",
        },
        "downtrend": {
            "ice": "risk_off",
            "repair": "risk_off",
            "strengthen": "defensive",
            "climax": "risk_off",
            "ebb": "risk_off",
        },
    },
}


def _reload_service():
    import app.advisor.regime.service as service

    service._CACHE.clear()
    service._CLOCK = lambda: datetime(2026, 8, 2, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    return service


def _raw(
    *,
    trade_date: str = "2026-08-02",
    sealed: list[dict] | None = None,
    broken: list[dict] | None = None,
    trend_features: dict | None = None,
    errors: list[str] | None = None,
) -> dict:
    return {
        "trade_date": trade_date,
        "sealed": sealed if sealed is not None else [{"board_count": 1}] * 8,
        "broken": broken if broken is not None else [{"board_count": 1}] * 2,
        "limit_down_count": 1,
        "ladder_max": 1,
        "trend_features": trend_features
        if trend_features is not None
        else {
            "ma_stack": "above",
            "drawdown_from_high": 0.05,
            "breadth": 0.62,
            "volume_vs_ma20": 1.1,
        },
        "errors": errors or [],
    }


@pytest.fixture()
def service(monkeypatch):
    svc = _reload_service()
    monkeypatch.setattr(svc, "load_config", lambda: {"regime": CFG})
    return svc


def test_failed_quality_forces_defensive(monkeypatch, service):
    monkeypatch.setattr(
        service.collector,
        "collect_raw",
        lambda trade_date=None: _raw(errors=["sealed: RuntimeError: zt failure"]),
    )
    monkeypatch.setattr(service.store, "get_daily", lambda trade_date: None)
    writes = []
    monkeypatch.setattr(
        service.store,
        "upsert_daily",
        lambda trade_date, doc: writes.append((trade_date, doc)),
    )

    out = service.get_current_regime(force=True)

    assert out["data_quality"] == "failed"
    assert out["gate_level"] == "defensive"
    assert out["position_cap"] == 0.35
    assert out["sentiment_cycle"] == "repair"
    assert any(e["key"] == "data_quality" and e["value"] == "failed" for e in out["evidence"])
    assert writes[0][0] == "2026-08-02"


def test_degraded_quality_when_promotion_rate_missing(monkeypatch, service):
    monkeypatch.setattr(service.collector, "collect_raw", lambda trade_date=None: _raw())
    monkeypatch.setattr(service.store, "get_daily", lambda trade_date: None)
    monkeypatch.setattr(service.store, "upsert_daily", lambda trade_date, doc: None)

    out = service.get_current_regime(force=True)

    assert out["data_quality"] == "degraded"
    assert out["trend_regime"] == "uptrend"
    assert out["promotion_rate"] is None


def _metrics_for_hysteresis(sentiment_score: float, sentiment_cycle: str = "strengthen") -> dict:
    return {
        "limit_up_count": 8,
        "limit_down_count": 1,
        "broken_count": 2,
        "seal_rate": 0.8,
        "break_rate": 0.2,
        "max_board": 1,
        "height_board_count": 0,
        "promotion_rate": 0.5,
        "sentiment_score": sentiment_score,
        "sentiment_cycle": sentiment_cycle,
        "evidence": [],
    }


def test_cycle_hysteresis_no_ebb_within_band(monkeypatch, service):
    """Score below strengthen but above strengthen−hysteresis must not flip to ebb."""
    monkeypatch.setattr(
        service,
        "compute_sentiment_metrics",
        lambda raw, prev, cfg=None: _metrics_for_hysteresis(0.52),
    )
    prev = {
        "trade_date": "2026-08-01",
        "sentiment_cycle": "climax",
        "gate_level": "normal",
    }
    out = service.build_regime_from_parts(_raw(), prev, cfg=CFG)
    assert out["sentiment_cycle"] == "strengthen"
    assert not any(
        e["key"] == "sentiment_cycle" and e["value"] == "ebb" for e in out["evidence"]
    )


def test_cycle_hysteresis_sets_ebb_below_strengthen_minus_hysteresis(monkeypatch, service):
    prev = {
        "trade_date": "2026-08-01",
        "by_board": {1: 20},
        "sentiment_cycle": "climax",
        "gate_level": "normal",
    }
    monkeypatch.setattr(
        service,
        "compute_sentiment_metrics",
        lambda raw, prev_daily, cfg=None: _metrics_for_hysteresis(0.49),
    )
    out = service.build_regime_from_parts(_raw(), prev, cfg=CFG)
    assert out["sentiment_cycle"] == "ebb"
    assert any(e["key"] == "sentiment_cycle" and e["value"] == "ebb" for e in out["evidence"])


def test_cache_returns_same_payload_until_force(monkeypatch, service):
    calls = []

    def collect(trade_date=None):
        calls.append(trade_date)
        return _raw(sealed=[{"board_count": 1}] * (8 + len(calls)))

    monkeypatch.setattr(service.collector, "collect_raw", collect)
    monkeypatch.setattr(service.store, "get_daily", lambda trade_date: None)
    monkeypatch.setattr(service.store, "upsert_daily", lambda trade_date, doc: None)

    first = service.get_current_regime(force=True)
    second = service.get_current_regime()
    third = service.get_current_regime(force=True)

    assert first == second
    assert third["limit_up_count"] == first["limit_up_count"] + 1
    assert len(calls) == 2


def test_history_and_sentiment_detail_use_archive(monkeypatch, service):
    rows = [
        {"trade_date": "2026-08-02", "sentiment_cycle": "ebb", "gate_level": "risk_off"},
        {
            "trade_date": "2026-08-01",
            "sentiment_cycle": "climax",
            "gate_level": "normal",
        },
    ]
    monkeypatch.setattr(service.store, "list_daily", lambda limit: rows[:limit])
    monkeypatch.setattr(service, "get_current_regime", lambda force=False: {"metrics": {"a": 1}, "sentiment_cycle": "ebb"})

    assert service.get_regime_history(limit=1) == [rows[0]]
    assert service.get_sentiment_detail() == {"metrics": {"a": 1}, "sentiment_cycle": "ebb"}
