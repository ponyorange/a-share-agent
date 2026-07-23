from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.advisor.committee.models import (
    BacktestVerdict,
    EvidenceRef,
    Freshness,
    TradeDirection,
    TradeProposal,
    VerdictStatus,
)
from app.advisor.committee.risk import (
    RiskInputs,
    RiskLimits,
    create_risk_provider,
    evaluate_risk,
    proposal_semantics_hash,
)


NOW = datetime(2026, 1, 8, 15, tzinfo=timezone.utc)


def proposal(**patch) -> TradeProposal:
    values = {
        "user_id": "u",
        "run_id": "r",
        "symbol": "510300",
        "direction": TradeDirection.BUY,
        "target_weight": 0.2,
        "confidence": 0.8,
        "rationale": "fixed",
        "limit_price": 10,
        "stop_price": 9,
        "expires_at": NOW + timedelta(days=1),
        "created_at": NOW - timedelta(days=1),
        "evidence_refs": (
            EvidenceRef(
                user_id="u",
                run_id="r",
                evidence_id="e",
                source="fake",
                captured_at=NOW,
                data_as_of=NOW,
                freshness=Freshness.FRESH,
            ),
        ),
    }
    values.update(patch)
    return TradeProposal(**values)


def backtest(proposal_value=None, **metrics) -> BacktestVerdict:
    base = {
        "sample_count": 60,
        "trade_count": 5,
        "max_drawdown": 0.1,
    }
    base.update(metrics)
    return BacktestVerdict(
        user_id="u",
        run_id="r",
        passed=True,
        score=0.8,
        metrics=base,
        summary="ok",
        proposal_hash=proposal_semantics_hash(
            proposal_value or proposal()
        ),
        created_at=NOW,
    )


def limits(**patch) -> RiskLimits:
    values = {
        "max_single_position": 0.25,
        "max_total_exposure": 0.8,
        "max_sector_concentration": 0.4,
        "min_average_turnover": 10_000_000,
        "max_annualized_volatility": 0.45,
        "max_portfolio_drawdown": 0.2,
        "min_samples": 40,
        "min_trades": 3,
        "min_evidence_quality": 0.8,
        "max_data_age_seconds": 86_400,
        "max_market_status_age_seconds": 30,
        "max_price_deviation": 0.03,
        "t_plus_one": True,
    }
    values.update(patch)
    return RiskLimits.model_validate(values)


def inputs(**patch) -> RiskInputs:
    values = {
        "as_of": NOW,
        "current_price": 10,
        "average_turnover": 20_000_000,
        "annualized_volatility": 0.2,
        "current_total_exposure": 0.4,
        "current_symbol_weight": 0,
        "sector": "宽基ETF",
        "sector_exposure": 0.1,
        "data_as_of": NOW,
        "evidence_quality": 1,
        "sellable_quantity": 100,
        "requested_quantity": 100,
    }
    values.update(patch)
    return RiskInputs(**values)


def test_all_hard_rules_are_emitted_and_clean_proposal_is_approved():
    value = proposal()
    verdict = evaluate_risk(value, backtest(value), inputs(), limits())
    assert verdict.status is VerdictStatus.APPROVED
    assert verdict.approved_weight == 0.2
    assert {rule.rule_id for rule in verdict.rules} == {
        "single_position",
        "total_exposure",
        "sector_concentration",
        "minimum_liquidity",
        "maximum_volatility",
        "portfolio_max_drawdown",
        "minimum_samples",
        "minimum_trades",
        "evidence_quality",
        "data_freshness",
        "proposal_expiry",
        "price_deviation",
        "t_plus_one",
    }
    assert all(rule.severity == "pass" for rule in verdict.rules)
    assert verdict.proposal_hash == proposal_semantics_hash(proposal())


