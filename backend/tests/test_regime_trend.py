from app.advisor.regime.trend import classify_trend

CFG = {
    "trend_rules": {
        "uptrend_breadth_min": 0.55,
        "uptrend_drawdown_max": 0.12,
        "downtrend_drawdown_min": 0.18,
    }
}


def test_uptrend_when_ma_above_and_breadth_ok():
    out = classify_trend({
        "ma_stack": "above", "drawdown_from_high": 0.05,
        "breadth": 0.62, "volume_vs_ma20": 1.1,
    })
    assert out["trend_regime"] == "uptrend"


def test_downtrend_when_ma_below_and_deep_drawdown():
    out = classify_trend({
        "ma_stack": "below", "drawdown_from_high": 0.22,
        "breadth": 0.30, "volume_vs_ma20": 0.8,
    })
    assert out["trend_regime"] == "downtrend"


def test_range_when_mixed_ma_stack():
    out = classify_trend({
        "ma_stack": "mixed", "drawdown_from_high": 0.08,
        "breadth": 0.50, "volume_vs_ma20": 1.0,
    }, cfg=CFG)
    assert out["trend_regime"] == "range"


def test_downtrend_on_drawdown_alone():
    out = classify_trend({
        "ma_stack": "above", "drawdown_from_high": 0.20,
        "breadth": 0.60, "volume_vs_ma20": 1.0,
    })
    assert out["trend_regime"] == "downtrend"


def test_uptrend_requires_all_conditions():
    out = classify_trend({
        "ma_stack": "above", "drawdown_from_high": 0.05,
        "breadth": 0.40, "volume_vs_ma20": 1.0,
    })
    assert out["trend_regime"] == "range"


def test_evidence_includes_feature_snapshot():
    out = classify_trend({
        "ma_stack": "above", "drawdown_from_high": 0.05,
        "breadth": 0.62, "volume_vs_ma20": 1.1,
    })
    keys = {e["key"] for e in out["evidence"]}
    assert "ma_stack" in keys
    assert "trend_regime" in keys
