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
