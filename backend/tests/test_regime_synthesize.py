from app.advisor.regime.synthesize import synthesize_gate


def test_downtrend_ebb_is_risk_off():
    out = synthesize_gate("downtrend", "ebb", cfg={
        "matrix": {"downtrend": {"ebb": "risk_off"}},
        "position_cap": {"risk_off": 0.15, "defensive": 0.35, "normal": 0.70, "aggressive": 0.85},
        "pool_policy": {"risk_off": "defense_only", "defensive": "shrink", "normal": "full", "aggressive": "full"},
    })
    assert out["gate_level"] == "risk_off"
    assert out["position_cap"] == 0.15
    assert out["pool_policy"] == "defense_only"


def test_uptrend_strengthen_is_aggressive():
    out = synthesize_gate("uptrend", "strengthen", cfg={
        "matrix": {"uptrend": {"strengthen": "aggressive"}},
        "position_cap": {"aggressive": 0.85, "normal": 0.70, "defensive": 0.35, "risk_off": 0.15},
        "pool_policy": {"aggressive": "full", "normal": "full", "defensive": "shrink", "risk_off": "defense_only"},
    })
    assert out["gate_level"] == "aggressive"
