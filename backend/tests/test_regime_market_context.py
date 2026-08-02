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


@pytest.fixture()
def use_gate_for_market_score(monkeypatch):
    monkeypatch.setattr(
        market_context,
        "load_config",
        lambda: {"regime": {"use_for_market_score": True}},
    )


@pytest.mark.parametrize(
    ("gate_level", "expected_score"),
    [
        ("aggressive", 0.8),
        ("normal", 0.55),
        ("defensive", 0.35),
        ("risk_off", 0.2),
    ],
)
def test_enrich_symbol_context_maps_gate_level_to_market_score(
    monkeypatch,
    neutral_symbol_context,
    use_gate_for_market_score,
    gate_level,
    expected_score,
):
    out = market_context.enrich_symbol_context(
        "600000",
        market={"score": 0.9, "gate_level": gate_level},
    )

    assert out["market_score"] == expected_score


def test_enrich_symbol_context_resolves_gate_level_from_get_current_regime(
    monkeypatch,
    neutral_symbol_context,
    use_gate_for_market_score,
):
    monkeypatch.setattr(
        "app.advisor.regime.service.get_current_regime",
        lambda force=False: {"gate_level": "risk_off"},
    )

    out = market_context.enrich_symbol_context(
        "600000",
        market={"score": 0.9},
    )

    assert out["market_score"] == 0.2


def test_enrich_symbol_context_falls_back_to_legacy_score_when_regime_lookup_fails(
    monkeypatch,
    neutral_symbol_context,
    use_gate_for_market_score,
):
    def _raise():
        raise RuntimeError("regime unavailable")

    monkeypatch.setattr(
        "app.advisor.regime.service.get_current_regime",
        _raise,
    )

    out = market_context.enrich_symbol_context(
        "600000",
        market={"score": 0.9},
    )

    assert out["market_score"] == 0.9


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
