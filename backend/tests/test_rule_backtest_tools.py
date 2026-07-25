import json

from app.advisor.agent.tools import build_tools


def _tool_map(user_id: str = "u1"):
    return {t.name: t for t in build_tools(user_id)}


def test_compile_knowledge_rules_validates():
    tools = _tool_map()
    raw = json.dumps(
        {
            "name": "t",
            "entry": {"all": [{"factor": "mom_5", "op": ">=", "value": 0.02}]},
        }
    )
    out = json.loads(
        tools["compile_knowledge_rules"].invoke(
            {
                "rule_json": raw,
                "text": "五日动量过滤",
            }
        )
    )
    assert out["ok"] is True
    assert out["rule"]["hold_days"] == 1
    assert "五日动量" in (out["rule"].get("natural_language_summary") or "")


def test_compile_knowledge_rules_bad_factor():
    tools = _tool_map()
    raw = json.dumps(
        {
            "entry": {"all": [{"factor": "nope", "op": ">=", "value": 1}]},
        }
    )
    out = json.loads(tools["compile_knowledge_rules"].invoke({"rule_json": raw}))
    assert out["ok"] is False
    assert out["errors"]


def test_optimize_knowledge_rules_json(monkeypatch):
    from app.advisor import rule_optimize as ro

    def fake_opt(spec, **kwargs):
        return {
            "ok": True,
            "objective": "C",
            "feasible": True,
            "truncated": True,
            "trials_run": 2,
            "best_spec": spec,
            "in_sample": {
                "total_return": 0.1,
                "sharpe": 0.2,
                "max_drawdown": 0.05,
                "trade_count": 8,
            },
            "out_of_sample": {
                "total_return": 0.02,
                "sharpe": 0.1,
                "max_drawdown": 0.1,
                "trade_count": 3,
            },
            "closest": None,
            "trial_log": [],
        }

    monkeypatch.setattr(ro, "optimize_rules", fake_opt)
    tools = _tool_map()
    raw = json.dumps(
        {
            "entry": {"all": [{"factor": "mom_5", "op": ">=", "value": 0.02}]},
        }
    )
    out = json.loads(
        tools["optimize_knowledge_rules"].invoke(
            {
                "rule_json": raw,
                "objective": "C",
                "symbols": "510300",
            }
        )
    )
    assert out["ok"] is True
    assert out["feasible"] is True
    assert "in_sample" in out
