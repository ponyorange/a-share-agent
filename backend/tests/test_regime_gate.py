from app.advisor.regime.gate import apply_regime_gate


def _risk_off_regime() -> dict:
    return {
        "gate_level": "risk_off",
        "position_cap": 0.15,
        "pool_policy": "defense_only",
        "data_quality": "ok",
    }


def test_apply_regime_gate_preserves_board_filtered_items():
    result = {
        "board": "hs",
        "count": 1,
        "items": [{"symbol": "000001", "action": "hold", "score": 0.5}],
        "boards": {
            "hs": {
                "items": [{"symbol": "000001", "action": "hold", "score": 0.5}],
            },
            "etf": {
                "items": [{"symbol": "510300", "action": "buy", "score": 0.9}],
            },
        },
    }

    out = apply_regime_gate(result, _risk_off_regime(), override=False)

    symbols = [i["symbol"] for i in out["items"]]
    assert symbols == ["000001"]
    assert "510300" not in symbols
    assert out["count"] == 1
    assert out["boards"]["etf"]["items"][0]["action"] == "watch"


def test_risk_off_blocks_buys():
    result = {"items": [{"symbol": "000001", "action": "buy", "score": 0.9}]}

    out = apply_regime_gate(result, _risk_off_regime(), override=False)

    assert out["gate_blocked_buys"] is True
    assert out["items"][0]["action"] == "watch"
    assert out["regime"]["gate_level"] == "risk_off"
    assert out["regime"]["override_applied"] is False


def test_risk_off_blocks_adds_to_hold():
    result = {
        "items": [
            {"symbol": "000001", "action": "add", "score": 0.9, "has_position": True}
        ]
    }

    out = apply_regime_gate(result, _risk_off_regime(), override=False)

    assert out["gate_blocked_buys"] is True
    assert out["items"][0]["action"] == "hold"


def test_risk_off_override_warns():
    result = {"items": [{"symbol": "000001", "action": "buy", "score": 0.9}] * 20}

    out = apply_regime_gate(
        result,
        _risk_off_regime(),
        override=True,
        cfg={
            "shrink_top_k": 8,
            "pool_policy": {"defensive": "shrink"},
            "position_cap": {"defensive": 0.35},
        },
    )

    assert out["regime"]["override_applied"] is True
    assert out["regime"]["gate_level"] == "defensive"
    assert out.get("warnings")
    assert len(out["items"]) <= 8


def test_snapshot_as_recommendations_applies_regime_gate(monkeypatch):
    from app.advisor import snapshots

    monkeypatch.setattr(
        snapshots,
        "get_snapshot",
        lambda trade_date, user_id=None: {
            "trade_date": trade_date,
            "user_id": user_id,
            "as_of": trade_date,
            "boards": {
                "hs": {
                    "label": "沪深",
                    "items": [
                        {"symbol": "000001", "action": "buy", "score": 0.9},
                    ],
                }
            },
        },
    )
    monkeypatch.setattr(snapshots, "get_current_regime", lambda: _risk_off_regime())

    out = snapshots.snapshot_as_recommendations("2026-08-02", board="hs", user_id="u1")

    assert out is not None
    assert out["items"][0]["action"] == "watch"
    assert out["regime"]["gate_level"] == "risk_off"


def test_snapshot_keeps_ungated_actions_for_regime_override(monkeypatch):
    """A gated response must not permanently overwrite the archived score/actions."""
    from app.advisor import snapshots

    stored = {}

    class _Snapshots:
        def update_one(self, flt, update, upsert=False):
            stored[flt["trade_date"]] = {
                **flt,
                **update["$set"],
                **update.get("$setOnInsert", {}),
            }

        def find_one(self, flt, projection):
            return stored.get(flt["trade_date"])

    monkeypatch.setattr(
        snapshots,
        "get_db",
        lambda: type("DB", (), {"rec_snapshots": _Snapshots()})(),
    )
    monkeypatch.setattr(snapshots, "get_current_regime", lambda: _risk_off_regime())
    raw = {
        "as_of": "2026-08-03",
        "boards": {
            "hs": {
                "label": "沪深",
                "items": [{"symbol": "000001", "action": "buy", "score": 0.9}],
            }
        },
    }
    gated = apply_regime_gate(raw, _risk_off_regime())

    snapshots.save_snapshot(gated, "2026-08-03", user_id="u1", raw_payload=raw)

    default = snapshots.snapshot_as_recommendations(
        "2026-08-03", board="hs", user_id="u1"
    )
    overridden = snapshots.snapshot_as_recommendations(
        "2026-08-03", board="hs", user_id="u1", regime_override=True
    )

    assert default["items"][0]["action"] == "watch"
    assert overridden["items"][0]["action"] == "buy"
