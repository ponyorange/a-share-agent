"""Deterministic approval and paper-execution cost model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ExecutionOrder(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    asset_type: Literal["etf", "stock"]
    quote_price: float
    executed_price: float
    price: float
    gross_amount: float
    commission: float
    stamp_tax: float
    slippage: float
    total_fees: float
    net_cash: float
    name: str | None = None
    market_status_hash: str | None = None
    market_status_expires_at: str | None = None


def calculate_execution(
    *,
    symbol: str,
    side: Literal["buy", "sell"],
    qty: float,
    quote_price: float,
    commission_rate: float,
    minimum_commission: float,
    stamp_tax_rate: float,
    slippage_bps: float,
    name: str | None = None,
    market_status_hash: str | None = None,
    market_status_expires_at: str | None = None,
) -> ExecutionOrder:
    asset_type = (
        "etf" if symbol.startswith(("5", "15", "16")) else "stock"
    )
    multiplier = 1 + (slippage_bps / 10_000) * (
        1 if side == "buy" else -1
    )
    executed_price = float(quote_price) * multiplier
    gross = executed_price * float(qty)
    commission = max(gross * commission_rate, minimum_commission)
    stamp_tax = (
        gross * stamp_tax_rate
        if side == "sell" and asset_type == "stock"
        else 0.0
    )
    slippage = abs(executed_price - float(quote_price)) * float(qty)
    total_fees = commission + stamp_tax + slippage
    net_cash = (
        -(gross + commission + stamp_tax)
        if side == "buy"
        else gross - commission - stamp_tax
    )
    return ExecutionOrder(
        symbol=symbol,
        side=side,
        qty=qty,
        asset_type=asset_type,
        quote_price=quote_price,
        executed_price=executed_price,
        price=executed_price,
        gross_amount=gross,
        commission=commission,
        stamp_tax=stamp_tax,
        slippage=slippage,
        total_fees=total_fees,
        net_cash=net_cash,
        name=name,
        market_status_hash=market_status_hash,
        market_status_expires_at=market_status_expires_at,
    )
