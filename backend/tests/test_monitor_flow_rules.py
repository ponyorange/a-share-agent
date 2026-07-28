from app.advisor.monitor.rules import evaluate_flow_rule


def test_flow_ratio_hit():
    flow = {
        "ok": True,
        "net_inflow": 5e7,
        "avg_net_inflow": 1e7,
        "amount": 4e8,
        "ratio": 0.125,
    }
    assert (
        evaluate_flow_rule(
            {"type": "flow_spike_in", "value": 0.10, "mult": 3}, flow
        )
        is True
    )


def test_flow_relative_hit():
    flow = {
        "ok": True,
        "net_inflow": -9e7,
        "avg_net_inflow": -2e7,
        "amount": None,
        "ratio": None,
    }
    assert (
        evaluate_flow_rule(
            {"type": "flow_spike_out", "value": 0.10, "mult": 3}, flow
        )
        is True
    )


def test_flow_missing_skips():
    assert (
        evaluate_flow_rule({"type": "flow_spike_in", "value": 0.1}, {"ok": False})
        is False
    )


def test_flow_mean_floor_skips_relative_uses_ratio_only():
    flow = {
        "ok": True,
        "net_inflow": 100.0,
        "avg_net_inflow": 10.0,
        "amount": 1e9,
        "ratio": 0.0001,
    }
    assert (
        evaluate_flow_rule(
            {"type": "flow_spike_in", "value": 0.10, "mult": 3}, flow
        )
        is False
    )
