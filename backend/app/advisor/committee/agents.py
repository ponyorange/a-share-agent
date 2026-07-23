"""Async, budgeted and replay-safe committee role execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import hashlib
import inspect
import json
import math
import threading
import time
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, HumanMessage
from pydantic import BaseModel, Field, ValidationError, model_validator

from ..agent.llm import build_chat_model
from .chat_stream import (
    ChatStreamEvent,
    IncrementalChatMessageParser,
    message_id_for,
)
from .models import NonEmptyStr
from .state import BudgetLimits, BudgetUsage, ModelCallRecord


class AnalystOutput(BaseModel):
    model_config = {"extra": "forbid"}
    chat_message: str = Field(min_length=1, max_length=12000)
    thesis: str = Field(min_length=1, max_length=12000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = ()
    symbols: tuple[NonEmptyStr, ...] = ()


class DebateOutput(BaseModel):
    model_config = {"extra": "forbid"}
    chat_message: str = Field(min_length=1, max_length=12000)
    argument: str = Field(min_length=1, max_length=12000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = ()


class TradeDraftOutput(BaseModel):
    model_config = {"extra": "forbid"}
    symbol: str = Field(min_length=1, max_length=256)
    direction: Literal["buy", "sell", "hold"]
    target_weight: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=12000)
    evidence_ids: tuple[str, ...] = ()
    order_type: Literal["market", "limit", "stop_limit"] = "market"
    time_in_force: Literal["day", "gtc"] = "day"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)


class TraderOutput(BaseModel):
    model_config = {"extra": "forbid"}
    chat_message: str = Field(min_length=1, max_length=12000)
    symbol: str | None = Field(default=None, min_length=1, max_length=256)
    direction: Literal["buy", "sell", "hold"] | None = None
    target_weight: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    rationale: str | None = Field(default=None, min_length=1, max_length=12000)
    evidence_ids: tuple[str, ...] = ()
    order_type: Literal["market", "limit", "stop_limit"] = "market"
    time_in_force: Literal["day", "gtc"] = "day"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    trade_proposals: tuple[TradeDraftOutput, ...] = ()

    @model_validator(mode="after")
    def exactly_one_shape(self) -> TraderOutput:
        legacy = (
            self.symbol,
            self.direction,
            self.target_weight,
            self.confidence,
            self.rationale,
        )
        if self.trade_proposals and any(value is not None for value in legacy):
            raise ValueError("use portfolio or legacy trader shape, not both")
        if not self.trade_proposals and any(value is None for value in legacy):
            raise ValueError("legacy trader shape is incomplete")
        return self


class ChairOutput(BaseModel):
    model_config = {"extra": "forbid"}
    chat_message: str = Field(min_length=1, max_length=12000)
    action: Literal["buy", "sell", "hold"]
    symbol: str = Field(min_length=1, max_length=256)
    target_weight: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=12000)
    evidence_ids: tuple[str, ...] = ()


ROLE_SCHEMAS: dict[str, type[BaseModel]] = {
    "fundamental": AnalystOutput,
    "technical": AnalystOutput,
    "news": AnalystOutput,
    "quant": AnalystOutput,
    "bull": DebateOutput,
    "bear": DebateOutput,
    "trader": TraderOutput,
    "chair": ChairOutput,
}


@dataclass(frozen=True, slots=True)
class RoleRequest:
    user_id: str
    run_id: str
    role: str
    prompt: str
    output_schema: type[BaseModel]
    model_tier: Literal["quick", "deep"]
    idempotency_key: str
    timeout_seconds: float
    deadline_at: float
    max_output_tokens: int | None = None
    message_id: str = ""
    generation: int = 1
    round_index: int | None = None
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: Mapping[str, Any] | BaseModel | str
    model_name: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    token_usage_known: bool | None = None
    tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        known = self.token_usage_known
        if known is None:
            known = self.input_tokens is not None and self.output_tokens is not None
        object.__setattr__(self, "token_usage_known", bool(known))


class RoleRunner(Protocol):
    async def __call__(self, request: RoleRequest) -> ModelResponse: ...


RoleStreamSink = Callable[[ChatStreamEvent], Awaitable[None]]


def _is_async_callable(value: Any) -> bool:
    return inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(
        getattr(value, "__call__", None)
    )


def _supports_native_streaming(model: Any) -> bool:
    if not isinstance(model, BaseChatModel):
        return False
    return bool(model._should_stream(async_api=True, stream=True))


def estimate_input_tokens(prompt: str) -> int:
    """Conservative estimate: ceil(UTF-8 bytes / 3) plus 16 framing tokens."""
    return max(1, math.ceil(len(prompt.encode("utf-8")) / 3) + 16)


@dataclass(slots=True)
class _RunBudget:
    limits: BudgetLimits
    deadline_at: float
    started_at: float
    calls: int = 0
    tokens: int = 0
    unknown_token_calls: int = 0
    elapsed_seconds: float = 0
    token_budget_closed: bool = False
    token_budget_violated: bool = False
    outstanding: dict[str, tuple[int, int]] = field(default_factory=dict)
    revision: int = 0


@dataclass(frozen=True, slots=True)
class AttemptReservation:
    reservation_id: str
    usage: BudgetUsage
    input_tokens: int
    max_output_tokens: int


class BudgetLedger:
    """Atomic in-process reservations; durable totals are mirrored into graph state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, _RunBudget] = {}

    @staticmethod
    def key(user_id: str, run_id: str) -> str:
        return f"{user_id}:{run_id}"

    def begin(
        self,
        user_id: str,
        run_id: str,
        limits: BudgetLimits,
        deadline_at: float,
        initial_usage: BudgetUsage | None = None,
    ) -> None:
        key = self.key(user_id, run_id)
        restored = initial_usage or BudgetUsage()
        restored_outstanding = dict(restored.reservations)
        restored_reserved_tokens = sum(
            input_tokens + output_tokens
            for input_tokens, output_tokens in restored_outstanding.values()
        )
        restored_committed_tokens = max(
            0, restored.tokens - restored_reserved_tokens
        )
        with self._lock:
            current = self._runs.get(key)
            if current is None or current.deadline_at < time.time():
                self._runs[key] = _RunBudget(
                    limits=limits,
                    deadline_at=deadline_at,
                    started_at=time.time(),
                    calls=restored.calls,
                    tokens=restored_committed_tokens,
                    unknown_token_calls=restored.unknown_token_calls,
                    elapsed_seconds=restored.elapsed_seconds,
                    token_budget_closed=(
                        restored.unknown_token_calls > 0
                        and restored.tokens >= limits.max_tokens
                    ),
                    outstanding=restored_outstanding,
                    revision=restored.revision,
                )
            else:
                current.calls = max(current.calls, restored.calls)
                current.tokens = max(
                    current.tokens, restored_committed_tokens
                )
                for reservation_id, value in restored_outstanding.items():
                    existing = current.outstanding.get(reservation_id)
                    if existing is not None and existing != value:
                        raise ValueError(
                            "conflicting restored budget reservation"
                        )
                    current.outstanding[reservation_id] = value
                current.unknown_token_calls = max(
                    current.unknown_token_calls,
                    restored.unknown_token_calls,
                )
                current.elapsed_seconds = max(
                    current.elapsed_seconds,
                    restored.elapsed_seconds,
                )
                current.token_budget_closed = (
                    current.token_budget_closed
                    or (
                        restored.unknown_token_calls > 0
                        and restored.tokens >= limits.max_tokens
                    )
                )
                current.revision = max(
                    current.revision,
                    restored.revision,
                )

    def reserve_attempt(
        self,
        user_id: str,
        run_id: str,
        *,
        reservation_id: str,
        estimated_input_tokens: int,
    ) -> AttemptReservation:
        key = self.key(user_id, run_id)
        with self._lock:
            run = self._runs[key]
            if time.time() >= run.deadline_at:
                raise RoleBudgetError(
                    "total deadline exhausted",
                    records=(),
                    usage=self._snapshot(run),
                )
            if run.calls >= run.limits.max_calls:
                raise RoleBudgetError(
                    "model call budget exhausted",
                    records=(),
                    usage=self._snapshot(run),
                )
            if run.tokens >= run.limits.max_tokens:
                raise RoleBudgetError(
                    "token budget exhausted",
                    records=(),
                    usage=self._snapshot(run),
                )
            if run.token_budget_closed:
                raise RoleBudgetError(
                    "token budget exhausted",
                    records=(),
                    usage=self._snapshot(run),
                )
            if reservation_id in run.outstanding:
                input_tokens, output_tokens = run.outstanding[reservation_id]
                return AttemptReservation(
                    reservation_id=reservation_id,
                    usage=self._snapshot(run),
                    input_tokens=input_tokens,
                    max_output_tokens=output_tokens,
                )
            outstanding = self._outstanding_tokens(run)
            remaining = run.limits.max_tokens - run.tokens - outstanding
            if estimated_input_tokens >= remaining:
                raise RoleBudgetError(
                    "insufficient token budget for prompt and output",
                    records=(),
                    usage=self._snapshot(run),
                )
            fair_output_quota = max(
                1,
                run.limits.max_tokens // run.limits.max_calls,
            )
            output_quota = min(
                fair_output_quota,
                remaining - estimated_input_tokens,
            )
            if output_quota < 1:
                raise RoleBudgetError(
                    "insufficient token budget for model output",
                    records=(),
                    usage=self._snapshot(run),
                )
            run.calls += 1
            run.outstanding[reservation_id] = (
                estimated_input_tokens,
                output_quota,
            )
            run.revision += 1
            return AttemptReservation(
                reservation_id=reservation_id,
                usage=self._snapshot(run),
                input_tokens=estimated_input_tokens,
                max_output_tokens=output_quota,
            )

    def account(
        self,
        user_id: str,
        run_id: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        token_usage_known: bool,
        elapsed_seconds: float,
        reservation_id: str,
    ) -> BudgetUsage:
        key = self.key(user_id, run_id)
        with self._lock:
            run = self._runs[key]
            reserved = run.outstanding.pop(reservation_id, (0, 0))
            outstanding = self._outstanding_tokens(run)
            if token_usage_known and not run.token_budget_closed:
                actual = int(input_tokens or 0) + int(output_tokens or 0)
                run.tokens += actual
                if actual > sum(reserved):
                    run.token_budget_violated = True
                if run.tokens + outstanding > run.limits.max_tokens:
                    run.token_budget_violated = True
                    run.token_budget_closed = True
            elif not token_usage_known:
                run.unknown_token_calls += 1
                run.token_budget_closed = True
            if run.token_budget_closed:
                run.tokens = max(0, run.limits.max_tokens - outstanding)
            run.revision += 1
            run.elapsed_seconds = max(
                run.elapsed_seconds,
                max(0.0, time.time() - run.started_at),
                elapsed_seconds,
            )
            return self._snapshot(run)

    def usage(self, user_id: str, run_id: str) -> BudgetUsage:
        with self._lock:
            return self._snapshot(self._runs[self.key(user_id, run_id)])

    def token_budget_exceeded(self, user_id: str, run_id: str) -> bool:
        with self._lock:
            run = self._runs[self.key(user_id, run_id)]
            return run.token_budget_violated

    @staticmethod
    def _snapshot(run: _RunBudget) -> BudgetUsage:
        return BudgetUsage(
            calls=run.calls,
            tokens=min(
                run.limits.max_tokens,
                run.tokens + BudgetLedger._outstanding_tokens(run),
            ),
            reserved_tokens=BudgetLedger._outstanding_tokens(run),
            unknown_token_calls=run.unknown_token_calls,
            elapsed_seconds=run.elapsed_seconds,
            revision=run.revision,
            reservations=dict(run.outstanding),
        )

    @staticmethod
    def _outstanding_tokens(run: _RunBudget) -> int:
        return sum(
            input_tokens + output_tokens
            for input_tokens, output_tokens in run.outstanding.values()
        )


class RoleExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        records: tuple[ModelCallRecord, ...] = (),
        usage: BudgetUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.records = records
        self.usage = usage or BudgetUsage()


class RoleBudgetError(RoleExecutionError):
    pass


class RoleTimeoutError(RoleExecutionError):
    pass


class StructuredOutputError(RoleExecutionError):
    pass


@dataclass(frozen=True, slots=True)
class AgentExecution:
    output: BaseModel
    records: tuple[ModelCallRecord, ...]
    usage: BudgetUsage

    @property
    def record(self) -> ModelCallRecord:
        return self.records[-1]


class RoleResultCache:
    """Process-local replay cache.

    External calls remain at-least-once across process loss. Providers receive a
    deterministic idempotency key so task 5 can add a durable cache if required.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, ModelResponse] = {}

    def get(self, key: str) -> ModelResponse | None:
        with self._lock:
            return self._values.get(key)

    def put(self, key: str, value: ModelResponse) -> None:
        with self._lock:
            self._values[key] = value


def _parse_content(
    content: Mapping[str, Any] | BaseModel | str,
) -> Mapping[str, Any]:
    if isinstance(content, BaseModel):
        return content.model_dump(mode="python")
    if isinstance(content, Mapping):
        return content
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("structured output must be a JSON object")
    return parsed


def _input_hash(request: RoleRequest) -> str:
    body = json.dumps(
        {
            "run_id": request.run_id,
            "role": request.role,
            "attempt": request.attempt,
            "round_index": request.round_index,
            "prompt": request.prompt,
            "schema": request.output_schema.model_json_schema(),
            "tier": request.model_tier,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


class RoleAgentExecutor:
    """Reserve each attempt, validate output and retry formatting once."""

    def __init__(
        self,
        runner: RoleRunner,
        *,
        budget: BudgetLedger | None = None,
        cache: RoleResultCache | None = None,
    ) -> None:
        self._runner = runner
        self._budget = budget or BudgetLedger()
        self._cache = cache or RoleResultCache()

    def begin_run(
        self,
        user_id: str,
        run_id: str,
        limits: BudgetLimits,
        *,
        deadline_at: float,
        initial_usage: BudgetUsage | None = None,
    ) -> None:
        self._budget.begin(
            user_id,
            run_id,
            limits,
            deadline_at,
            initial_usage=initial_usage,
        )

    def ensure_run(
        self,
        user_id: str,
        run_id: str,
        limits: BudgetLimits,
        *,
        deadline_at: float,
        initial_usage: BudgetUsage | None = None,
    ) -> None:
        """Idempotently initialize or restore a checkpointed run budget."""
        self.begin_run(
            user_id,
            run_id,
            limits,
            deadline_at=deadline_at,
            initial_usage=initial_usage,
        )

    async def aexecute(
        self,
        *,
        user_id: str,
        run_id: str,
        role: str,
        prompt: str,
        model_tier: Literal["quick", "deep"],
        timeout_seconds: float,
        deadline_at: float,
        idempotency_key: str,
        allowed_evidence_ids: frozenset[str] = frozenset(),
        attempt: int = 1,
        round_index: int | None = None,
    ) -> AgentExecution:
        if not _is_async_callable(self._runner):
            raise TypeError("role runner must be an async coroutine function")
        schema = ROLE_SCHEMAS[role]
        records: list[ModelCallRecord] = []
        last_error: Exception | None = None
        message_id = message_id_for(run_id, attempt, role, round_index)
        for format_attempt in (1, 2):
            correction = (
                ""
                if format_attempt == 1
                else "\n上次输出格式无效。仅返回严格符合字段定义的 JSON 对象。"
            )
            request = RoleRequest(
                user_id=user_id,
                run_id=run_id,
                role=role,
                prompt=prompt + correction,
                output_schema=schema,
                model_tier=model_tier,
                idempotency_key=f"{idempotency_key}:attempt:{format_attempt}",
                timeout_seconds=timeout_seconds,
                deadline_at=deadline_at,
                message_id=message_id,
                generation=format_attempt,
                round_index=round_index,
                attempt=attempt,
            )
            cache_key = (
                f"{user_id}:{run_id}:{idempotency_key}:{format_attempt}:"
                f"{_input_hash(request)}"
            )
            response = self._cache.get(cache_key)
            cached = response is not None
            reservation_id = request.idempotency_key
            if not cached:
                try:
                    reservation = self._budget.reserve_attempt(
                        user_id,
                        run_id,
                        reservation_id=reservation_id,
                        estimated_input_tokens=estimate_input_tokens(
                            request.prompt
                        ),
                    )
                except RoleBudgetError as exc:
                    raise RoleBudgetError(
                        str(exc),
                        records=tuple(records),
                        usage=exc.usage,
                    ) from exc
                request = replace(
                    request,
                    max_output_tokens=reservation.max_output_tokens,
                )
                started = time.monotonic()
                remaining = min(timeout_seconds, deadline_at - time.time())
                if remaining <= 0:
                    usage = self._budget.account(
                        user_id,
                        run_id,
                        input_tokens=None,
                        output_tokens=None,
                        token_usage_known=False,
                        elapsed_seconds=0,
                        reservation_id=reservation_id,
                    )
                    record = self._record(
                        request,
                        format_attempt,
                        status="timeout",
                        error="deadline exhausted before model call",
                        elapsed_ms=0,
                    )
                    records.append(record)
                    raise RoleTimeoutError(
                        "deadline exhausted before model call",
                        records=tuple(records),
                        usage=usage,
                    )
                try:
                    async with asyncio.timeout(remaining):
                        response = await self._runner(request)
                except asyncio.CancelledError:
                    elapsed = time.monotonic() - started
                    self._budget.account(
                        user_id,
                        run_id,
                        input_tokens=None,
                        output_tokens=None,
                        token_usage_known=False,
                        elapsed_seconds=elapsed,
                        reservation_id=reservation_id,
                    )
                    raise
                except TimeoutError as exc:
                    elapsed = time.monotonic() - started
                    usage = self._budget.account(
                        user_id,
                        run_id,
                        input_tokens=None,
                        output_tokens=None,
                        token_usage_known=False,
                        elapsed_seconds=elapsed,
                        reservation_id=reservation_id,
                    )
                    record = self._record(
                        request,
                        format_attempt,
                        status="timeout",
                        error=f"{role} exceeded node timeout",
                        elapsed_ms=int(elapsed * 1000),
                    )
                    records.append(record)
                    raise RoleTimeoutError(
                        f"{role} exceeded node timeout",
                        records=tuple(records),
                        usage=usage,
                    ) from exc
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    usage = self._budget.account(
                        user_id,
                        run_id,
                        input_tokens=None,
                        output_tokens=None,
                        token_usage_known=False,
                        elapsed_seconds=elapsed,
                        reservation_id=reservation_id,
                    )
                    record = self._record(
                        request,
                        format_attempt,
                        status="error",
                        error=f"{type(exc).__name__}: {exc}",
                        elapsed_ms=int(elapsed * 1000),
                    )
                    records.append(record)
                    raise RoleExecutionError(
                        f"{role} runner failed",
                        records=tuple(records),
                        usage=usage,
                    ) from exc
                if not isinstance(response, ModelResponse):
                    elapsed = time.monotonic() - started
                    usage = self._budget.account(
                        user_id,
                        run_id,
                        input_tokens=None,
                        output_tokens=None,
                        token_usage_known=False,
                        elapsed_seconds=elapsed,
                        reservation_id=reservation_id,
                    )
                    error = "role runner must return ModelResponse"
                    records.append(
                        self._record(
                            request,
                            format_attempt,
                            status="error",
                            error=error,
                            elapsed_ms=int(elapsed * 1000),
                        )
                    )
                    raise RoleExecutionError(
                        error,
                        records=tuple(records),
                        usage=usage,
                    )
                elapsed = time.monotonic() - started
                usage = self._budget.account(
                    user_id,
                    run_id,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    token_usage_known=bool(response.token_usage_known),
                    elapsed_seconds=elapsed,
                    reservation_id=reservation_id,
                )
                self._cache.put(cache_key, response)
            else:
                usage = self._budget.usage(user_id, run_id)
                elapsed = 0.0
            if self._budget.token_budget_exceeded(user_id, run_id):
                records.append(
                    self._record(
                        request,
                        format_attempt,
                        response=response,
                        status="error",
                        error="token budget exceeded by model response",
                        elapsed_ms=int(elapsed * 1000),
                        cached=cached,
                    )
                )
                raise RoleBudgetError(
                    "token budget exceeded",
                    records=tuple(records),
                    usage=usage,
                )
            try:
                output = schema.model_validate(_parse_content(response.content))
                referenced = tuple(getattr(output, "evidence_ids", ()))
                unknown = set(referenced).difference(allowed_evidence_ids)
                if unknown:
                    raise ValueError("output references unknown evidence ids")
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = exc
                records.append(
                    self._record(
                        request,
                        format_attempt,
                        response=response,
                        status="invalid",
                        error=f"{type(exc).__name__}: {exc}",
                        elapsed_ms=int(elapsed * 1000),
                        cached=cached,
                    )
                )
                continue
            records.append(
                self._record(
                    request,
                    format_attempt,
                    response=response,
                    status="success",
                    elapsed_ms=int(elapsed * 1000),
                    evidence_ids=referenced,
                    cached=cached,
                )
            )
            return AgentExecution(
                output=output,
                records=tuple(records),
                usage=usage,
            )
        raise StructuredOutputError(
            f"{role} returned invalid structured output",
            records=tuple(records),
            usage=usage,
        ) from last_error

    def execute(self, **kwargs: Any) -> AgentExecution:
        """Legacy synchronous adapter; no worker thread or fake cancellation."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aexecute(**kwargs))
        raise RuntimeError("execute() cannot run inside an event loop; use aexecute()")

    @staticmethod
    def _record(
        request: RoleRequest,
        attempt: int,
        *,
        response: ModelResponse | None = None,
        status: Literal["success", "invalid", "error", "timeout"],
        error: str | None = None,
        elapsed_ms: int,
        evidence_ids: tuple[str, ...] = (),
        cached: bool = False,
    ) -> ModelCallRecord:
        return ModelCallRecord(
            call_id=f"{request.idempotency_key}:model",
            role=request.role,
            model_tier=request.model_tier,
            model_name=response.model_name if response else "unknown",
            elapsed_ms=max(0, elapsed_ms),
            input_tokens=response.input_tokens if response else None,
            output_tokens=response.output_tokens if response else None,
            token_usage_known=bool(response and response.token_usage_known),
            tool_names=tuple(sorted(set(response.tool_names))) if response else (),
            evidence_ids=tuple(sorted(set(evidence_ids))),
            attempt=attempt,
            status=status,
            error=error[:500] if error else None,
            cached=cached,
        )


