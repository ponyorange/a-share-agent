from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Callable, Iterator, Literal

ProgressStep = Literal[
    "delegate", "list_sources", "search", "describe",
    "fetch", "sandbox", "submit", "run_python",
]
ProgressPhase = Literal["data_agent", "main_agent"]
ProgressStatus = Literal["started", "completed", "failed"]
ProgressSink = Callable[[dict[str, object]], None]

PROGRESS_STEPS: frozenset[str] = frozenset({
    "delegate", "list_sources", "search", "describe",
    "fetch", "sandbox", "submit", "run_python",
})
PROGRESS_PHASES: frozenset[str] = frozenset({"data_agent", "main_agent"})
PROGRESS_STATUSES: frozenset[str] = frozenset({"started", "completed", "failed"})
_ALLOWED_EMIT_KEYS: frozenset[str] = frozenset({
    "step", "status", "phase", "source", "interface", "rows", "truncated", "error_code",
})
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_IDENTIFIER_LIMITS = {"source": 128, "interface": 256}

_SINK: ContextVar[ProgressSink | None] = ContextVar("advisor_progress_sink", default=None)


class ProgressValidationError(ValueError):
    """Raised when progress input violates the allowlisted protocol."""


def _short(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[:limit]


def _validate_step(step: object) -> ProgressStep:
    if not isinstance(step, str) or step not in PROGRESS_STEPS:
        raise ProgressValidationError(f"invalid progress step: {step!r}")
    return step  # type: ignore[return-value]


def _validate_status(status: object) -> ProgressStatus:
    if not isinstance(status, str) or status not in PROGRESS_STATUSES:
        raise ProgressValidationError(f"invalid progress status: {status!r}")
    return status  # type: ignore[return-value]


def _validate_phase(phase: object) -> ProgressPhase:
    if not isinstance(phase, str) or phase not in PROGRESS_PHASES:
        raise ProgressValidationError(f"invalid progress phase: {phase!r}")
    return phase  # type: ignore[return-value]


def _validate_error_code(error_code: object) -> str:
    if not isinstance(error_code, str) or not _ERROR_CODE_PATTERN.fullmatch(error_code):
        raise ProgressValidationError(f"invalid progress error_code: {error_code!r}")
    return error_code


def validate_progress_identifier(value: object, *, field: str) -> str:
    limit = _IDENTIFIER_LIMITS.get(field)
    if (
        limit is None
        or not isinstance(value, str)
        or len(value) > limit
        or not _IDENTIFIER_PATTERN.fullmatch(value)
    ):
        raise ProgressValidationError(f"invalid progress {field}")
    return value


def _stage_message(
    step: ProgressStep,
    status: ProgressStatus,
    *,
    source: str | None = None,
    interface: str | None = None,
    rows: int | None = None,
) -> str:
    if step == "delegate":
        if status == "started":
            return "正在启动数据子 Agent"
        if status == "completed":
            return "数据查询完成"
        return "数据子 Agent 执行失败"

    if step == "list_sources":
        if status == "started":
            return "正在检查可用数据源"
        if status == "completed":
            return "可用数据源检查完成"
        return "数据源检查失败"

    if step == "search":
        if status == "started":
            return f"正在搜索 {source} 数据接口" if source else "正在搜索数据接口"
        if status == "completed":
            return "数据接口搜索完成"
        return "数据接口搜索失败"

    if step == "describe":
        if status == "started":
            if source and interface:
                return f"正在读取 {source}/{interface} 参数定义"
            return "正在读取接口参数定义"
        if status == "completed":
            return "参数定义读取完成"
        return "参数定义读取失败"

    if step == "fetch":
        if status == "started":
            if source and interface:
                return f"正在调用 {source}/{interface}"
            return "正在调用数据接口"
        if status == "completed":
            if rows is not None:
                return f"已获取 {rows} 行数据"
            return "已获取数据"
        return "数据接口调用失败"

    if step == "sandbox":
        if status == "started":
            return "正在计算和整理数据"
        if status == "completed":
            return "计算完成"
        return "数据计算失败"

    if step == "run_python":
        if status == "started":
            return "正在执行 Python 脚本"
        if status == "completed":
            return "Python 脚本执行完成"
        return "Python 脚本执行失败"

    if step == "submit":
        if status == "started":
            return "正在校验来源与结果"
        if status == "completed":
            return "结果校验完成"
        return "结果校验失败"

    return "数据查询处理中"


@dataclass(frozen=True)
class ProgressEvent:
    step: ProgressStep
    status: ProgressStatus
    source: str | None = None
    interface: str | None = None
    rows: int | None = None
    truncated: bool | None = None
    error_code: str | None = None
    phase: ProgressPhase = "data_agent"

    def __post_init__(self) -> None:
        object.__setattr__(self, "step", _validate_step(self.step))
        object.__setattr__(self, "status", _validate_status(self.status))
        object.__setattr__(self, "phase", _validate_phase(self.phase))
        if self.source is not None:
            object.__setattr__(
                self,
                "source",
                validate_progress_identifier(self.source, field="source"),
            )
        if self.interface is not None:
            object.__setattr__(
                self,
                "interface",
                validate_progress_identifier(self.interface, field="interface"),
            )
        if self.error_code is not None:
            object.__setattr__(self, "error_code", _validate_error_code(self.error_code))
        if self.rows is not None:
            object.__setattr__(self, "rows", max(0, min(int(self.rows), 50_000)))

    @property
    def message(self) -> str:
        return _stage_message(
            self.step,
            self.status,
            source=self.source,
            interface=self.interface,
            rows=self.rows,
        )

    def as_dict(self) -> dict[str, object]:
        raw = asdict(self)
        raw["message"] = self.message
        return {key: value for key, value in raw.items() if value is not None}


@contextmanager
def bind_progress_sink(sink: ProgressSink) -> Iterator[None]:
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def emit_progress(**kwargs: object) -> None:
    extra = set(kwargs) - _ALLOWED_EMIT_KEYS
    if extra:
        raise ProgressValidationError(
            f"unexpected progress fields: {', '.join(sorted(extra))}",
        )
    event = ProgressEvent(
        step=kwargs["step"],  # type: ignore[arg-type]
        status=kwargs["status"],  # type: ignore[arg-type]
        source=kwargs.get("source"),  # type: ignore[arg-type]
        interface=kwargs.get("interface"),  # type: ignore[arg-type]
        rows=kwargs.get("rows"),  # type: ignore[arg-type]
        truncated=kwargs.get("truncated"),  # type: ignore[arg-type]
        error_code=kwargs.get("error_code"),  # type: ignore[arg-type]
        phase=kwargs.get("phase", "data_agent"),  # type: ignore[arg-type]
    )
    sink = _SINK.get()
    if sink is None:
        return
    sink(event.as_dict())


def progress_to_tool_trace(event: dict[str, object]) -> dict[str, str]:
    step = _validate_step(event["step"])
    status = _validate_status(event["status"])
    source_value = event.get("source")
    interface_value = event.get("interface")
    source = (
        validate_progress_identifier(source_value, field="source")
        if source_value is not None
        else ""
    )
    interface = (
        validate_progress_identifier(interface_value, field="interface")
        if interface_value is not None
        else ""
    )
    prefix = "/".join(value for value in (source, interface) if value)

    rows = event.get("rows")
    if rows is not None:
        detail = f"{max(0, min(int(rows), 50_000))} 行"
    else:
        detail = _stage_message(
            step,
            status,
            source=source or None,
            interface=interface or None,
        )

    if event.get("truncated") is True:
        detail += "，已截断"

    error_code = event.get("error_code")
    if error_code is not None:
        detail += f"，错误码 {_validate_error_code(error_code)}"

    content = f"{prefix}：{detail}" if prefix else detail
    phase_value = event.get("phase", "data_agent")
    phase = _validate_phase(phase_value) if phase_value is not None else "data_agent"
    return {"tool": f"{phase}.{step}", "content": content[:800]}
