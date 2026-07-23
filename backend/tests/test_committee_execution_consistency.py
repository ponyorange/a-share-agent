from __future__ import annotations

import pytest

from app.advisor import paper
from app.advisor.committee.execution_costs import calculate_execution
from tests.test_paper_atomic_flow import Database


def test_fee_and_slippage_model_matches_hand_calculation():
    etf_buy = calculate_execution(
        symbol="510300",
        side="buy",
        qty=100,
        quote_price=10,
        commission_rate=0.0003,
        minimum_commission=5,
        stamp_tax_rate=0.001,
        slippage_bps=10,
    )
    assert etf_buy.executed_price == pytest.approx(10.01)
    assert etf_buy.gross_amount == pytest.approx(1001)
    assert etf_buy.commission == pytest.approx(5)
    assert etf_buy.stamp_tax == 0
    assert etf_buy.slippage == pytest.approx(1)
    assert etf_buy.total_fees == pytest.approx(6)
    assert etf_buy.net_cash == pytest.approx(-1006)

    stock_sell = calculate_execution(
        symbol="600000",
        side="sell",
        qty=100,
        quote_price=10,
        commission_rate=0.0003,
        minimum_commission=5,
        stamp_tax_rate=0.001,
        slippage_bps=10,
    )
    assert stock_sell.executed_price == pytest.approx(9.99)
    assert stock_sell.stamp_tax == pytest.approx(0.999)
    assert stock_sell.net_cash == pytest.approx(993.001)


def test_paper_executor_uses_bound_execution_cash_and_trade_fields(monkeypatch):
    database = Database()
    monkeypatch.setattr(paper, "get_db", lambda: database)
    execution = calculate_execution(
        symbol="510300",
        side="buy",
        qty=100,
        quote_price=10,
        commission_rate=0.0003,
        minimum_commission=5,
        stamp_tax_rate=0.001,
        slippage_bps=10,
    )
    result = paper.place_orders_atomic(
        user_id="u",
        orders=[execution.model_dump(mode="python")],
        external_idempotency_key="fees",
        mutation_source="committee_approval:r",
        expected_account_version=0,
        lease_owner="owner",
        lease_renew=lambda: None,
    )

    trade = result["trades"][0]
    assert result["account"]["cash"] == pytest.approx(98_994)
    for field in (
        "quote_price",
        "executed_price",
        "gross_amount",
        "commission",
        "stamp_tax",
        "slippage",
        "total_fees",
        "net_cash",
    ):
        assert trade[field] == pytest.approx(
            execution.model_dump()[field]
        )
