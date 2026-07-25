import numpy as np
import pandas as pd

from app.advisor import rule_backtest as rb


def test_validate_rule_spec_ok_normalizes_defaults():
    raw = {
        "name": "动量",
        "entry": {"all": [{"factor": "mom_5", "op": ">=", "value": 0.03}]},
    }
    spec, errs = rb.validate_rule_spec(raw)
    assert errs == []
    assert spec["version"] == 1
    assert spec["action"] == "buy"
    assert spec["hold_days"] == 1
    assert spec["exit"]["any"] == [{"type": "hold_days"}]


def test_validate_rule_spec_rejects_bad_factor():
    raw = {
        "entry": {"all": [{"factor": "foo", "op": ">=", "value": 1}]},
    }
    spec, errs = rb.validate_rule_spec(raw)
    assert spec is None
    assert any("factor" in e for e in errs)


def test_eval_condition_between_and_nan_false():
    assert rb.eval_condition(
        {"factor": "mom_5", "op": "between", "value": [0.01, 0.05]},
        {"mom_5": 0.03},
    )
    assert not rb.eval_condition(
        {"factor": "mom_5", "op": ">=", "value": 0.0},
        {"mom_5": float("nan")},
    )


def test_entry_matches_all():
    spec = {
        "entry": {
            "all": [
                {"factor": "mom_5", "op": ">=", "value": 0.02},
                {"factor": "ma20_bias", "op": ">", "value": 0.0},
            ]
        }
    }
    assert rb.entry_matches(spec, {"mom_5": 0.03, "ma20_bias": 0.01})
    assert not rb.entry_matches(spec, {"mom_5": 0.01, "ma20_bias": 0.01})


def _synth_df(n: int = 80, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.002, 0.01, size=n)
    close = 100 * np.cumprod(1 + rets)
    vol = rng.integers(1_000_000, 2_000_000, size=n).astype(float)
    vol[-10:] *= 3
    times = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "time": times.strftime("%Y-%m-%d"),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": vol,
            "amount": close * vol,
        }
    )


def test_split_bar_range_70_30():
    train_end, n = rb.split_bar_range(100, 0.7)
    assert n == 100
    assert train_end == 70


def test_simulate_symbol_produces_trades():
    df = _synth_df(90)
    spec, errs = rb.validate_rule_spec(
        {
            "hold_days": 1,
            "entry": {"all": [{"factor": "mom_5", "op": ">", "value": -1.0}]},
        }
    )
    assert errs == []
    out = rb.simulate_symbol(df, None, spec, sample_step=1)
    assert out["trade_count"] >= 5
    m = rb.metrics_from_trades(out["trades"], out["equity_rets"])
    assert "total_return" in m and "sharpe" in m and "max_drawdown" in m
    assert m["trade_count"] == out["trade_count"]


def test_run_rule_backtest_report_split(monkeypatch):
    df = _synth_df(100)

    def fake_fetch(symbol):
        return symbol, df.copy()

    monkeypatch.setattr(rb, "fetch_daily_df", fake_fetch)
    monkeypatch.setattr(rb, "load_benchmark", lambda: None)
    monkeypatch.setattr(rb, "resolve_symbols", lambda symbols=None: ["AAA", "BBB"])

    spec, _ = rb.validate_rule_spec(
        {
            "hold_days": 1,
            "entry": {"all": [{"factor": "mom_5", "op": ">", "value": -1.0}]},
        }
    )
    report = rb.run_rule_backtest_report(spec, symbols=["AAA", "BBB"], segment="all")
    assert report["ok"] is True
    assert "in_sample" in report and "out_of_sample" in report
    assert set(report["in_sample"]) >= {
        "total_return",
        "sharpe",
        "max_drawdown",
        "trade_count",
    }
    assert set(report["out_of_sample"]) >= {
        "total_return",
        "sharpe",
        "max_drawdown",
        "trade_count",
    }
