from app.advisor import rule_optimize as ro
from app.advisor import rule_backtest as rb


def test_score_objective_c_feasibility():
    ok_m = {
        "total_return": 0.1,
        "sharpe": 0.5,
        "max_drawdown": 0.1,
        "trade_count": 10,
    }
    bad_m = {
        "total_return": 0.5,
        "sharpe": -1.0,
        "max_drawdown": 0.5,
        "trade_count": 10,
    }
    f1, s1 = ro.score_objective(ok_m, "C", min_sharpe=0.0, max_dd=0.25)
    f2, s2 = ro.score_objective(bad_m, "C", min_sharpe=0.0, max_dd=0.25)
    assert f1 and s1 == 0.1
    assert not f2


def test_optimize_respects_trial_budget(monkeypatch):
    spec, _ = rb.validate_rule_spec(
        {
            "entry": {"all": [{"factor": "mom_5", "op": ">=", "value": 0.02}]},
        }
    )
    calls = {"n": 0}

    def fake_report(s, **kwargs):
        calls["n"] += 1
        return {
            "ok": True,
            "metrics": {
                "total_return": 0.01 * calls["n"],
                "sharpe": 0.2,
                "max_drawdown": 0.05,
                "trade_count": 10,
                "hit_rate": 0.5,
                "sample_count": 50,
            },
            "in_sample": {
                "total_return": 0.01 * calls["n"],
                "sharpe": 0.2,
                "max_drawdown": 0.05,
                "trade_count": 10,
            },
            "out_of_sample": {
                "total_return": 0.005,
                "sharpe": 0.1,
                "max_drawdown": 0.08,
                "trade_count": 3,
            },
        }

    monkeypatch.setattr(rb, "run_rule_backtest_report", fake_report)
    out = ro.optimize_rules(spec, objective="A", max_trials=5, seed=1)
    assert out["trials_run"] <= 5
    assert out["truncated"] is True
    assert out["best_spec"] is not None
