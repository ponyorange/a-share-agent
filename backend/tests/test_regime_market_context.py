from __future__ import annotations

import pytest

from app.advisor import market_context


@pytest.fixture()
def neutral_symbol_context(monkeypatch):
    monkeypatch.setattr(
        market_context,
        "fetch_individual_flow_score",
        lambda symbol: {"score": 0.5, "ok": True},
    )
    monkeypatch.setattr(
        market_context,
        "sector_score_for_symbol",
        lambda symbol, industry_map=None: {"score": 0.5, "ok": True},
    )
    monkeypatch.setattr(
        market_context,
        "fetch_valuation_score",
        lambda symbol: {"score": 0.5, "ok": True},
    )


def test_enrich_symbol_context_maps_gate_level_to_market_score(monkeypatch, neutral_symbol_context):
    monkeypatch.setattr(
        market_context,
        "load_config",
        lambda: {"regime": {"use_for_market_score": True}},
    )

    out = market_context.enrich_symbol_context(
        "600000",
        market={"score": 0.9, "gate_level": "defensive"},
    )

    assert out["market_score"] == 0.35


def test_enrich_symbol_context_can_keep_legacy_market_score(monkeypatch, neutral_symbol_context):
    monkeypatch.setattr(
        market_context,
        "load_config",
        lambda: {"regime": {"use_for_market_score": False}},
    )

    out = market_context.enrich_symbol_context(
        "600000",
        market={"score": 0.9, "gate_level": "risk_off"},
    )

    assert out["market_score"] == 0.9
