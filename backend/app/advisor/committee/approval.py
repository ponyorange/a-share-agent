"""Fail-closed validation and execution planning for committee approvals."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .models import (
    BacktestVerdict,
    FinalDecision,
    RiskVerdict,
    RunStatus,
    TradeDirection,
    VerdictStatus,
)
from .execution_costs import calculate_execution
from .risk import proposal_semantics_hash


class ApprovalRejected(ValueError):
    """The frozen decision is no longer safe to execute."""


class PlannedOrder(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    side: str
    qty: float
    price: float
    name: str | None = None
    asset_type: str | None = None
    quote_price: float | None = None
    executed_price: float | None = None
    gross_amount: float | None = None
    commission: float | None = None
    stamp_tax: float | None = None
    slippage: float | None = None
    total_fees: float | None = None
    net_cash: float | None = None
    market_status_hash: str | None = None
    market_status_expires_at: str | None = None


class ApprovalPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_hash: str
    decision_hash: str
    account_version: int
    orders: tuple[PlannedOrder, ...]


def approval_plan_hash(plan: ApprovalPlan) -> str:
    return hashlib.sha256(
        json.dumps(
            plan.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def plans_match(left: ApprovalPlan, right: ApprovalPlan) -> bool:
    def comparable(plan: ApprovalPlan) -> dict[str, Any]:
        payload = plan.model_dump(mode="json")
        for order in payload["orders"]:
            order["market_status_expires_at"] = None
        return payload

    return hmac.compare_digest(
        hashlib.sha256(
            json.dumps(
                comparable(left),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        hashlib.sha256(
            json.dumps(
                comparable(right),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    )


def decision_hash(decision: FinalDecision) -> str:
    payload = decision.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _position_map(account: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["symbol"]): dict(item)
        for item in account.get("positions") or ()
        if item.get("symbol")
    }


def validate_approval(
    *,
    run_status: RunStatus,
    decision: FinalDecision,
    backtest: BacktestVerdict,
    risk: RiskVerdict,
    current_quotes: dict[str, float],
    current_market_status: dict[str, dict[str, Any]] | None = None,
    frozen_account_version: int,
    current_account: dict[str, Any],
    now: datetime,
    max_price_deviation: float,
    frozen_prices: dict[str, float] | None = None,
    risk_limits: dict[str, Any] | None = None,
    execution_settings: dict[str, Any] | None = None,
) -> ApprovalPlan:
    """Revalidate the complete approval chain against current account state."""
    now = now.astimezone(timezone.utc)
    if run_status is not RunStatus.COMPLETED:
        raise ApprovalRejected("委员会运行尚未完成")
    if (
        decision.action is TradeDirection.HOLD
        or not decision.orders
        or decision.risk_status is not VerdictStatus.APPROVED
    ):
        raise ApprovalRejected("最终决定未批准交易")
    if not backtest.passed:
        raise ApprovalRejected("回测未批准")
    if (
        risk.status is not VerdictStatus.APPROVED
        or risk.approved_weight <= 0
    ):
        raise ApprovalRejected("风险审核未批准")

    proposals = tuple(decision.orders)
    digest = proposal_semantics_hash(proposals)
    if (
        decision.proposal_hash != digest
        or backtest.proposal_hash != digest
        or risk.proposal_hash != digest
    ):
        raise ApprovalRejected("决策哈希链不一致或已被篡改")
    if any(item.expires_at is None or item.expires_at < now for item in proposals):
        raise ApprovalRejected("决策已过期")

    account_version = int(current_account.get("account_version", -1))
    if account_version != int(frozen_account_version):
        raise ApprovalRejected("账户版本已变化，必须重新召开委员会")
    cash = float(current_account.get("cash") or 0)
    equity = float(current_account.get("equity") or 0)
    if equity <= 0:
        raise ApprovalRejected("账户权益无效")
    positions = _position_map(current_account)
    orders: list[PlannedOrder] = []
    required_cash = 0.0
    buy_weight = 0.0
    sell_weight = 0.0
    sector_deltas: dict[str, float] = {}

    for proposal in proposals:
        market_status_hash = None
        market_status_expires_at = None
        market = (
            dict(current_market_status.get(proposal.symbol) or {})
            if current_market_status is not None
            else None
        )
        if market is not None:
            required_market_fields = {
                "quote",
                "volume",
                "suspended",
                "limit_up",
                "limit_down",
                "locked",
                "source",
                "as_of",
            }
            if not required_market_fields.issubset(market):
                raise ApprovalRejected(
                    f"实时市场状态缺失: {proposal.symbol}"
                )
            market_as_of = market["as_of"]
            if isinstance(market_as_of, str):
                market_as_of = datetime.fromisoformat(
                    market_as_of.replace("Z", "+00:00")
                )
            if (
                not isinstance(market_as_of, datetime)
                or market_as_of.tzinfo is None
            ):
                raise ApprovalRejected(
                    f"实时市场状态时间无效: {proposal.symbol}"
                )
            max_market_age = float(
                (risk_limits or {}).get(
                    "max_market_status_age_seconds", 30
                )
            )
            if (now - market_as_of.astimezone(timezone.utc)).total_seconds() > (
                max_market_age
            ):
                raise ApprovalRejected(
                    f"实时市场状态已过期: {proposal.symbol}"
                )
            if bool(market["suspended"]) or float(market["volume"]) <= 0:
                raise ApprovalRejected(f"标的停牌或无成交: {proposal.symbol}")
            if (
                proposal.direction is TradeDirection.BUY
                and bool(market["limit_up"])
                and bool(market["locked"])
            ):
                raise ApprovalRejected(f"涨停锁死禁止买入: {proposal.symbol}")
            if (
                proposal.direction is TradeDirection.SELL
                and bool(market["limit_down"])
                and bool(market["locked"])
            ):
                raise ApprovalRejected(f"跌停锁死禁止卖出: {proposal.symbol}")
            canonical_market = {
                key: value
                for key, value in market.items()
                if key != "as_of"
            }
            market_status_hash = hashlib.sha256(
                json.dumps(
                    canonical_market,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            ).hexdigest()
            market_status_expires_at = datetime.fromtimestamp(
                market_as_of.timestamp() + max_market_age,
                tz=timezone.utc,
            ).isoformat()
        quote = float(
            (market or {}).get(
                "quote", current_quotes.get(proposal.symbol)
            )
            or 0
        )
        if quote <= 0:
            raise ApprovalRejected(f"当前报价不可用: {proposal.symbol}")
        frozen_price = (
            proposal.limit_price
            or proposal.stop_price
            or (frozen_prices or {}).get(proposal.symbol)
        )
        if frozen_price is None or frozen_price <= 0:
            raise ApprovalRejected(f"冻结计划价格缺失: {proposal.symbol}")
        deviation = abs(quote / float(frozen_price) - 1)
        if deviation > max_price_deviation:
            raise ApprovalRejected(f"价格偏离超过阈值: {proposal.symbol}")

        raw_qty = proposal.target_weight * equity / quote
        qty = (raw_qty // 100) * 100
        if proposal.direction is TradeDirection.BUY:
            if qty < 100:
                raise ApprovalRejected(f"计划数量不足一手: {proposal.symbol}")
            buy_weight += proposal.target_weight
            sector = (
                "ETF"
                if proposal.symbol.startswith(("5", "15", "16"))
                else "STOCK"
            )
            sector_deltas[sector] = (
                sector_deltas.get(sector, 0) + proposal.target_weight
            )
            side = "buy"
        elif proposal.direction is TradeDirection.SELL:
            position = positions.get(proposal.symbol)
            if position is None:
                raise ApprovalRejected(f"当前无持仓: {proposal.symbol}")
            available = float(
                position.get(
                    "available_quantity",
                    position.get("available_qty", position.get("qty", 0)),
                )
            )
            held = float(position.get("quantity", position.get("qty", 0)))
            qty = min(qty, held)
            qty = (qty // 100) * 100
            if qty < 100 or available + 1e-9 < qty:
                raise ApprovalRejected(f"T+1 可卖数量不足: {proposal.symbol}")
            side = "sell"
            sell_weight += proposal.target_weight
        else:
            raise ApprovalRejected("审批计划不得包含持有指令")
        costs = dict(execution_settings or {})
        execution = calculate_execution(
            symbol=proposal.symbol,
            side=side,
            qty=qty,
            quote_price=quote,
            commission_rate=float(costs.get("commission_rate", 0)),
            minimum_commission=float(costs.get("minimum_commission", 0)),
            stamp_tax_rate=float(costs.get("stamp_tax_rate", 0)),
            slippage_bps=float(costs.get("slippage_bps", 0)),
            market_status_hash=market_status_hash,
            market_status_expires_at=market_status_expires_at,
        )
        if side == "buy":
            required_cash += -execution.net_cash
        orders.append(
            PlannedOrder(
                **execution.model_dump(mode="python"),
            )
        )
    if required_cash > cash + 1e-6:
        raise ApprovalRejected("账户现金不足")
    limits = dict(risk_limits or {})
    max_single = float(
        limits.get("max_single_position", risk.max_position or 0)
    )
    if max_single <= 0 or any(
        item.target_weight > max_single + 1e-12 for item in proposals
    ):
        raise ApprovalRejected("当前组合单标的风控不通过")
    current_exposure = max(0.0, (equity - cash) / equity)
    max_total = float(limits.get("max_total_exposure", 1))
    if current_exposure + buy_weight - sell_weight > max_total + 1e-12:
        raise ApprovalRejected("当前组合总仓位风控不通过")
    max_sector = float(limits.get("max_sector_concentration", 1))
    current_sectors: dict[str, float] = {}
    for symbol, position in positions.items():
        market_value = float(
            position.get("market_value")
            or float(position.get("quantity", position.get("qty", 0)))
            * float(position.get("last_price", position.get("last", 0)))
        )
        sector = (
            "ETF" if symbol.startswith(("5", "15", "16")) else "STOCK"
        )
        current_sectors[sector] = (
            current_sectors.get(sector, 0) + market_value / equity
        )
    if any(
        current_sectors.get(sector, 0) + delta > max_sector + 1e-12
        for sector, delta in sector_deltas.items()
    ):
        raise ApprovalRejected("当前组合集中度风控不通过")
    return ApprovalPlan(
        proposal_hash=digest,
        decision_hash=decision_hash(decision),
        account_version=account_version,
        orders=tuple(orders),
    )


def execute_approval_once(
    database: Any,
    *,
    user_id: str,
    run_id: str,
    idempotency_key: str,
    plan: ApprovalPlan,
    executor: Any,
    lease_owner: str | None = None,
    lease_seconds: int = 60,
) -> dict[str, Any]:
    """Journal a portfolio execution and replay its result exactly once."""
    collection = database.committee_approvals
    identity = {
        "user_id": user_id,
        "idempotency_key": idempotency_key,
    }
    now = datetime.now(timezone.utc)
    owner = lease_owner or uuid4().hex
    plan_hash = approval_plan_hash(plan)
    try:
        collection.insert_one(
            {
                **identity,
                "run_id": run_id,
                "status": "pending",
                "decision_hash": plan.decision_hash,
                "proposal_hash": plan.proposal_hash,
                "account_version": plan.account_version,
                "plan_hash": plan_hash,
                "created_at": now,
                "updated_at": now,
            }
        )
    except DuplicateKeyError:
        pass
    journal = collection.find_one(identity)
    if journal is None:
        raise RuntimeError("approval journal was not durable")
    if (
        journal.get("run_id") != run_id
        or journal.get("decision_hash") != plan.decision_hash
        or journal.get("proposal_hash") != plan.proposal_hash
        or int(journal.get("account_version", -1)) != plan.account_version
        or not hmac.compare_digest(
            str(journal.get("plan_hash") or ""),
            plan_hash,
        )
    ):
        raise ApprovalRejected("审批幂等键已用于不同决策版本")
    if journal.get("status") == "completed":
        return dict(journal["result"])
    lease_expires = datetime.fromtimestamp(
        now.timestamp() + lease_seconds,
        tz=timezone.utc,
    )
    if journal.get("status") == "executing":
        current_expiry = journal.get("lease_expires_at")
        if isinstance(current_expiry, datetime) and current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        if current_expiry is None or current_expiry > now:
            raise ApprovalRejected("审批正在执行，请稍后以同一幂等键重试")
        claimed = collection.find_one_and_update(
            {
                **identity,
                "status": "executing",
                "lease_owner": journal.get("lease_owner"),
                "lease_expires_at": journal.get("lease_expires_at"),
            },
            {
                "$set": {
                    "lease_owner": owner,
                    "lease_expires_at": lease_expires,
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    else:
        claimed = collection.find_one_and_update(
            {**identity, "status": journal.get("status")},
            {
                "$set": {
                    "status": "executing",
                    "updated_at": now,
                    "error": None,
                    "lease_owner": owner,
                    "lease_expires_at": lease_expires,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    if claimed is None:
        latest = collection.find_one(identity)
        if latest and latest.get("status") == "completed":
            return dict(latest["result"])
        raise ApprovalRejected("审批状态发生并发变化")

    def renew_lease() -> None:
        renewed_at = datetime.now(timezone.utc)
        renewed_until = datetime.fromtimestamp(
            renewed_at.timestamp() + lease_seconds,
            tz=timezone.utc,
        )
        renewed = collection.find_one_and_update(
            {
                **identity,
                "status": "executing",
                "lease_owner": owner,
            },
            {
                "$set": {
                    "lease_expires_at": renewed_until,
                    "updated_at": renewed_at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if renewed is None:
            raise ApprovalRejected("审批执行租约已丢失")

    try:
        renew_lease()
        result = executor(
            user_id=user_id,
            orders=[item.model_dump(mode="python") for item in plan.orders],
            external_idempotency_key=idempotency_key,
            mutation_source=f"committee_approval:{run_id}",
            expected_account_version=plan.account_version,
            lease_owner=owner,
            lease_renew=renew_lease,
        )
        renew_lease()
    except BaseException as exc:
        collection.find_one_and_update(
            {
                **identity,
                "status": "executing",
                "lease_owner": owner,
            },
            {
                "$set": {
                    "status": "failed",
                    "updated_at": datetime.now(timezone.utc),
                    "error": type(exc).__name__,
                    "lease_owner": None,
                    "lease_expires_at": None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        raise
    completed = collection.find_one_and_update(
        {
            **identity,
            "status": "executing",
            "lease_owner": owner,
        },
        {
            "$set": {
                "status": "completed",
                "result": result,
                "updated_at": datetime.now(timezone.utc),
                "completed_at": datetime.now(timezone.utc),
                "error": None,
                "lease_owner": None,
                "lease_expires_at": None,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if completed is None:
        raise RuntimeError("approval completion was not durable")
    return dict(result)