@pytest.mark.parametrize(
    ("rule_id", "proposal_patch", "backtest_patch", "input_patch"),
    [
        ("single_position", {"target_weight": 0.3}, {}, {}),
        ("total_exposure", {}, {}, {"current_total_exposure": 0.75}),
        ("sector_concentration", {}, {}, {"sector_exposure": 0.3}),
        ("minimum_liquidity", {}, {}, {"average_turnover": 1}),
        ("maximum_volatility", {}, {}, {"annualized_volatility": 0.8}),
        ("portfolio_max_drawdown", {}, {"max_drawdown": 0.3}, {}),
        ("minimum_samples", {}, {"sample_count": 5}, {}),
        ("minimum_trades", {}, {"trade_count": 1}, {}),
        ("evidence_quality", {}, {}, {"evidence_quality": 0.2}),
        (
            "data_freshness",
            {},
            {},
            {"data_as_of": NOW - timedelta(days=2)},
        ),
        (
            "proposal_expiry",
            {"expires_at": NOW - timedelta(seconds=1)},
            {},
            {},
        ),
        ("price_deviation", {"limit_price": 11}, {}, {}),
        (
            "t_plus_one",
            {"direction": TradeDirection.SELL},
            {},
            {"sellable_quantity": 0},
        ),
    ],
)
def test_each_hard_rule_rejects_and_zeros_size(
    rule_id, proposal_patch, backtest_patch, input_patch
):
    value = proposal(**proposal_patch)
    verdict = evaluate_risk(
        value,
        backtest(value, **backtest_patch),
        inputs(**input_patch),
        limits(),
    )
    failed = {rule.rule_id: rule for rule in verdict.rules if rule.severity == "hard"}
    assert rule_id in failed
    assert verdict.status is VerdictStatus.REJECTED
    assert verdict.approved_weight == 0
    assert verdict.max_position == 0
    assert failed[rule_id].message
    assert failed[rule_id].observed is not None
    assert failed[rule_id].limit is not None


def test_missing_or_invalid_risk_config_fails_closed():
    for value in (
        {},
        limits().model_dump() | {"max_single_position": 2},
        limits().model_dump() | {"max_data_age_seconds": 0},
    ):
        with pytest.raises(ValueError):
            RiskLimits.from_mapping(value)


def test_proposal_hash_protects_trade_semantics_but_not_rationale():
    original = proposal()
    original_hash = proposal_semantics_hash(original)
    assert proposal_semantics_hash(
        original.model_copy(update={"rationale": "rewritten"})
    ) == original_hash
    for patch in (
        {"symbol": "159915"},
        {"direction": TradeDirection.SELL},
        {"target_weight": 0.1},
        {"stop_price": 8.5},
        {"expires_at": NOW + timedelta(days=2)},
    ):
        assert proposal_semantics_hash(original.model_copy(update=patch)) != original_hash


def test_reviewed_hash_mismatch_forces_re_review():
    verdict = evaluate_risk(
        proposal(target_weight=0.1),
        backtest(proposal(target_weight=0.1)),
        inputs(),
        limits(),
        expected_proposal_hash=proposal_semantics_hash(proposal()),
    )
    assert verdict.status is VerdictStatus.NEEDS_REVISION
    assert verdict.approved_weight == 0
    assert any(rule.rule_id == "proposal_semantics" for rule in verdict.rules)


def test_async_risk_provider_has_no_llm_and_uses_injected_market_provider():
    calls = []

    async def market_provider(symbol, as_of):
        calls.append((symbol, as_of))
        return inputs()

    provider = create_risk_provider(
        market_provider=market_provider,
        config=limits(),
    )

    class Snapshot:
        as_of = NOW

    class Context:
        user_id = "u"
        run_id = "r"
        snapshot = Snapshot()

    value = proposal()
    verdict = asyncio.run(provider(value, backtest(value), Context()))
    assert verdict.status is VerdictStatus.APPROVED
    assert calls == [("510300", NOW)]


def test_package_exports_async_risk_provider_adapter():
    import app.advisor.committee as committee

    assert committee.create_risk_provider is create_risk_provider
