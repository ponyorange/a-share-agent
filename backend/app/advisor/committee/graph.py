"""Finite async LangGraph orchestration over JSON-safe checkpoint state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import inspect
import time
from typing import Any, Awaitable, Callable, NotRequired, Protocol

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .agents import (
    AnalystOutput,
    ChairOutput,
    DebateOutput,
    RoleAgentExecutor,
    RoleBudgetError,
    RoleExecutionError,
    RoleTimeoutError,
    TraderOutput,
)
from .chat_stream import CardRef, ChatMessagePayload, message_id_for
from .models import (
    AnalystReport,
    AnalystRole,
    BacktestVerdict,
    DebateTurn,
    EvidenceRef,
    FinalDecision,
    RiskVerdict,
    TradeDirection,
    TradeProposal,
    VerdictStatus,
)
from .prompts import build_role_prompt
from .risk import proposal_semantics_hash
from .snapshot import MarketSnapshot
from .state import (
    BudgetLimits,
    BudgetUsage,
    CommitteeError,
    CommitteeEvent,
    CommitteeState,
)
from .tools import SnapshotView, snapshot_view


ANALYST_ROLES = ("fundamental", "technical", "news", "quant")


class CommitteeGraphState(CommitteeState):
    attempt: NotRequired[int]


@dataclass(frozen=True, slots=True)
class CommitteeContext:
    user_id: str
    run_id: str
    snapshot: MarketSnapshot
    idempotency_key: str
    deadline_at: float

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - time.time())


class SnapshotLoader(Protocol):
    async def __call__(
        self,
        user_id: str,
        run_id: str,
        request: dict[str, Any],
    ) -> MarketSnapshot: ...


BacktestProvider = Callable[
    [TradeProposal, CommitteeContext],
    Awaitable[BacktestVerdict],
]
RiskProvider = Callable[
    [TradeProposal, BacktestVerdict, CommitteeContext],
    Awaitable[RiskVerdict],
]
PortfolioBacktestProvider = Callable[
    [tuple[TradeProposal, ...], CommitteeContext],
    Awaitable[BacktestVerdict],
]
PortfolioRiskProvider = Callable[
    [tuple[TradeProposal, ...], BacktestVerdict, CommitteeContext],
    Awaitable[RiskVerdict],
]


async def _default_snapshot_loader(
    user_id: str,
    run_id: str,
    request: dict[str, Any],
) -> MarketSnapshot:
    del user_id, run_id, request
    raise RuntimeError("snapshot loader is not configured")


async def _default_backtest(
    proposal: TradeProposal,
    context: CommitteeContext,
) -> BacktestVerdict:
    return BacktestVerdict(
        user_id=context.user_id,
        run_id=context.run_id,
        passed=False,
        score=0,
        metrics={},
        summary="未注入回测实现，按保守默认不通过",
        proposal_hash=proposal_semantics_hash(proposal),
        created_at=context.snapshot.created_at,
    )


async def _default_risk(
    proposal: TradeProposal,
    backtest: BacktestVerdict,
    context: CommitteeContext,
) -> RiskVerdict:
    return RiskVerdict(
        user_id=context.user_id,
        run_id=context.run_id,
        status=VerdictStatus.REJECTED,
        max_position=0,
        approved_weight=0,
        confidence=1,
        reasons=("未注入确定性风险实现，保守否决",),
        proposal_hash=proposal_semantics_hash(proposal),
        created_at=context.snapshot.created_at,
    )


@dataclass(frozen=True, slots=True)
class CommitteeDependencies:
    role_executor: RoleAgentExecutor
    snapshot_loader: SnapshotLoader = _default_snapshot_loader
    backtest: BacktestProvider = _default_backtest
    risk: RiskProvider = _default_risk
    portfolio_backtest: PortfolioBacktestProvider | None = None
    portfolio_risk: PortfolioRiskProvider | None = None
    expiry_calendar: Callable[[datetime, frozenset[date]], datetime] = (
        lambda value, sessions: next_trading_day(
            value, sessions=sessions
        )
    )


def next_trading_day(
    value: datetime,
    *,
    sessions: frozenset[date] | set[date] | None = None,
    holidays: set[date] | frozenset[date] = frozenset(),
) -> datetime:
    if sessions is not None:
        future = sorted(day for day in sessions if day > value.date())
        if not future:
            raise ValueError("authoritative trading calendar is unavailable")
        next_date = future[0]
        return value.replace(
            year=next_date.year,
            month=next_date.month,
            day=next_date.day,
        )
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate.date() in holidays:
        candidate += timedelta(days=1)
    return candidate


def _calendar_sessions(snapshot: MarketSnapshot) -> frozenset[date]:
    for item in snapshot.items:
        if item.name != "trading_calendar":
            continue
        raw = item.content.get("sessions") if item.content else None
        if not raw:
            break
        try:
            return frozenset(date.fromisoformat(str(value)) for value in raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("trading calendar evidence is invalid") from exc
    raise ValueError("trading calendar evidence is missing")


def _json(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _snapshot(state: CommitteeState) -> MarketSnapshot:
    return MarketSnapshot.model_validate(state["snapshot"])


def _limits(state: CommitteeState) -> BudgetLimits:
    return BudgetLimits.model_validate(state.get("limits") or {})


def _usage(state: CommitteeState) -> BudgetUsage:
    return BudgetUsage.model_validate(state.get("budget") or {})


def _deadline(state: CommitteeState) -> float:
    return float(state.get("deadline_at_epoch") or 0)


def _remaining(state: CommitteeState) -> float:
    return max(0.0, _deadline(state) - time.time())


def _event(
    state: CommitteeState,
    node: str,
    event_type: str,
    **payload: Any,
) -> dict[str, Any]:
    return CommitteeEvent(
        event_id=f"{state['run_id']}:{node}:{event_type}",
        node=node,
        event_type=event_type,
        payload=payload,
    ).model_dump(mode="json")


def _message_completed(
    state: CommitteeState,
    *,
    node: str,
    role: str,
    content: str,
    round_index: int | None = None,
    card_kind: str | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    attempt = int(state.get("attempt", 1))
    payload = ChatMessagePayload(
        message_id=message_id_for(
            state["run_id"],
            attempt,
            node,
            round_index,
        ),
        role=role,
        node=node,
        round=round_index,
        content=content,
        status=status,
        # Graph parallel branches cannot allocate a stable global order.
        # Mongo CommitteeEventRecord.sequence / SSE event id is authoritative;
        # Task 5 will replace this transport placeholder for presentation.
        sequence=0,
        generation=1,
        card_kind=card_kind,
        card_ref=(
            CardRef(attempt=attempt, node=node, kind=card_kind)
            if card_kind
            else None
        ),
    )
    return {
        "event_id": payload.message_id,
        "node": node,
        "event_type": "message_completed",
        "payload": payload.model_dump(mode="json"),
    }


def _error(
    node: str,
    exc: Exception,
    *,
    critical: bool,
    code: str | None = None,
) -> dict[str, Any]:
    return CommitteeError(
        node=node,
        code=code or type(exc).__name__,
        message=(str(exc) or type(exc).__name__)[:500],
        critical=critical,
    ).model_dump(mode="json")


def _context(state: CommitteeState, node: str) -> CommitteeContext:
    return CommitteeContext(
        user_id=state["user_id"],
        run_id=state["run_id"],
        snapshot=_snapshot(state),
        idempotency_key=f"{state['run_id']}:{node}",
        deadline_at=_deadline(state),
    )


async def _call_provider(
    function: Callable[..., Any],
    state: CommitteeState,
    *args: Any,
) -> Any:
    is_async = inspect.iscoroutinefunction(function) or inspect.iscoroutinefunction(
        getattr(function, "__call__", None)
    )
    if not is_async:
        raise TypeError("committee providers must be async coroutine functions")
    remaining = min(_limits(state).node_timeout_seconds, _remaining(state))
    if remaining <= 0:
        raise TimeoutError("committee deadline exhausted")
    started = time.time()
    async with asyncio.timeout(remaining):
        result = await function(*args)
    if time.time() - started > remaining or _remaining(state) <= 0:
        raise TimeoutError("provider exceeded committee deadline")
    return result


def _evidence_refs(
    snapshot: MarketSnapshot,
    view: SnapshotView,
    evidence_ids: tuple[str, ...],
    state: CommitteeState,
) -> tuple[EvidenceRef, ...]:
    items = {f"{snapshot.snapshot_id}:{item.name}": item for item in snapshot.items}
    return tuple(
        EvidenceRef(
            user_id=state["user_id"],
            run_id=state["run_id"],
            evidence_id=evidence_id,
            source=items[evidence_id].source,
            captured_at=items[evidence_id].captured_at,
            data_as_of=items[evidence_id].data_as_of,
            freshness=items[evidence_id].freshness,
            degraded=items[evidence_id].degraded,
            error=items[evidence_id].error,
        )
        for evidence_id in sorted(set(evidence_ids))
        if evidence_id in view.evidence_ids
    )


def _conservative_decision(
    state: CommitteeState,
    rationale: str,
    *,
    proposal: TradeProposal | None = None,
    risk_status: VerdictStatus = VerdictStatus.REJECTED,
) -> dict[str, Any]:
    snapshot = None
    if state.get("snapshot"):
        try:
            snapshot = MarketSnapshot.model_validate(state["snapshot"])
        except ValidationError:
            snapshot = None
    symbol = (
        proposal.symbol
        if proposal is not None
        else snapshot.universe[0]
        if snapshot and snapshot.universe
        else "UNKNOWN"
    )
    values: dict[str, Any] = {
        "user_id": state["user_id"],
        "run_id": state["run_id"],
        "action": TradeDirection.HOLD,
        "symbol": symbol,
        "target_weight": 0,
        "confidence": 1,
        "rationale": rationale,
        "risk_status": risk_status,
        "evidence_refs": proposal.evidence_refs if proposal else (),
    }
    if snapshot is not None:
        values["created_at"] = snapshot.created_at
    if proposal is not None:
        values["proposals"] = (proposal,)
        values["orders"] = ()
        values["proposal_hash"] = proposal_semantics_hash(proposal)
    return FinalDecision(**values).model_dump(mode="json")


def _abort_update(
    state: CommitteeState,
    reason: str,
    node: str,
    *,
    proposal: TradeProposal | None = None,
) -> dict[str, Any]:
    exc = RuntimeError(reason)
    return {
        "status": "aborted",
        "errors": [_error(node, exc, critical=True)],
        "events": [_event(state, node, "aborted", reason=reason)],
        "final_decision": _conservative_decision(
            state,
            reason,
            proposal=proposal,
        ),
    }


def _parallel_abort_update(
    state: CommitteeState,
    reason: str,
    node: str,
) -> dict[str, Any]:
    update = _abort_update(state, reason, node)
    update.pop("final_decision", None)
    return update


def _execution_updates(execution: Any) -> dict[str, Any]:
    return {
        "model_calls": [_json(record) for record in execution.records],
        "budget": _json(execution.usage),
    }


def _failure_updates(exc: RoleExecutionError) -> dict[str, Any]:
    return {
        "model_calls": [_json(record) for record in exc.records],
        "budget": _json(exc.usage),
    }


def build_committee_graph(
    dependencies: CommitteeDependencies,
    *,
    checkpointer: Any | None = None,
):
    """Compile an async graph. Use package invoke wrappers for sync callers."""

    def ensure_agent_run(state: CommitteeState) -> None:
        dependencies.role_executor.ensure_run(
            state["user_id"],
            state["run_id"],
            _limits(state),
            deadline_at=_deadline(state),
            initial_usage=_usage(state),
        )

    async def prepare(state: CommitteeGraphState) -> dict[str, Any]:
        try:
            limits = _limits(state)
        except ValidationError as exc:
            return _abort_update(
                state,
                "委员会预算配置无效",
                "limits",
            ) | {"errors": [_error("limits", exc, critical=True)]}
        started = float(state.get("started_at_epoch") or time.time())
        deadline = float(
            state.get("deadline_at_epoch")
            or started + limits.total_timeout_seconds
        )
        base_state = dict(state)
        base_state.update(
            {
                "started_at_epoch": started,
                "deadline_at_epoch": deadline,
                "limits": limits.model_dump(mode="json"),
            }
        )
        snapshot_value = state.get("snapshot")
        if snapshot_value is None:
            try:
                snapshot_value = await _call_provider(
                    dependencies.snapshot_loader,
                    base_state,
                    state["user_id"],
                    state["run_id"],
                    state.get("snapshot_request", {}),
                )
            except Exception as exc:
                update = _abort_update(
                    base_state,
                    "冻结快照加载失败",
                    "snapshot",
                )
                update["errors"] = [_error("snapshot", exc, critical=True)]
                return update
        try:
            snapshot = MarketSnapshot.model_validate(snapshot_value)
        except ValidationError as exc:
            return _abort_update(
                base_state,
                "冻结快照无效",
                "snapshot",
            ) | {"errors": [_error("snapshot", exc, critical=True)]}
        dependencies.role_executor.ensure_run(
            state["user_id"],
            state["run_id"],
            limits,
            deadline_at=deadline,
            initial_usage=_usage(state),
        )
        try:
            rounds = max(
                1,
                min(int(state.get("max_debate_rounds", 2)), 2),
            )
        except (TypeError, ValueError) as exc:
            return _abort_update(
                base_state | {"snapshot": snapshot.model_dump(mode="json")},
                "辩论轮次配置无效",
                "max_debate_rounds",
            ) | {
                "errors": [
                    _error("max_debate_rounds", exc, critical=True)
                ]
            }
        minimum_calls = len(ANALYST_ROLES) + rounds * 2 + 2
        prepared = {
            "snapshot": snapshot.model_dump(mode="json"),
            "analyst_reports": [],
            "debate_turns": [],
            "debate_round": int(state.get("debate_round", 0)),
            "max_debate_rounds": rounds,
            "limits": limits.model_dump(mode="json"),
            "budget": BudgetUsage().model_dump(mode="json"),
            "model_calls": [],
            "errors": [],
            "events": [
                _event(
                    base_state,
                    "prepare",
                    "ready",
                    snapshot_id=snapshot.snapshot_id,
                ),
                _message_completed(
                    base_state,
                    node="prepare",
                    role="data",
                    content=f"已冻结 {len(snapshot.universe)} 个标的的市场快照。",
                    card_kind="snapshot",
                ),
            ],
            "degraded": False,
            "status": "running",
            "started_at_epoch": started,
            "deadline_at_epoch": deadline,
        }
        if limits.max_calls < minimum_calls:
            prepared.update(
                _abort_update(
                    base_state | {"snapshot": snapshot.model_dump(mode="json")},
                    "模型调用预算不足",
                    "budget",
                )
            )
        return prepared

    def analyst_node(role: str):
        async def run(state: CommitteeGraphState) -> dict[str, Any]:
            if state.get("status") == "aborted":
                return {}
            reports = [
                AnalystReport.model_validate(item)
                for item in state.get("analyst_reports", [])
            ]
            if any(report.role.value == role for report in reports):
                return {}
            if _remaining(state) <= 0:
                return _parallel_abort_update(
                    state,
                    "委员会总时限已耗尽",
                    role,
                )
            snapshot = _snapshot(state)
            view = snapshot_view(snapshot, role)
            try:
                ensure_agent_run(state)
                execution = await dependencies.role_executor.aexecute(
                    user_id=state["user_id"],
                    run_id=state["run_id"],
                    role=role,
                    prompt=build_role_prompt(
                        role,
                        {"snapshot": view.prompt_payload()},
                    ),
                    model_tier="quick",
                    timeout_seconds=_limits(state).node_timeout_seconds,
                    deadline_at=_deadline(state),
                    idempotency_key=f"{state['run_id']}:{role}",
                    allowed_evidence_ids=view.evidence_ids,
                    attempt=int(state.get("attempt", 1)),
                )
                output = AnalystOutput.model_validate(execution.output)
                report = AnalystReport(
                    user_id=state["user_id"],
                    run_id=state["run_id"],
                    role=AnalystRole(role),
                    thesis=output.thesis,
                    confidence=output.confidence,
                    evidence=_evidence_refs(
                        snapshot,
                        view,
                        output.evidence_ids,
                        state,
                    ),
                    symbols=output.symbols,
                    created_at=snapshot.created_at,
                )
                return _execution_updates(execution) | {
                    "analyst_reports": [report.model_dump(mode="json")],
                    "events": [
                        _event(state, role, "completed"),
                        _message_completed(
                            state,
                            node=role,
                            role=role,
                            content=output.chat_message,
                            card_kind="analyst_reports",
                        ),
                    ],
                }
            except (RoleBudgetError, RoleTimeoutError) as exc:
                update = _parallel_abort_update(
                    state,
                    str(exc),
                    role,
                ) | _failure_updates(exc)
                update["events"] = [
                    *update.get("events", []),
                    _message_completed(
                        state,
                        node=role,
                        role=role,
                        content=f"{role} 节点执行失败，未生成角色结论。",
                        status="failed",
                    ),
                ]
                return update
            except RoleExecutionError as exc:
                return _failure_updates(exc) | {
                    "degraded": True,
                    "errors": [_error(role, exc, critical=False)],
                    "events": [
                        _event(state, role, "degraded"),
                        _message_completed(
                            state,
                            node=role,
                            role=role,
                            content=f"{role} 节点执行失败，已降级处理。",
                            status="degraded",
                        ),
                    ],
                }
            except ValidationError as exc:
                updates = (
                    _execution_updates(execution)
                    if "execution" in locals()
                    else {}
                )
                return updates | {
                    "degraded": True,
                    "errors": [_error(role, exc, critical=False)],
                    "events": [
                        _event(state, role, "degraded"),
                        _message_completed(
                            state,
                            node=role,
                            role=role,
                            content=f"{role} 节点输出无效，已降级处理。",
                            status="degraded",
                        ),
                    ],
                }

        return run

    async def analyst_fan_in(state: CommitteeGraphState) -> dict[str, Any]:
        if state.get("status") == "aborted":
            return {
                "final_decision": _conservative_decision(
                    state,
                    "分析节点超时或预算不足，保守中止",
                ),
                "events": [_event(state, "fan_in", "aborted")],
            }
        return {
            "events": [
                _event(
                    state,
                    "fan_in",
                    "completed",
                    report_count=len(state.get("analyst_reports", [])),
                )
            ]
        }

    def debate_node(role: str):
        async def run(state: CommitteeGraphState) -> dict[str, Any]:
            if _remaining(state) <= 0:
                return _abort_update(state, "委员会总时限已耗尽", role)
            round_index = int(state.get("debate_round", 0)) + 1
            sequence = (round_index - 1) * 2 + (1 if role == "bull" else 2)
            turns = [
                DebateTurn.model_validate(item)
                for item in state.get("debate_turns", [])
            ]
            if any(turn.sequence == sequence and turn.speaker.value == role for turn in turns):
                return {}
            snapshot = _snapshot(state)
            view = snapshot_view(snapshot, role)
            payload = {
                "reports": state.get("analyst_reports", []),
                "prior_turns": state.get("debate_turns", []),
                "evidence_catalog": [
                    {
                        "evidence_id": item.evidence_id,
                        "name": item.name,
                        "freshness": item.freshness,
                    }
                    for item in view.evidence
                ],
                "round": round_index,
            }
            try:
                ensure_agent_run(state)
                execution = await dependencies.role_executor.aexecute(
                    user_id=state["user_id"],
                    run_id=state["run_id"],
                    role=role,
                    prompt=build_role_prompt(role, payload),
                    model_tier="deep",
                    timeout_seconds=_limits(state).node_timeout_seconds,
                    deadline_at=_deadline(state),
                    idempotency_key=f"{state['run_id']}:{role}:{round_index}",
                    allowed_evidence_ids=view.evidence_ids,
                    attempt=int(state.get("attempt", 1)),
                    round_index=round_index,
                )
                output = DebateOutput.model_validate(execution.output)
                turn = DebateTurn(
                    user_id=state["user_id"],
                    run_id=state["run_id"],
                    sequence=sequence,
                    speaker=AnalystRole(role),
                    argument=output.argument,
                    confidence=output.confidence,
                    evidence_ids=output.evidence_ids,
                    created_at=snapshot.created_at,
                )
                update = _execution_updates(execution) | {
                    "debate_turns": [turn.model_dump(mode="json")],
                    "events": [
                        _event(state, f"{role}:{round_index}", "completed"),
                        _message_completed(
                            state,
                            node=role,
                            role=role,
                            content=output.chat_message,
                            round_index=round_index,
                            card_kind="debate_turns",
                        ),
                    ],
                }
                if role == "bear":
                    update["debate_round"] = round_index
                return update
            except RoleExecutionError as exc:
                update = _abort_update(state, f"{role} 辩论失败", role) | _failure_updates(exc) | {
                    "errors": [_error(role, exc, critical=True)]
                }
                update["events"] = [
                    *update.get("events", []),
                    _message_completed(
                        state,
                        node=role,
                        role=role,
                        content=f"{role} 辩论节点执行失败，未生成角色结论。",
                        round_index=round_index,
                        status="failed",
                    ),
                ]
                return update
            except ValidationError as exc:
                update = _abort_update(state, f"{role} 辩论输出无效", role) | (
                    _execution_updates(execution)
                    if "execution" in locals()
                    else {}
                ) | {"errors": [_error(role, exc, critical=True)]}
                update["events"] = [
                    *update.get("events", []),
                    _message_completed(
                        state,
                        node=role,
                        role=role,
                        content=f"{role} 辩论节点输出无效，未生成角色结论。",
                        round_index=round_index,
                        status="failed",
                    ),
                ]
                return update

        return run

    async def trader(state: CommitteeGraphState) -> dict[str, Any]:
        if state.get("trade_proposal") is not None:
            return {}
        if _remaining(state) <= 0:
            return _abort_update(state, "委员会总时限已耗尽", "trader")
        snapshot = _snapshot(state)
        view = snapshot_view(snapshot, "trader")
        try:
            ensure_agent_run(state)
            execution = await dependencies.role_executor.aexecute(
                user_id=state["user_id"],
                run_id=state["run_id"],
                role="trader",
                prompt=build_role_prompt(
                    "trader",
                    {
                        "reports": state.get("analyst_reports", []),
                        "debate": state.get("debate_turns", []),
                        "universe": snapshot.universe,
                    },
                ),
                model_tier="deep",
                timeout_seconds=_limits(state).node_timeout_seconds,
                deadline_at=_deadline(state),
                idempotency_key=f"{state['run_id']}:trader",
                allowed_evidence_ids=view.evidence_ids,
                attempt=int(state.get("attempt", 1)),
            )
            output = TraderOutput.model_validate(execution.output)
            drafts = (
                output.trade_proposals
                if output.trade_proposals
                else (output,)
            )
            proposals = tuple(
                TradeProposal(
                    user_id=state["user_id"],
                    run_id=state["run_id"],
                    strategy_id=snapshot.strategy_id,
                    strategy_version=snapshot.strategy_version,
                    symbol=str(draft.symbol),
                    direction=TradeDirection(str(draft.direction)),
                    target_weight=float(draft.target_weight),
                    confidence=float(draft.confidence),
                    rationale=str(draft.rationale),
                    evidence_refs=_evidence_refs(
                        snapshot,
                        view,
                        draft.evidence_ids,
                        state,
                    ),
                    order_type=draft.order_type,
                    time_in_force=draft.time_in_force,
                    limit_price=draft.limit_price,
                    stop_price=draft.stop_price,
                    expires_at=dependencies.expiry_calendar(
                        snapshot.as_of,
                        _calendar_sessions(snapshot),
                    ),
                    created_at=snapshot.created_at,
                )
                for draft in drafts
            )
            proposal = proposals[0]
            return _execution_updates(execution) | {
                "trade_proposal": proposal.model_dump(mode="json"),
                "trade_proposals": [
                    item.model_dump(mode="json") for item in proposals
                ],
                "events": [
                    _event(state, "trader", "completed"),
                    _message_completed(
                        state,
                        node="trader",
                        role="trader",
                        content=output.chat_message,
                        card_kind="trade_proposal",
                    ),
                ],
            }
        except RoleExecutionError as exc:
            update = _abort_update(state, "交易员节点失败", "trader") | _failure_updates(exc) | {
                "errors": [_error("trader", exc, critical=True)]
            }
            update["events"] = [
                *update.get("events", []),
                _message_completed(
                    state,
                    node="trader",
                    role="trader",
                    content="交易员节点执行失败，未生成交易结论。",
                    status="failed",
                ),
            ]
            return update
        except ValidationError as exc:
            update = _abort_update(state, "交易员输出无效", "trader") | (
                _execution_updates(execution)
                if "execution" in locals()
                else {}
            ) | {"errors": [_error("trader", exc, critical=True)]}
            update["events"] = [
                *update.get("events", []),
                _message_completed(
                    state,
                    node="trader",
                    role="trader",
                    content="交易员节点输出无效，未生成交易结论。",
                    status="failed",
                ),
            ]
            return update

    async def backtest(state: CommitteeGraphState) -> dict[str, Any]:
        if state.get("status") == "aborted" or state.get("backtest_verdict") is not None:
            return {}
        proposal = None
        try:
            proposals = tuple(
                TradeProposal.model_validate(item)
                for item in (
                    state.get("trade_proposals")
                    or [state["trade_proposal"]]
                )
            )
            proposal = proposals[0]
            provider = dependencies.portfolio_backtest
            raw = await _call_provider(
                provider or dependencies.backtest,
                state,
                proposals if provider is not None else proposal,
                _context(state, "backtest"),
            )
            verdict = BacktestVerdict.model_validate(raw)
            if verdict.user_id != state["user_id"] or verdict.run_id != state["run_id"]:
                raise ValueError("backtest verdict scope mismatch")
            return {
                "backtest_verdict": verdict.model_dump(mode="json"),
                "events": [
                    _event(state, "backtest", "completed", passed=verdict.passed),
                    _message_completed(
                        state,
                        node="backtest",
                        role="backtest",
                        content=(
                            f"回测{'通过' if verdict.passed else '未通过'}，"
                            f"得分 {verdict.score:.2f}。{verdict.summary}"
                        ),
                        card_kind="backtest_verdict",
                    ),
                ],
            }
        except Exception as exc:
            update = _abort_update(
                state,
                "回测节点失败",
                "backtest",
                proposal=proposal,
            ) | {"errors": [_error("backtest", exc, critical=True)]}
            update["events"] = [
                *update.get("events", []),
                _message_completed(
                    state,
                    node="backtest",
                    role="backtest",
                    content="回测节点执行失败，未生成回测结论。",
                    status="failed",
                ),
            ]
            return update

    async def risk(state: CommitteeGraphState) -> dict[str, Any]:
        if state.get("status") == "aborted" or state.get("risk_verdict") is not None:
            return {}
        proposal = None
        try:
            proposals = tuple(
                TradeProposal.model_validate(item)
                for item in (
                    state.get("trade_proposals")
                    or [state["trade_proposal"]]
                )
            )
            proposal = proposals[0]
            backtest_verdict = BacktestVerdict.model_validate(
                state["backtest_verdict"]
            )
            portfolio_hash = proposal_semantics_hash(proposals)
            if backtest_verdict.proposal_hash != portfolio_hash:
                raise ValueError("backtest proposal semantics hash mismatch")
            provider = dependencies.portfolio_risk
            raw = await _call_provider(
                provider or dependencies.risk,
                state,
                proposals if provider is not None else proposal,
                backtest_verdict,
                _context(state, "risk"),
            )
            verdict = RiskVerdict.model_validate(raw)
            if verdict.user_id != state["user_id"] or verdict.run_id != state["run_id"]:
                raise ValueError("risk verdict scope mismatch")
            if verdict.proposal_hash != portfolio_hash:
                raise ValueError(
                    "risk verdict proposal semantics hash mismatch"
                )
            return {
                "risk_verdict": verdict.model_dump(mode="json"),
                "events": [
                    _event(
                        state,
                        "risk",
                        "completed",
                        status=verdict.status.value,
                    ),
                    _message_completed(
                        state,
                        node="risk",
                        role="risk",
                        content=(
                            f"风控结论：{verdict.status.value}，"
                            f"批准仓位 {verdict.approved_weight:.0%}。"
                        ),
                        card_kind="risk_verdict",
                    ),
                ],
            }
        except Exception as exc:
            update = _abort_update(
                state,
                "风险节点失败",
                "risk",
                proposal=proposal,
            ) | {"errors": [_error("risk", exc, critical=True)]}
            update["events"] = [
                *update.get("events", []),
                _message_completed(
                    state,
                    node="risk",
                    role="risk",
                    content="风险节点执行失败，未生成风控结论。",
                    status="failed",
                ),
            ]
            return update

    async def chair(state: CommitteeGraphState) -> dict[str, Any]:
        if state.get("status") == "aborted":
            return {}
        if state.get("final_decision") is not None:
            return {}
        proposal = None
        try:
            proposal = TradeProposal.model_validate(state["trade_proposal"])
            proposals = tuple(
                TradeProposal.model_validate(item)
                for item in (
                    state.get("trade_proposals")
                    or [state["trade_proposal"]]
                )
            )
            backtest_verdict = BacktestVerdict.model_validate(
                state["backtest_verdict"]
            )
            risk_verdict = RiskVerdict.model_validate(state["risk_verdict"])
            portfolio_hash = proposal_semantics_hash(proposals)
            if (
                backtest_verdict.proposal_hash != portfolio_hash
                or risk_verdict.proposal_hash != portfolio_hash
            ):
                return _abort_update(
                    state,
                    "风险审核后交易语义发生变化，必须重新风控",
                    "chair",
                    proposal=proposal,
                ) | {
                    "errors": [
                        _error(
                            "chair",
                            ValueError("proposal semantics changed after risk"),
                            critical=True,
                            code="proposal_semantics_changed_after_risk",
                        )
                    ]
                }
            snapshot = _snapshot(state)
            view = snapshot_view(snapshot, "chair")
            ensure_agent_run(state)
            execution = await dependencies.role_executor.aexecute(
                user_id=state["user_id"],
                run_id=state["run_id"],
                role="chair",
                prompt=build_role_prompt(
                    "chair",
                    {
                        "proposal": proposal.model_dump(mode="json"),
                        "trade_proposals": [
                            item.model_dump(mode="json")
                            for item in proposals
                        ],
                        "backtest": backtest_verdict.model_dump(mode="json"),
                        "risk": risk_verdict.model_dump(mode="json"),
                    },
                ),
                model_tier="deep",
                timeout_seconds=_limits(state).node_timeout_seconds,
                deadline_at=_deadline(state),
                idempotency_key=f"{state['run_id']}:chair",
                allowed_evidence_ids=view.evidence_ids,
                attempt=int(state.get("attempt", 1)),
            )
            output = ChairOutput.model_validate(execution.output)
            vetoed = (
                risk_verdict.status is not VerdictStatus.APPROVED
                or risk_verdict.approved_weight <= 0
            )
            locked_action = TradeDirection.HOLD if vetoed else proposal.direction
            locked_weight = (
                0
                if vetoed or locked_action is TradeDirection.HOLD
                else min(proposal.target_weight, risk_verdict.approved_weight)
            )
            if not vetoed and (
                output.symbol != proposal.symbol
                or TradeDirection(output.action) is not locked_action
                or output.target_weight != locked_weight
            ):
                reason = "主席修改了已经回测和风险复核的交易语义，要求重审"
                decision = FinalDecision(
                    user_id=state["user_id"],
                    run_id=state["run_id"],
                    action=TradeDirection.HOLD,
                    symbol=proposal.symbol,
                    target_weight=0,
                    confidence=1,
                    rationale=reason,
                    risk_status=VerdictStatus.NEEDS_REVISION,
                    evidence_refs=proposal.evidence_refs,
                    proposals=proposals,
                    orders=(),
                    proposal_hash=portfolio_hash,
                    created_at=snapshot.created_at,
                )
                return _execution_updates(execution) | {
                    "status": "aborted",
                    "final_decision": decision.model_dump(mode="json"),
                    "errors": [
                        _error(
                            "chair",
                            ValueError(reason),
                            critical=True,
                            code="chair_trade_semantics_changed",
                        )
                    ],
                    "events": [
                        _event(state, "chair", "needs_revision"),
                        _message_completed(
                            state,
                            node="chair",
                            role="chair",
                            content="主席输出改变已审核交易语义，未形成有效裁决。",
                            card_kind="final_decision",
                            status="failed",
                        ),
                    ],
                }
            chair_refs = _evidence_refs(
                snapshot,
                view,
                output.evidence_ids,
                state,
            )
            refs = {
                ref.evidence_id: ref
                for ref in (*proposal.evidence_refs, *chair_refs)
            }
            decision = FinalDecision(
                user_id=state["user_id"],
                run_id=state["run_id"],
                action=locked_action,
                symbol=proposal.symbol,
                target_weight=locked_weight,
                confidence=output.confidence,
                rationale=(
                    f"风险否决不可推翻；{output.rationale}"
                    if vetoed
                    else output.rationale
                ),
                risk_status=risk_verdict.status,
                evidence_refs=tuple(refs[key] for key in sorted(refs)),
                proposals=proposals,
                orders=() if vetoed else proposals,
                proposal_hash=portfolio_hash,
                created_at=snapshot.created_at,
            )
            return _execution_updates(execution) | {
                "final_decision": decision.model_dump(mode="json"),
                "status": "completed",
                "events": [
                    _event(state, "chair", "completed", vetoed=vetoed),
                    _message_completed(
                        state,
                        node="chair",
                        role="chair",
                        content=output.chat_message,
                        card_kind="final_decision",
                    ),
                ],
            }
        except RoleExecutionError as exc:
            update = _abort_update(
                state,
                "主席节点失败",
                "chair",
                proposal=proposal,
            ) | _failure_updates(exc) | {
                "errors": [_error("chair", exc, critical=True)]
            }
            update["events"] = [
                *update.get("events", []),
                _message_completed(
                    state,
                    node="chair",
                    role="chair",
                    content="主席节点执行失败，未生成最终裁决。",
                    card_kind="final_decision",
                    status="failed",
                ),
            ]
            return update
        except ValidationError as exc:
            update = _abort_update(
                state,
                "主席输入或输出无效",
                "chair",
                proposal=proposal,
            ) | (
                _execution_updates(execution)
                if "execution" in locals()
                else {}
            ) | {"errors": [_error("chair", exc, critical=True)]}
            update["events"] = [
                *update.get("events", []),
                _message_completed(
                    state,
                    node="chair",
                    role="chair",
                    content="主席节点输入或输出无效，未生成最终裁决。",
                    card_kind="final_decision",
                    status="failed",
                ),
            ]
            return update

    builder = StateGraph(CommitteeGraphState)
    builder.add_node("prepare", prepare)
    builder.add_node("fan_out", lambda _state: {})
    for role in ANALYST_ROLES:
        builder.add_node(role, analyst_node(role))
    builder.add_node("analyst_fan_in", analyst_fan_in)
    builder.add_node("bull", debate_node("bull"))
    builder.add_node("bear", debate_node("bear"))
    builder.add_node("trader", trader)
    builder.add_node("backtest", backtest)
    builder.add_node("risk", risk)
    builder.add_node("chair", chair)

    builder.add_edge(START, "prepare")
    builder.add_conditional_edges(
        "prepare",
        lambda state: END if state.get("status") == "aborted" else "fan_out",
        {"fan_out": "fan_out", END: END},
    )
    for role in ANALYST_ROLES:
        builder.add_edge("fan_out", role)
    builder.add_edge(list(ANALYST_ROLES), "analyst_fan_in")
    builder.add_conditional_edges(
        "analyst_fan_in",
        lambda state: END if state.get("status") == "aborted" else "bull",
        {"bull": "bull", END: END},
    )
    builder.add_edge("bull", "bear")
    builder.add_conditional_edges(
        "bear",
        lambda state: (
            END
            if state.get("status") == "aborted"
            else "bull"
            if state.get("debate_round", 0) < state.get("max_debate_rounds", 2)
            else "trader"
        ),
        {"bull": "bull", "trader": "trader", END: END},
    )
    builder.add_edge("trader", "backtest")
    builder.add_edge("backtest", "risk")
    builder.add_edge("risk", "chair")
    builder.add_edge("chair", END)
    return builder.compile(checkpointer=checkpointer)
