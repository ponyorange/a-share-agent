"""Authenticated HTTP API for durable committee runs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo.errors import DuplicateKeyError
from rq.job import Job
from rq.command import send_stop_job_command

from ...auth import get_current_user
from ...db import get_db
from ..config_loader import load_config
from ..paper import (
    get_account_snapshot_atomic,
    place_orders_atomic,
)
from .approval import (
    ApprovalPlan,
    ApprovalRejected,
    approval_plan_hash,
    execute_approval_once,
    plans_match,
    validate_approval,
)
from .jobs import create_queue, enqueue_committee_run
from .models import (
    BacktestVerdict,
    CommitteeRun,
    FinalDecision,
    RiskVerdict,
    RunStatus,
)
from .redis_client import (
    CommitteeConfigurationError,
    CommitteeDisabledError,
    CommitteeRedisSettings,
    health_check,
)
from .checkpoint import initialize_checkpoint_saver
from .reconcile import reconcile_stale_runs
from .repository import (
    CommitteeRepository,
    IllegalStatusTransition,
    RunNotFound,
    VersionConflict,
    encode_api,
)
from .runtime import CommitteeRuntime
from .service import deterministic_job_id


Board = Literal["etf", "hs", "star"]
_EVENT_ID_RE = re.compile(
    r"^(?P<sequence>0|[1-9][0-9]*)"
    r"(?:-0|-live-[1-9][0-9]*|-terminal)?$"
)


def parse_last_event_id(value: str | None) -> int:
    if value is None or value == "":
        return 0
    match = _EVENT_ID_RE.fullmatch(value)
    if match is None:
        raise HTTPException(status_code=400, detail="Last-Event-ID 非法")
    return int(match.group("sequence"))


class CommitteeRunCreateBody(BaseModel):
    model_config = {"extra": "forbid"}

    symbols: tuple[
        Annotated[str, Field(pattern=r"^\d{6}$")], ...
    ] = Field(default=(), max_length=100)
    boards: tuple[Board, ...] = Field(default=(), max_length=3)
    horizon: Literal["next_day"] = "next_day"
    strategy_version: str = Field(min_length=1, max_length=128)

    @field_validator("symbols", "boards")
    @classmethod
    def unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value

    @model_validator(mode="after")
    def require_scope(self) -> CommitteeRunCreateBody:
        if not self.symbols and not self.boards:
            raise ValueError("symbols or boards is required")
        return self


router = APIRouter(prefix="/committee", tags=["committee"])


class OrderPreviewBindBody(BaseModel):
    model_config = {"extra": "forbid"}
    decision_hash: str = Field(min_length=64, max_length=64)
    account_version: int = Field(ge=0)


class ApproveBody(BaseModel):
    model_config = {"extra": "forbid"}
    preview_id: str = Field(min_length=1, max_length=256)
    decision_hash: str = Field(min_length=64, max_length=64)
    proposal_hash: str = Field(min_length=64, max_length=64)
    account_version: int = Field(ge=0)
    confirm: Literal[True]


def _uid(user: dict[str, Any]) -> str:
    return str(user["id"])


def _plain_repository() -> CommitteeRepository:
    return CommitteeRepository.from_default_database()


def _repository() -> CommitteeRepository:
    repository = _plain_repository()
    try:
        settings = CommitteeRedisSettings.from_env()
        if settings.enabled:
            queue = create_queue(settings)
            reconcile_stale_runs(
                repository=repository,
                connection=queue.connection,
            )
    except Exception:
        pass
    return repository


def _infra() -> tuple[CommitteeRedisSettings, CommitteeRuntime, Any]:
    try:
        settings = CommitteeRedisSettings.from_env()
        runtime = CommitteeRuntime(settings)
        return settings, runtime, create_queue(settings)
    except (CommitteeConfigurationError, CommitteeDisabledError) as exc:
        raise HTTPException(
            status_code=503,
            detail="委员会后台任务未启用或配置无效",
        ) from exc


@router.get("/health")
def committee_health(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    del user
    status = health_check()
    checkpoint = initialize_checkpoint_saver()
    result = {
        "redis": status,
        "checkpoint": {
            "enabled": checkpoint.enabled,
            "ok": checkpoint.ok,
            "status": checkpoint.status,
            "error": checkpoint.error,
        },
    }
    if not status.get("ok") or not checkpoint.ok:
        raise HTTPException(status_code=503, detail=result)
    return result


def _run_json(run: CommitteeRun) -> dict[str, Any]:
    return encode_api(run)


def _request_hash(
    body: CommitteeRunCreateBody,
    parent_run_id: str | None = None,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "request": body.model_dump(mode="json"),
                "parent_run_id": parent_run_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _universe(body: CommitteeRunCreateBody) -> tuple[str, ...]:
    return tuple(
        [*body.symbols, *(f"board:{board}" for board in body.boards)]
    )


def _publish(
    repository: CommitteeRepository,
    runtime: CommitteeRuntime,
    user_id: str,
    run_id: str,
    event_type: str,
    payload: Any,
    event_key: str | None = None,
) -> None:
    run = repository.get_run(user_id, run_id)
    durable = repository.append_outbox_event(
        user_id,
        run_id,
        attempt=run.attempt,
        node="api",
        event_type=event_type,
        event_key=event_key or f"api:{event_type}:{uuid4().hex}",
        payload=payload,
    )
    try:
        runtime.append_event(
            user_id,
            run_id,
            event_type,
            payload,
            event_id=f"{durable['sequence']}-0",
        )
        repository.mark_event_published(
            user_id,
            run_id,
            str(durable["event_key"]),
        )
    except Exception:
        pass


def _create_and_enqueue(
    *,
    repository: CommitteeRepository,
    settings: CommitteeRedisSettings,
    runtime: CommitteeRuntime,
    queue: Any,
    user_id: str,
    body: CommitteeRunCreateBody,
    idempotency_key: str,
    parent_run_id: str | None = None,
    attempt: int = 1,
) -> CommitteeRun:
    request_hash = _request_hash(body, parent_run_id)
    existing = repository.find_idempotent_run(user_id, idempotency_key)
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key 已用于不同请求",
            )
        return existing
    now = datetime.now(timezone.utc)
    run_id = uuid4().hex
    run = CommitteeRun(
        user_id=user_id,
        run_id=run_id,
        status=RunStatus.QUEUED,
        strategy_version=body.strategy_version,
        horizon=body.horizon,
        universe=_universe(body),
        as_of=now,
        created_at=now,
        updated_at=now,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        queue_job_id=deterministic_job_id(user_id, idempotency_key),
        parent_run_id=parent_run_id,
        attempt=attempt,
        initial_input={
            "snapshot_request": {
                "universe": list(_universe(body)),
                "strategy_version": body.strategy_version,
                "as_of": now.isoformat(),
            },
            "attempt": attempt,
        },
        job_deadline_at=now + timedelta(seconds=settings.job_timeout),
    )
    try:
        repository.create_run(run)
    except DuplicateKeyError:
        raced = repository.find_idempotent_run(user_id, idempotency_key)
        if raced is None or raced.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="幂等请求冲突")
        return raced
    try:
        _publish(
            repository,
            runtime,
            user_id,
            run_id,
            "queued",
            {},
            event_key=f"attempt:{attempt}:queued",
        )
        enqueue_committee_run(
            user_id,
            run_id,
            idempotency_key,
            settings=settings,
            queue=queue,
        )
    except Exception as exc:
        run = repository.transition_status(
            user_id,
            run_id,
            expected_version=run.version,
            new_status=RunStatus.FAILED,
            error_code="queue_submission_failed",
            error_message=type(exc).__name__,
        )
        raise HTTPException(status_code=503, detail="委员会任务入队失败") from exc
    return run


@router.post("/runs", status_code=202)
def create_run(
    body: CommitteeRunCreateBody,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=256
    ),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    repository = _repository()
    settings, runtime, queue = _infra()
    run = _create_and_enqueue(
        repository=repository,
        settings=settings,
        runtime=runtime,
        queue=queue,
        user_id=_uid(user),
        body=body,
        idempotency_key=idempotency_key,
    )
    return {"run_id": run.run_id, "status": run.status.value}


@router.get("/runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    runs = _repository().list_runs(_uid(user), limit=limit)
    return {"runs": [_run_json(run) for run in runs]}


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        detail = _repository().get_detail(_uid(user), run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    return {
        "run": _run_json(detail.run),
        "artifacts": list(detail.artifacts),
        "events": list(detail.events),
    }


@router.delete("/runs/{run_id}")
def delete_run(
    run_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    uid = _uid(user)
    try:
        _plain_repository().soft_delete_run(
            uid,
            run_id,
            deleted_at=datetime.now(timezone.utc),
            deleted_by=uid,
        )
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    except (IllegalStatusTransition, VersionConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id, "deleted": True}


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    uid = _uid(user)
    repository = _repository()
    settings, runtime, queue = _infra()
    try:
        run = repository.get_run(uid, run_id)
        if run.status is RunStatus.CANCELLED:
            return {"run_id": run_id, "status": "cancelled"}
        runtime.request_cancel(uid, run_id)
        try:
            run = repository.request_cancel(
                uid,
                run_id,
                expected_version=run.version,
            )
        except VersionConflict:
            current = repository.get_run(uid, run_id)
            return {
                "run_id": run_id,
                "status": current.status.value,
            }
        try:
            job = Job.fetch(run.queue_job_id, connection=queue.connection)
            if job.get_status(refresh=True) in {"queued", "deferred", "scheduled"}:
                job.cancel()
            elif job.get_status(refresh=False) in {"started", "busy"}:
                send_stop_job_command(queue.connection, job.id)
        except Exception:
            pass
        _publish(repository, runtime, uid, run_id, "cancelled", {})
        return {"run_id": run_id, "status": run.status.value}
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    except IllegalStatusTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/retry", status_code=202)
def retry_run(
    run_id: str,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=256
    ),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    uid = _uid(user)
    repository = _repository()
    try:
        parent = repository.get_run(uid, run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    if parent.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
        raise HTTPException(status_code=409, detail="仅失败或取消的会议可重试")
    symbols = tuple(item for item in parent.universe if not item.startswith("board:"))
    boards = tuple(item.removeprefix("board:") for item in parent.universe if item.startswith("board:"))
    body = CommitteeRunCreateBody(
        symbols=symbols,
        boards=boards,
        horizon="next_day",
        strategy_version=parent.strategy_version,
    )
    settings, runtime, queue = _infra()
    existing = repository.find_idempotent_run(uid, idempotency_key)
    if existing is not None:
        if existing.parent_run_id != parent.run_id:
            raise HTTPException(status_code=409, detail="重试幂等键冲突")
        return {"run_id": existing.run_id, "status": existing.status.value}
    attempt = repository.allocate_retry_attempt(uid, parent.run_id)
    run = _create_and_enqueue(
        repository=repository,
        settings=settings,
        runtime=runtime,
        queue=queue,
        user_id=uid,
        body=body,
        idempotency_key=idempotency_key,
        parent_run_id=parent.run_id,
        attempt=attempt,
    )
    return {"run_id": run.run_id, "status": run.status.value}


async def _event_stream(
    request: Request,
    repository: CommitteeRepository,
    runtime: CommitteeRuntime,
    user_id: str,
    run_id: str,
    last_event_id: int,
):
    durable_cursor = int(last_event_id)
    ephemeral_cursor = "$"
    live_counter = 0
    while True:
        if await request.is_disconnected():
            return
        rows = repository.list_events_after(
            user_id,
            run_id,
            after_sequence=durable_cursor,
            limit=100,
        )
        for event in rows:
            durable_cursor = int(event["sequence"])
            payload = json.dumps(
                encode_api(event.get("payload") or {}),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield (
                f"id: {event['event_id']}\n"
                f"event: {event.get('event_type', 'message')}\n"
                f"data: {payload}\n\n"
            )
        if rows:
            continue
        run = repository.get_run(user_id, run_id)
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            # Emit a terminal SSE frame so clients stop reconnecting even when
            # Mongo has no matching event_type (e.g. early ValidationError).
            payload = json.dumps(
                encode_api(
                    {
                        "status": run.status.value,
                        "error_code": getattr(run, "error_code", None),
                        "error_message": getattr(run, "error_message", None),
                    }
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield (
                f"id: {durable_cursor}-terminal\n"
                f"event: {run.status.value}\n"
                f"data: {payload}\n\n"
            )
            return
        try:
            live = await asyncio.to_thread(
                runtime.read_ephemeral_events_after,
                user_id,
                run_id,
                last_event_id=ephemeral_cursor,
                count=20,
                block_ms=1000,
            )
        except Exception:
            live = []
        for event in live:
            ephemeral_cursor = event.event_id
            if event.event_type not in {
                "message_started",
                "message_delta",
            }:
                continue
            live_counter += 1
            payload = json.dumps(
                encode_api(event.payload or {}),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield (
                f"id: {durable_cursor}-live-{live_counter}\n"
                f"event: {event.event_type}\n"
                f"data: {payload}\n\n"
            )
        if not live:
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user: dict[str, Any] = Depends(get_current_user),
):
    uid = _uid(user)
    repository = _repository()
    try:
        repository.get_run(uid, run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    settings = CommitteeRedisSettings.from_env()
    runtime = CommitteeRuntime(settings)
    parsed_last_event_id = parse_last_event_id(last_event_id)
    return StreamingResponse(
        _event_stream(
            request,
            repository,
            runtime,
            uid,
            run_id,
            parsed_last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _artifacts(detail: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for artifact in detail.artifacts:
        values[str(artifact["kind"])] = artifact["payload"]
    return values


def _frozen_account_version(snapshot: dict[str, Any]) -> int:
    for item in snapshot.get("items") or ():
        if item.get("name") in {"account", "portfolio_account"}:
            content = item.get("content") or {}
            value = content.get("account_version", content.get("version"))
            try:
                return int(value)
            except (TypeError, ValueError):
                break
    raise ApprovalRejected("冻结账户版本缺失")


def _frozen_prices(snapshot: dict[str, Any]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for item in snapshot.get("items") or ():
        if item.get("name") != "kline":
            continue
        for symbol, value in dict(item.get("content") or {}).items():
            bars = list((value or {}).get("bars") or ())
            if bars and float(bars[-1].get("close") or 0) > 0:
                prices[str(symbol)] = float(bars[-1]["close"])
    return prices


def _current_market_status(symbol: str) -> dict[str, Any]:
    import akshare as ak

    asset_type = (
        "etf" if symbol.startswith(("5", "15", "16")) else "stock"
    )
    frame = (
        ak.fund_etf_spot_em()
        if asset_type == "etf"
        else ak.stock_zh_a_spot_em()
    )
    columns = {
        "code": ("代码", "基金代码", "证券代码"),
        "name": ("名称", "基金简称", "证券简称"),
        "quote": ("最新价",),
        "volume": ("成交量",),
        "high": ("最高", "最高价"),
        "low": ("最低", "最低价"),
        "previous_close": ("昨收", "昨收价"),
    }
    resolved = {
        key: next((name for name in names if name in frame), None)
        for key, names in columns.items()
    }
    if any(value is None for value in resolved.values()):
        raise ApprovalRejected("实时市场状态字段缺失")
    matched = frame.loc[
        frame[resolved["code"]].astype(str).str.zfill(6) == symbol
    ]
    if matched.empty:
        raise ApprovalRejected(f"实时市场状态缺失: {symbol}")
    row = matched.iloc[0]
    quote = float(row[resolved["quote"]])
    volume = float(row[resolved["volume"]])
    high = float(row[resolved["high"]])
    low = float(row[resolved["low"]])
    previous_close = float(row[resolved["previous_close"]])
    if (
        not all(
            math.isfinite(value)
            for value in (quote, volume, high, low, previous_close)
        )
        or min(quote, high, low, previous_close) <= 0
        or volume < 0
    ):
        raise ApprovalRejected(f"实时市场状态无效: {symbol}")
    name = str(row[resolved["name"]])
    if name.upper().startswith(("ST", "*ST", "SST")):
        limit_pct = 0.05
    elif asset_type == "etf" or symbol.startswith(
        ("000", "001", "002", "003", "600", "601", "603", "605")
    ):
        limit_pct = 0.1
    elif symbol.startswith(("300", "301", "688", "689")):
        limit_pct = 0.2
    elif symbol.startswith(("4", "8")):
        limit_pct = 0.3
    else:
        raise ApprovalRejected(f"实时涨跌停规则未知: {symbol}")
    up_price = previous_close * (1 + limit_pct)
    down_price = previous_close * (1 - limit_pct)
    limit_up = quote >= up_price - 0.001
    limit_down = quote <= down_price + 0.001
    locked = abs(high - low) <= 1e-9 and (
        limit_up or limit_down
    )
    return {
        "quote": quote,
        "volume": volume,
        "suspended": volume <= 0,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "locked": locked,
        "source": (
            "AKShare.fund_etf_spot_em"
            if asset_type == "etf"
            else "AKShare.stock_zh_a_spot_em"
        ),
        "as_of": datetime.now(timezone.utc),
    }


def _build_preview(user_id: str, run_id: str) -> tuple[Any, Any]:
    repository = _repository()
    detail = repository.get_detail(user_id, run_id)
    artifacts = _artifacts(detail)
    decision = FinalDecision.model_validate(artifacts.get("final_decision"))
    backtest = BacktestVerdict.model_validate(artifacts.get("backtest_verdict"))
    risk = RiskVerdict.model_validate(artifacts.get("risk_verdict"))
    snapshot = dict(artifacts.get("snapshot") or {})
    market_status = {
        item.symbol: _current_market_status(item.symbol)
        for item in decision.orders
    }
    quotes = {
        symbol: float(status["quote"])
        for symbol, status in market_status.items()
    }
    account = get_account_snapshot_atomic(
        user_id,
        as_of=datetime.now(timezone.utc),
    )
    committee_config = dict(load_config().get("committee") or {})
    risk_limits = dict(committee_config.get("risk_limits") or {})
    execution_settings = dict(committee_config.get("backtest") or {})
    max_deviation = float(risk_limits.get("max_price_deviation", 0.03))
    plan = validate_approval(
        run_status=detail.run.status,
        decision=decision,
        backtest=backtest,
        risk=risk,
        current_quotes=quotes,
        current_market_status=market_status,
        frozen_account_version=_frozen_account_version(snapshot),
        current_account=account,
        now=datetime.now(timezone.utc),
        max_price_deviation=max_deviation,
        frozen_prices=_frozen_prices(snapshot),
        risk_limits=risk_limits,
        execution_settings=execution_settings,
    )
    return detail, plan


def _account_semantics(account: dict[str, Any]) -> dict[str, Any]:
    positions = []
    for raw in account.get("positions") or ():
        item = dict(raw)
        positions.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "quantity": float(item.get("quantity", item.get("qty", 0))),
                "available_quantity": float(
                    item.get(
                        "available_quantity",
                        item.get("available_qty", item.get("qty", 0)),
                    )
                ),
                "cost": float(item.get("cost") or 0),
                "last_price": float(
                    item.get("last_price", item.get("last", 0))
                ),
            }
        )
    return {
        "cash": float(account.get("cash") or 0),
        "equity": float(account.get("equity") or 0),
        "positions": sorted(positions, key=lambda item: item["symbol"]),
    }


def _build_recovery_preview(
    database: Any,
    *,
    user_id: str,
    run_id: str,
    idempotency_key: str,
    detail: Any,
    preview_plan: ApprovalPlan,
    approval_journal: dict[str, Any],
) -> ApprovalPlan:
    mutation = database.paper_mutations.find_one(
        {
            "user_id": user_id,
            "external_idempotency_key": idempotency_key,
            "status": "recovered",
        }
    )
    if mutation is None:
        raise ApprovalRejected("无可恢复的模拟盘变更")
    mutation_id = str(mutation["mutation_id"])
    if mutation.get("type") != f"committee_approval:{run_id}":
        raise ApprovalRejected("恢复 lineage 与委员会运行不匹配")
    if not hmac.compare_digest(
        str(approval_journal.get("plan_hash") or ""),
        approval_plan_hash(preview_plan),
    ):
        raise ApprovalRejected("恢复审批计划哈希不匹配")
    pre_snapshot = mutation.get("pre_snapshot")
    if not isinstance(pre_snapshot, dict):
        raise ApprovalRejected("恢复前账户快照缺失")
    actual_pre_hash = hashlib.sha256(
        json.dumps(
            pre_snapshot,
            default=str,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(
        str(mutation.get("pre_snapshot_hash") or ""),
        actual_pre_hash,
    ):
        raise ApprovalRejected("恢复前账户快照哈希不匹配")
    recovery = database.paper_mutations.find_one(
        {
            "user_id": user_id,
            "mutation_id": f"recovery:{mutation_id}",
            "status": "completed",
            "recovered_mutation_id": mutation_id,
        }
    )
    if recovery is None:
        raise ApprovalRejected("恢复 lineage 不完整")
    current_account = get_account_snapshot_atomic(
        user_id,
        as_of=datetime.now(timezone.utc),
    )
    if current_account.get("latest_mutation_id") != f"recovery:{mutation_id}":
        raise ApprovalRejected("当前账户不再位于恢复 lineage")
    if _account_semantics(current_account) != _account_semantics(pre_snapshot):
        raise ApprovalRejected("恢复后账户实际状态与原预览不一致")

    artifacts = _artifacts(detail)
    decision = FinalDecision.model_validate(artifacts.get("final_decision"))
    backtest = BacktestVerdict.model_validate(
        artifacts.get("backtest_verdict")
    )
    risk = RiskVerdict.model_validate(artifacts.get("risk_verdict"))
    snapshot = dict(artifacts.get("snapshot") or {})
    market_status = {
        item.symbol: _current_market_status(item.symbol)
        for item in decision.orders
    }
    quotes = {
        symbol: float(status["quote"])
        for symbol, status in market_status.items()
    }
    committee_config = dict(load_config().get("committee") or {})
    risk_limits = dict(committee_config.get("risk_limits") or {})
    execution_settings = dict(committee_config.get("backtest") or {})
    validation_account = {
        **current_account,
        "account_version": preview_plan.account_version,
    }
    current_plan = validate_approval(
        run_status=detail.run.status,
        decision=decision,
        backtest=backtest,
        risk=risk,
        current_quotes=quotes,
        current_market_status=market_status,
        frozen_account_version=preview_plan.account_version,
        current_account=validation_account,
        now=datetime.now(timezone.utc),
        max_price_deviation=float(
            risk_limits.get("max_price_deviation", 0.03)
        ),
        frozen_prices=_frozen_prices(snapshot),
        risk_limits=risk_limits,
        execution_settings=execution_settings,
    )
    if not plans_match(current_plan, preview_plan):
        raise ApprovalRejected("恢复安全重验与原确定性订单不一致")
    return preview_plan


@router.get("/runs/{run_id}/order-preview")
def get_order_preview(
    run_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        _detail, plan = _build_preview(_uid(user), run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    except (ApprovalRejected, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"preview": plan.model_dump(mode="json")}


@router.post("/runs/{run_id}/order-preview")
def bind_order_preview(
    run_id: str,
    body: OrderPreviewBindBody,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    uid = _uid(user)
    try:
        _detail, plan = _build_preview(uid, run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    except (ApprovalRejected, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if (
        body.decision_hash != plan.decision_hash
        or body.account_version != plan.account_version
    ):
        raise HTTPException(status_code=409, detail="预览绑定版本已变化")
    preview_id = uuid4().hex
    _repository().append_artifact(
        uid,
        run_id,
        kind="order_preview",
        artifact_id=preview_id,
        payload=plan,
    )
    return {
        "preview_id": preview_id,
        "preview": plan.model_dump(mode="json"),
    }


@router.post("/runs/{run_id}/approve")
def approve_run(
    run_id: str,
    body: ApproveBody,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=256
    ),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    uid = _uid(user)
    database = get_db()
    existing = database.committee_approvals.find_one(
        {"user_id": uid, "idempotency_key": idempotency_key}
    )
    if existing and existing.get("status") == "completed":
        if (
            existing.get("run_id") != run_id
            or not hmac.compare_digest(
                str(existing.get("decision_hash") or ""),
                body.decision_hash,
            )
            or not hmac.compare_digest(
                str(existing.get("proposal_hash") or ""),
                body.proposal_hash,
            )
            or int(existing.get("account_version", -1))
            != body.account_version
        ):
            raise HTTPException(status_code=409, detail="审批幂等键冲突")
        return {"approval": encode_api(existing["result"]), "replayed": True}
    try:
        detail = _repository().get_detail(uid, run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail="会议不存在") from exc
    preview = next(
        (
            item
            for item in detail.artifacts
            if item.get("kind") == "order_preview"
            and item.get("artifact_id") == body.preview_id
        ),
        None,
    )
    if preview is None:
        raise HTTPException(status_code=409, detail="审批预览不存在")
    plan = ApprovalPlan.model_validate(preview["payload"])
    if (
        not hmac.compare_digest(body.decision_hash, plan.decision_hash)
        or not hmac.compare_digest(body.proposal_hash, plan.proposal_hash)
        or body.account_version != plan.account_version
    ):
        raise HTTPException(status_code=409, detail="审批版本与预览不一致")
    recovered_mutation = database.paper_mutations.find_one(
        {
            "user_id": uid,
            "external_idempotency_key": idempotency_key,
            "status": "recovered",
        }
    )
    if existing is not None and recovered_mutation is not None:
        try:
            recovery_plan = _build_recovery_preview(
                database,
                user_id=uid,
                run_id=run_id,
                idempotency_key=idempotency_key,
                detail=detail,
                preview_plan=plan,
                approval_journal=existing,
            )
            result = execute_approval_once(
                database,
                user_id=uid,
                run_id=run_id,
                idempotency_key=idempotency_key,
                plan=recovery_plan,
                executor=place_orders_atomic,
            )
        except (ApprovalRejected, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"approval": encode_api(result), "replayed": True}
    completed_mutation = database.paper_mutations.find_one(
        {
            "user_id": uid,
            "external_idempotency_key": idempotency_key,
            "status": "completed",
        }
    )
    if existing is not None and completed_mutation is not None:
        if not hmac.compare_digest(
            str(existing.get("plan_hash") or ""),
            approval_plan_hash(plan),
        ):
            raise HTTPException(status_code=409, detail="审批恢复版本冲突")
        result = place_orders_atomic(
            user_id=uid,
            orders=[
                item.model_dump(mode="python") for item in plan.orders
            ],
            external_idempotency_key=idempotency_key,
            mutation_source=f"committee_approval:{run_id}",
            expected_account_version=plan.account_version,
            lease_owner="approval-recovery",
            lease_renew=lambda: None,
        )
        database.committee_approvals.update_one(
            {
                "user_id": uid,
                "idempotency_key": idempotency_key,
                "status": {"$ne": "completed"},
            },
            {
                "$set": {
                    "status": "completed",
                    "result": result,
                    "completed_at": datetime.now(timezone.utc),
                    "lease_owner": None,
                    "lease_expires_at": None,
                }
            },
        )
        return {"approval": encode_api(result), "replayed": True}
    try:
        _detail, current_plan = _build_preview(uid, run_id)
    except (ApprovalRejected, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    journal_matches = (
        existing is None
        or hmac.compare_digest(
            str(existing.get("plan_hash") or ""),
            approval_plan_hash(current_plan),
        )
    )
    if not plans_match(current_plan, plan) or not journal_matches:
        if existing is not None:
            database.committee_approvals.update_one(
                {
                    "user_id": uid,
                    "idempotency_key": idempotency_key,
                    "status": existing.get("status"),
                },
                {
                    "$set": {
                        "status": "invalidated",
                        "invalidated_at": datetime.now(timezone.utc),
                        "error": "approval plan changed; new confirmation required",
                    }
                },
            )
        raise HTTPException(
            status_code=409,
            detail="审批状态已变化，请重新预览并确认新版本",
        )
    try:
        result = execute_approval_once(
            database,
            user_id=uid,
            run_id=run_id,
            idempotency_key=idempotency_key,
            plan=plan,
            executor=place_orders_atomic,
        )
    except ApprovalRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approval": encode_api(result), "replayed": existing is not None}
