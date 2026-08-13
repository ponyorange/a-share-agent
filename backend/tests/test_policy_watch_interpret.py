import json

import pytest

from app.advisor.policy_watch.interpret import (
    interpret_pending,
    parse_interpretation,
    verify_symbols,
)


def test_parse_interpretation_json_and_fence():
    raw = {
        "impact_score": 0.8,
        "direction": "up",
        "summary": "新能源政策",
        "sectors": [{"name": "新能源", "reason": "补贴"}],
        "symbols": [{"symbol": "300750", "name": "宁德时代", "reason": "x", "direction": "up"}],
        "category": "policy",
    }
    parsed = parse_interpretation(json.dumps(raw, ensure_ascii=False))
    assert parsed["impact_score"] == 0.8
    assert parsed["direction"] == "up"
    fenced = parse_interpretation("```json\n" + json.dumps(raw, ensure_ascii=False) + "\n```")
    assert fenced["category"] == "policy"
    with pytest.raises(ValueError):
        parse_interpretation('{"summary":"no score"}')


def test_verify_symbols_keeps_codes():
    out = verify_symbols(
        [
            {"symbol": "300750", "name": "宁德时代"},
            {"name": "苹果公司"},
            {"symbol": "not-a-code", "name": "乱码"},
        ]
    )
    assert any(x.get("symbol") == "300750" and x.get("verified") is True for x in out)
    assert any(x.get("name") == "苹果公司" and x.get("verified") is False for x in out)
    assert all(x.get("name") != "乱码" or x.get("verified") is False for x in out)


def test_interpret_pending_saves_ready(monkeypatch):
    saved = []

    class _Model:
        def invoke(self, _msgs):
            return type(
                "R",
                (),
                {
                    "content": json.dumps(
                        {
                            "impact_score": 0.7,
                            "direction": "up",
                            "summary": "ok",
                            "sectors": [{"name": "新能源", "reason": "x"}],
                            "symbols": [{"symbol": "300750", "name": "宁德时代"}],
                            "category": "policy",
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    monkeypatch.setattr(
        "app.advisor.policy_watch.interpret.list_pending_interpret",
        lambda limit=None: [
            {
                "url_key": "https://www.gov.cn/a",
                "title": "新政",
                "source_label": "政府网",
                "body_excerpt": "正文" * 20,
                "body_ok": True,
            }
        ],
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.interpret.pick_interpret_user_id",
        lambda: "u1",
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.interpret.build_chat_model",
        lambda *a, **k: _Model(),
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.interpret.save_interpretation",
        lambda url_key, interpretation, status: saved.append(
            (url_key, interpretation, status)
        ),
    )
    monkeypatch.setattr(
        "app.advisor.policy_watch.interpret.policy_watch_config",
        lambda: {"max_article_chars": 8000, "max_fetch_per_tick": 5},
    )
    out = interpret_pending()
    assert out["ok"] == 1
    assert saved[0][2] == "ready"
    assert saved[0][1]["impact_score"] == 0.7