class ChatModelRoleRunner:
    """OpenAI-compatible JSON-mode runner with native request timeouts."""

    def __init__(
        self,
        committee_config: Mapping[str, Any] | None = None,
        *,
        stream_sink: RoleStreamSink | None = None,
    ) -> None:
        if stream_sink is not None and not _is_async_callable(stream_sink):
            raise TypeError("stream_sink must be an async callable")
        self._committee_config = dict(committee_config or {})
        self._stream_sink = stream_sink

    async def __call__(self, request: RoleRequest) -> ModelResponse:
        model = build_chat_model(
            request.user_id,
            tier=request.model_tier,
            committee_config=self._committee_config,
            temperature=0,
            streaming=True,
            request_timeout=request.timeout_seconds,
        )
        bind_options: dict[str, Any] = {
            "response_format": {"type": "json_object"}
        }
        if request.max_output_tokens is not None:
            bind_options["max_tokens"] = request.max_output_tokens
        json_model = model.bind(**bind_options)
        messages = [HumanMessage(content=request.prompt)]
        if not _supports_native_streaming(model):
            message = await json_model.ainvoke(messages)
            return self._response_from_message(message, model)

        parser = IncrementalChatMessageParser()
        raw_parts: list[str] = []
        decoded_length = 0
        aggregate_chunk: AIMessageChunk | None = None
        received_chunk = False
        try:
            async for chunk in json_model.astream(messages):
                if not received_chunk:
                    received_chunk = True
                    await self._publish(
                        "message_started",
                        request,
                        offset=0,
                    )
                aggregate_chunk = (
                    chunk
                    if aggregate_chunk is None
                    else aggregate_chunk + chunk
                )
                text = self._content_text(getattr(chunk, "content", ""))
                raw_parts.append(text)
                for delta in parser.feed(text):
                    await self._publish(
                        "message_delta",
                        request,
                        offset=decoded_length,
                        delta=delta,
                    )
                    decoded_length += len(delta)
        except (NotImplementedError, AttributeError):
            if received_chunk:
                raise
            message = await json_model.ainvoke(messages)
            return self._response_from_message(message, model)

        return self._response_from_message(
            aggregate_chunk,
            model,
            content="".join(raw_parts),
        )

    async def _publish(
        self,
        event_type: Literal["message_started", "message_delta"],
        request: RoleRequest,
        *,
        offset: int,
        delta: str | None = None,
    ) -> None:
        if self._stream_sink is None:
            return
        payload: dict[str, Any] = {
            "message_id": request.message_id,
            "role": request.role,
            "node": request.role,
            "round": request.round_index,
            "generation": request.generation,
            "offset": offset,
        }
        if delta is not None:
            payload["delta"] = delta
        try:
            await self._stream_sink(
                ChatStreamEvent(event_type=event_type, payload=payload)
            )
        except Exception:
            return

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, (list, tuple)):
            return ""
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, Mapping) and block.get("type") == "text":
                text = block.get("text")
            else:
                text = None
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)

    @classmethod
    def _response_from_message(
        cls,
        message: Any | None,
        model: Any,
        *,
        content: str | None = None,
    ) -> ModelResponse:
        if message is None:
            usage: Mapping[str, Any] = {}
            response_meta: Mapping[str, Any] = {}
            message_content = ""
        else:
            raw_usage = getattr(message, "usage_metadata", None) or {}
            usage = raw_usage if isinstance(raw_usage, Mapping) else {}
            raw_meta = getattr(message, "response_metadata", None) or {}
            response_meta = raw_meta if isinstance(raw_meta, Mapping) else {}
            message_content = cls._content_text(
                getattr(message, "content", "")
            )
        fallback = response_meta.get("token_usage") or {}
        if not isinstance(fallback, Mapping):
            fallback = {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if input_tokens is None:
            input_tokens = fallback.get("prompt_tokens")
        if output_tokens is None:
            output_tokens = fallback.get("completion_tokens")
        known = input_tokens is not None and output_tokens is not None
        model_name = str(
            response_meta.get("model_name")
            or getattr(model, "model_name", None)
            or "openai-compatible"
        )
        return ModelResponse(
            content=message_content if content is None else content,
            model_name=model_name,
            input_tokens=int(input_tokens) if input_tokens is not None else None,
            output_tokens=int(output_tokens) if output_tokens is not None else None,
            token_usage_known=known,
        )
