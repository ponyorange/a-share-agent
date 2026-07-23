"""Deterministic, non-LLM risk vetoes for committee trade proposals."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    BacktestVerdict,
    RiskRuleResult,
    RiskVerdict,
    TradeDirection,
    TradeProposal,
    VerdictStatus,
)


class RiskLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_single_position: float = Field(gt=0, le=1)
    max_total_exposure: float = Field(gt=0, le=1)
    max_sector_concentration: float = Field(gt=0, le=1)
    min_average_turnover: float = Field(gt=0)
    max_annualized_volatility: float = Field(gt=0)
    max_portfolio_drawdown: float = Field(gt=0, le=1)
    min_samples: int = Field(ge=2)
    min_trades: int = Field(ge=1)
    min_evidence_quality: float = Field(gt=0, le=1)
    max_data_age_seconds: float = Field(gt=0)
    max_market_status_age_seconds: float = Field(gt=0)
    max_price_deviation: float = Field(gt=0, le=1)
    t_plus_one: Literal[True]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> RiskLimits:
        missing = set(cls.model_fields).difference(value)
        if missing:
            raise ValueError(
                "missing committee.risk_limits settings: "
                + ", ".join(sorted(missing))
            )
        return cls.model_validate(value)


class RiskInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    as_of: datetime
    current_price: float = Field(gt=0)
    average_turnover: float = Field(ge=0)
    annualized_volatility: float = Field(ge=0)
    current_total_exposure: float = Field(ge=0, le=1)
    current_symbol_weight: float = Field(ge=0, le=1)
    sector: str = Field(min_length=1)
    sector_exposure: float = Field(ge=0, le=1)
    data_as_of: datetime
    captured_at: datetime | None = None
    evidence_quality: float = Field(ge=0, le=1)
    sellable_quantity: float = Field(ge=0)
    requested_quantity: float = Field(gt=0)

    @field_validator("as_of", "data_as_of", "captured_at")
    @classmethod
    def utc_only(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("risk timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def reject_future_data(self) -> RiskInputs:
        if self.data_as_of > self.as_of:
            raise ValueError("future data_as_of is forbidden")
        if self.captured_at is not None and self.captured_at > self.as_of:
            raise ValueError("future captured_at is forbidden")
        return self


def proposal_semantics_hash(
    proposals: TradeProposal | Sequence[TradeProposal],
) -> str:
    """Canonical hash of every field that can change execution semantics."""
    items = (
        (proposals,)
        if isinstance(proposals, TradeProposal)
        else tuple(proposals)
    )
    if not items:
        raise ValueError("proposal hash requires at least one proposal")
    payload = [
        {
            "strategy_id": item.strategy_id,
            "strategy_version": item.strategy_version,
            "created_at": item.created_at.astimezone(timezone.utc).isoformat(),
            "strategy_template_as_of": (
                None
                if item.strategy_template_as_of is None
                else item.strategy_template_as_of.astimezone(
                    timezone.utc
                ).isoformat()
            ),
            "symbol": item.symbol,
            "action": item.direction.value,
            "size": item.target_weight,
            "order_type": item.order_type,
            "time_in_force": item.time_in_force,
            "limit_price": item.limit_price,
            "stop": item.stop_price,
            "expiry": (
                None
                if item.expires_at is None
                else item.expires_at.astimezone(timezone.utc).isoformat()
            ),
        }
        for item in sorted(
            items,
            key=lambda value: (
                value.symbol,
                value.created_at,
                value.direction.value,
            ),
        )
    ]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rule(
    rule_id: str,
    *,
    passed: bool,
    observed: Any,
    limit: Any,
    message: str,
) -> RiskRuleResult:
    return RiskRuleResult(
        rule_id=rule_id,
        observed=observed,
        limit=limit,
        severity="pass" if passed else "hard",
        message=message,
    )


def evaluate_risk(
    proposal: TradeProposal,
    backtest: BacktestVerdict,
    market: RiskInputs,
    limits: RiskLimits,
    *,
    expected_proposal_hash: str | None = None,
) -> RiskVerdict:
    if proposal.user_id != backtest.user_id or proposal.run_id != backtest.run_id:
        raise ValueError("risk artifacts scope mismatch")
    semantic_hash = proposal_semantics_hash(proposal)
    if backtest.proposal_hash != semantic_hash:
        expected_proposal_hash = backtest.proposal_hash
    rules: list[RiskRuleResult] = []
    revision = False
    if expected_proposal_hash is not None and expected_proposal_hash != semantic_hash:
        revision = True
        rules.append(
            _rule(
                "proposal_semantics",
                passed=False,
                observed=semantic_hash,
                limit=expected_proposal_hash,
                message="交易语义已变化，必须重新回测和风控",
            )
        )

    buy_weight = (
        proposal.target_weight
        if proposal.direction is TradeDirection.BUY
        else 0.0
    )
    resulting_symbol = market.current_symbol_weight + buy_weight
    resulting_total = market.current_total_exposure + buy_weight
    resulting_sector = market.sector_exposure + buy_weight
    sample_count = int(backtest.metrics.get("sample_count", 0))
    trade_count = int(backtest.metrics.get("trade_count", 0))
    drawdown = float(backtest.metrics.get("max_drawdown", 1))
    evidence_quality = min(
        market.evidence_quality,
        1.0
        if proposal.evidence_refs
        and all(
            ref.freshness.value == "fresh"
            and not ref.degraded
            and ref.error is None
            and ref.captured_at <= market.as_of
            and (
                ref.data_as_of is None
                or ref.data_as_of <= market.as_of
            )
            for ref in proposal.evidence_refs
        )
        else 0.0,
    )
    data_age = (market.as_of - market.data_as_of).total_seconds()
    expiry_ok = (
        proposal.expires_at is not None
        and proposal.expires_at >= market.as_of
    )
    price_deviation = (
        0.0
        if proposal.limit_price is None
        else abs(proposal.limit_price / market.current_price - 1)
    )
    t1_ok = (
        not limits.t_plus_one
        or proposal.direction is not TradeDirection.SELL
        or market.sellable_quantity >= market.requested_quantity
    )

    specifications = (
        (
            "single_position",
            resulting_symbol <= limits.max_single_position,
            resulting_symbol,
            limits.max_single_position,
            "单标的交易后仓位不得超过上限",
        ),
        (
            "total_exposure",
            resulting_total <= limits.max_total_exposure,
            resulting_total,
            limits.max_total_exposure,
            "交易后总仓位不得超过上限",
        ),
        (
            "sector_concentration",
            resulting_sector <= limits.max_sector_concentration,
            resulting_sector,
            limits.max_sector_concentration,
            f"{market.sector} 行业集中度不得超过上限",
        ),
        (
            "minimum_liquidity",
            market.average_turnover >= limits.min_average_turnover,
            market.average_turnover,
            limits.min_average_turnover,
            "平均成交额必须达到最低流动性要求",
        ),
        (
            "maximum_volatility",
            market.annualized_volatility <= limits.max_annualized_volatility,
            market.annualized_volatility,
            limits.max_annualized_volatility,
            "年化波动率不得超过上限",
        ),
        (
            "portfolio_max_drawdown",
            drawdown <= limits.max_portfolio_drawdown,
            drawdown,
            limits.max_portfolio_drawdown,
            "组合最大回撤不得超过上限",
        ),
        (
            "minimum_samples",
            sample_count >= limits.min_samples,
            sample_count,
            limits.min_samples,
            "回测样本数必须达到下限",
        ),
        (
            "minimum_trades",
            trade_count >= limits.min_trades,
            trade_count,
            limits.min_trades,
            "回测交易数必须达到下限",
        ),
        (
            "evidence_quality",
            evidence_quality >= limits.min_evidence_quality,
            evidence_quality,
            limits.min_evidence_quality,
            "证据质量必须达到下限",
        ),
        (
            "data_freshness",
            data_age <= limits.max_data_age_seconds,
            data_age,
            limits.max_data_age_seconds,
            "市场数据年龄不得超过上限秒数",
        ),
        (
            "proposal_expiry",
            expiry_ok,
            (
                None
                if proposal.expires_at is None
                else proposal.expires_at.isoformat()
            ),
            market.as_of.isoformat(),
            "提案必须设置且尚未超过有效期",
        ),
        (
            "price_deviation",
            price_deviation <= limits.max_price_deviation,
            price_deviation,
            limits.max_price_deviation,
            "限价与当前价格偏离不得超过上限",
        ),
        (
            "t_plus_one",
            t1_ok,
            {
                "sellable": market.sellable_quantity,
                "requested": market.requested_quantity,
            },
            "sellable >= requested",
            "卖出数量不得超过 T+1 可卖数量",
        ),
    )
    rules.extend(
        _rule(
            rule_id,
            passed=passed,
            observed=observed,
            limit=limit,
            message=message,
        )
        for rule_id, passed, observed, limit, message in specifications
    )
    if not backtest.passed:
        rules.append(
            _rule(
                "backtest_approved",
                passed=False,
                observed=False,
                limit=True,
                message="组合回测未通过，风险审核不得批准",
            )
        )
    failed = [item for item in rules if item.severity == "hard"]
    status = (
        VerdictStatus.NEEDS_REVISION
        if revision
        else VerdictStatus.REJECTED
        if failed
        else VerdictStatus.APPROVED
    )
    approved_weight = (
        proposal.target_weight if status is VerdictStatus.APPROVED else 0.0
    )
    return RiskVerdict(
        user_id=proposal.user_id,
        run_id=proposal.run_id,
        status=status,
        max_position=(
            limits.max_single_position
            if status is VerdictStatus.APPROVED
            else 0.0
        ),
        approved_weight=approved_weight,
        confidence=1,
        reasons=tuple(item.message for item in failed),
        rules=tuple(rules),
        proposal_hash=semantic_hash,
        created_at=market.as_of,
    )


def create_risk_provider(
    *,
    market_provider: Callable[[str, datetime], Awaitable[RiskInputs]],
    config: RiskLimits,
):
    """Build the graph-compatible async deterministic risk provider."""

    async def provider(
        proposal: TradeProposal,
        backtest: BacktestVerdict,
        context: Any,
    ) -> RiskVerdict:
        market = await market_provider(
            proposal.symbol,
            context.snapshot.as_of,
        )
        return evaluate_risk(proposal, backtest, market, config)

    return provider


def create_portfolio_risk_provider(
    *,
    market_provider: Callable[
        [str, datetime], Awaitable[RiskInputs]
    ],
    config: RiskLimits,
    portfolio_provider: Callable[[Any], Awaitable[Mapping[str, Any]]] | None = None,
):
    """Evaluate every proposal and aggregate with an all-or-nothing hard veto."""

    async def provider(
        proposals: TradeProposal | Sequence[TradeProposal],
        backtest: BacktestVerdict,
        context: Any,
    ) -> RiskVerdict:
        items = (
            (proposals,)
            if isinstance(proposals, TradeProposal)
            else tuple(proposals)
        )
        portfolio_hash = proposal_semantics_hash(items)
        if backtest.proposal_hash != portfolio_hash:
            raise ValueError("backtest portfolio semantics hash mismatch")
        markets = await asyncio.gather(
            *(
                market_provider(item.symbol, context.snapshot.as_of)
                for item in items
            )
        )
        portfolio = (
            await portfolio_provider(context)
            if portfolio_provider is not None
            else None
        )
        if portfolio is not None:
            base_total = float(portfolio["total_exposure"])
            base_weights = {
                str(key): float(value)
                for key, value in dict(
                    portfolio["symbol_weights"]
                ).items()
            }
            base_sector_weights = {
                str(key): float(value)
                for key, value in dict(
                    portfolio["sector_weights"]
                ).items()
            }
            sellable = dict(portfolio["sellable_quantity"])
            equity = float(portfolio["equity"])
            final_weights = dict(base_weights)
            final_sector_weights = dict(base_sector_weights)
            final_total = base_total
            for item, market in zip(items, markets):
                delta = (
                    item.target_weight
                    if item.direction is TradeDirection.BUY
                    else -item.target_weight
                    if item.direction is TradeDirection.SELL
                    else 0
                )
                final_weights[item.symbol] = (
                    final_weights.get(item.symbol, 0) + delta
                )
                final_sector_weights[market.sector] = (
                    final_sector_weights.get(market.sector, 0)
                    + delta
                )
                final_total += delta
            adjusted = []
            for item, market in zip(items, markets):
                own_buy = (
                    item.target_weight
                    if item.direction is TradeDirection.BUY
                    else 0
                )
                requested = max(
                    1.0,
                    item.target_weight
                    * equity
                    / market.current_price,
                )
                adjusted.append(
                    market.model_copy(
                        update={
                            "current_total_exposure": max(
                                0.0, final_total - own_buy
                            ),
                            "current_symbol_weight": max(
                                0.0,
                                final_weights.get(item.symbol, 0)
                                - own_buy,
                            ),
                            "sector_exposure": max(
                                0.0,
                                final_sector_weights.get(
                                    market.sector, 0
                                )
                                - own_buy,
                            ),
                            "sellable_quantity": float(
                                sellable.get(item.symbol, 0)
                            ),
                            "requested_quantity": requested,
                        }
                    )
                )
            markets = adjusted
        verdicts = [
            evaluate_risk(
                item,
                backtest.model_copy(
                    update={"proposal_hash": proposal_semantics_hash(item)}
                ),
                market,
                config,
            )
            for item, market in zip(items, markets)
        ]
        approved = all(
            item.status is VerdictStatus.APPROVED for item in verdicts
        )
        rules = tuple(
            rule.model_copy(
                update={"rule_id": f"{verdict_index}:{rule.rule_id}"}
            )
            for verdict_index, verdict in enumerate(verdicts)
            for rule in verdict.rules
        )
        return RiskVerdict(
            user_id=items[0].user_id,
            run_id=items[0].run_id,
            status=(
                VerdictStatus.APPROVED
                if approved
                else VerdictStatus.REJECTED
            ),
            max_position=(
                config.max_single_position if approved else 0
            ),
            approved_weight=(
                min(1.0, sum(item.target_weight for item in items))
                if approved
                else 0
            ),
            confidence=1,
            reasons=tuple(
                reason
                for verdict in verdicts
                for reason in verdict.reasons
            ),
            rules=rules,
            proposal_hash=portfolio_hash,
            created_at=context.snapshot.as_of,
        )

    return provider
