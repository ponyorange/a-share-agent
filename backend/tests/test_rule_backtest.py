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


def test_validate_alias_and_default_lookback():
    spec, errs = rb.validate_rule_spec(
        {
            "entry": {
                "all": [{"factor": "volume_ratio", "op": "<", "value": 0.8}]
            }
        }
    )
    assert errs == []
    assert spec["entry"]["all"][0]["factor"] == "vol_ratio"
    assert spec["entry"]["all"][0]["lookback"] == 5


def test_validate_lookback_bounds():
    _, errs_lo = rb.validate_rule_spec(
        {
            "entry": {
                "all": [
                    {"factor": "vol_ratio", "lookback": 1, "op": "<", "value": 1}
                ]
            }
        }
    )
    assert any("lookback" in e for e in errs_lo)
    _, errs_hi = rb.validate_rule_spec(
        {
            "entry": {
                "all": [
                    {"factor": "vol_ratio", "lookback": 99, "op": "<", "value": 1}
                ]
            }
        }
    )
    assert any("lookback" in e for e in errs_hi)


def test_validate_rejects_absolute_volume():
    _, errs = rb.validate_rule_spec(
        {"entry": {"all": [{"factor": "volume", "op": "<", "value": 1e6}]}}
    )
    assert any("vol_ratio" in e for e in errs)


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


def test_yin_yang_and_vol_ratio_lookback_on_synth():
    from app.advisor.features import compute_factors, volume_ratio_last

    df = _synth_df(40)
    # prior 5 days low vol, prior 6-10 high vol → r5 vs r10 differ
    df.loc[df.index[-11:-6], "volume"] = 5_000_000.0
    df.loc[df.index[-6:-1], "volume"] = 1_000_000.0
    df.loc[df.index[-1], "volume"] = 500_000.0
    df.loc[df.index[-1], "open"] = float(df.iloc[-1]["close"]) * 1.02
    factors = compute_factors(df, None)
    assert factors["is_yin"] == 1.0
    assert factors["is_yang"] == 0.0
    r5 = volume_ratio_last(df, 5)
    r10 = volume_ratio_last(df, 10)
    assert r5 < 1.0
    assert r10 < r5  # 10日均量被前半高量抬高 → 比值更小
    spec, errs = rb.validate_rule_spec(
        {
            "entry": {
                "all": [
                    {"factor": "is_yin", "op": ">=", "value": 1},
                    {
                        "factor": "vol_ratio",
                        "lookback": 5,
                        "op": "<",
                        "value": 1.0,
                    },
                ]
            }
        }
    )
    assert errs == []
    assert rb.entry_matches(spec, factors, df=df)


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
