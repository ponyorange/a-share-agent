from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from ..config_loader import default_config
from .data_agent.models import DataAgentLimits
from .data_agent.sandbox import SandboxClient
from .progress import emit_progress

_DATASET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_ERROR_MESSAGES = {
    "invalid_dataset_name": "数据集名称无效",
    "invalid_dataset_json": "数据集 JSON 无效",
    "invalid_dataset_shape": "数据集格式无效，需要对象数组或可归一化的对象",
    "dataset_too_large": "数据集超过行数或字节上限",
    "dataset_limit_exceeded": "本轮登记数据集数量已达上限",
    "dataset_not_registered": "数据集未登记",
    "invalid_dataset_ids": "数据集参数错误",
    "invalid_inline_datasets": "inline 数据集参数错误",
    "python_call_limit_exceeded": "本轮 Python 调用次数已达上限",
    "sandbox_config_missing": "沙箱未配置",
    "sandbox_config_invalid": "沙箱配置无效",
    "sandbox_timeout": "计算超时",
    "sandbox_unavailable": "计算服务暂不可用",
    "sandbox_invalid_output": "计算结果无效",
    "sandbox_rejected": "计算失败",
}


class AgentPythonLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_rows_per_dataset: int = Field(default=200, ge=1, le=200)
    max_bytes_per_dataset: int = Field(default=204_800, ge=1, le=204_800)
    max_registered_datasets: int = Field(default=5, ge=1, le=5)
    max_python_calls: int = Field(default=3, ge=1, le=3)
    sandbox_timeout_seconds: int = Field(default=30, ge=1, le=30)
    sandbox_memory_mb: int = Field(default=512, ge=128, le=512)
    max_output_bytes: int = Field(default=1_048_576, ge=1024, le=1_048_576)

    @classmethod
    def from_config(cls, value: dict[str, Any] | None) -> "AgentPythonLimits":
        return cls.model_validate(value or {})

    def as_sandbox_limits(self) -> DataAgentLimits:
        return DataAgentLimits(
            sandbox_timeout_seconds=self.sandbox_timeout_seconds,
            sandbox_memory_mb=self.sandbox_memory_mb,
            max_output_bytes=self.max_output_bytes,
        )


class RequestPythonWorkspace:
    def __init__(self, limits: AgentPythonLimits):
        self.limits = limits
        self._datasets: dict[str, list[dict[str, Any]]] = {}
        self._python_calls = 0

    def register(self, name: str, rows: list[dict[str, Any]]) -> None:
        if name in self._datasets:
            self._datasets[name] = rows
            return
        if len(self._datasets) >= self.limits.max_registered_datasets:
            raise ValueError("dataset_limit_exceeded")
        self._datasets[name] = rows

    def export(self, dataset_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        missing = [dataset_id for dataset_id in dataset_ids if dataset_id not in self._datasets]
        if missing:
            raise KeyError("dataset_not_registered")
        return {dataset_id: self._datasets[dataset_id] for dataset_id in dataset_ids}

    def begin_python_call(self) -> bool:
        if self._python_calls >= self.limits.max_python_calls:
            return False
        self._python_calls += 1
        return True


def _tool_error(code: str) -> str:
    return json.dumps(
        {
            "error": {
                "code": code,
                "message": _ERROR_MESSAGES.get(code, "计算失败"),
            }
        },
        ensure_ascii=False,
    )


def _map_runtime_error(exc: RuntimeError) -> str:
    code = str(exc)
    if code.startswith("sandbox_rejected:"):
        return "sandbox_rejected"
    if code in _ERROR_MESSAGES:
        return code
    return "sandbox_rejected"


def _rows_byte_size(rows: list[dict[str, Any]]) -> int:
    return len(json.dumps(rows, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _normalize_dataset_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        if not value:
            return []
        if all(isinstance(item, dict) for item in value):
            return value
        raise ValueError("invalid_dataset_shape")
    if isinstance(value, dict):
        for key in ("items", "data", "rows"):
            nested = value.get(key)
            if isinstance(nested, list) and all(isinstance(item, dict) for item in nested):
                return nested
        return [value]
    raise ValueError("invalid_dataset_shape")


def _validate_dataset(name: str, rows: list[dict[str, Any]], limits: AgentPythonLimits) -> None:
    if not _DATASET_NAME.fullmatch(name):
        raise ValueError("invalid_dataset_name")
    if len(rows) > limits.max_rows_per_dataset:
        raise ValueError("dataset_too_large")
    if _rows_byte_size(rows) > limits.max_bytes_per_dataset:
        raise ValueError("dataset_too_large")


def _parse_dataset_ids(dataset_ids_json: str) -> list[str]:
    try:
        dataset_ids = json.loads(dataset_ids_json)
    except ValueError as exc:
        raise ValueError("invalid_dataset_ids") from exc
    if not isinstance(dataset_ids, list):
        raise ValueError("invalid_dataset_ids")
    if any(not isinstance(dataset_id, str) for dataset_id in dataset_ids):
        raise ValueError("invalid_dataset_ids")
    if len(set(dataset_ids)) != len(dataset_ids):
        raise ValueError("invalid_dataset_ids")
    return dataset_ids


def _parse_inline_datasets(
    inline_datasets_json: str,
    limits: AgentPythonLimits,
) -> dict[str, list[dict[str, Any]]]:
    try:
        raw = json.loads(inline_datasets_json)
    except ValueError as exc:
        raise ValueError("invalid_inline_datasets") from exc
    if not isinstance(raw, dict):
        raise ValueError("invalid_inline_datasets")
    datasets: dict[str, list[dict[str, Any]]] = {}
    for name, value in raw.items():
        if not isinstance(name, str):
            raise ValueError("invalid_inline_datasets")
        rows = _normalize_dataset_value(value)
        _validate_dataset(name, rows, limits)
        datasets[name] = rows
    return datasets


def build_agent_python_tools(user_id: str) -> list[BaseTool]:
    del user_id
    config = default_config().get("agent_python")
    limits = AgentPythonLimits.from_config(config if isinstance(config, dict) else {})
    workspace = RequestPythonWorkspace(limits)

    @tool
    def register_tool_dataset(name: str, tool_result_json: str) -> str:
        """把本轮某次成功工具返回的 JSON 登记为临时 dataset，供 run_python_script 使用。
        name 仅允许字母数字下划线短横线；仅当前请求有效。"""
        try:
            raw = json.loads(tool_result_json)
        except ValueError:
            return _tool_error("invalid_dataset_json")
        try:
            rows = _normalize_dataset_value(raw)
            _validate_dataset(name, rows, limits)
            workspace.register(name, rows)
        except ValueError as exc:
            return _tool_error(str(exc))
        return json.dumps(
            {"ok": True, "name": name, "rows": len(rows)},
            ensure_ascii=False,
        )

    @tool
    def run_python_script(
        code: str,
        dataset_ids_json: str = "[]",
        inline_datasets_json: str = "{}",
    ) -> str:
        """在沙箱运行 Python。已预置 pd/np（推荐直接用，也可 import pandas/numpy）；
        仅允许 pandas/numpy/math/statistics/datetime。可用 datasets['id']。
        优先赋值 result；否则回传 stdout/stderr。小计算用本工具；大表/跨源仍用 delegate_data_task。"""
        emit_progress(step="run_python", status="started", phase="main_agent")
        if not workspace.begin_python_call():
            emit_progress(
                step="run_python",
                status="failed",
                phase="main_agent",
                error_code="python_call_limit_exceeded",
            )
            return _tool_error("python_call_limit_exceeded")
        try:
            dataset_ids = _parse_dataset_ids(dataset_ids_json)
            datasets = workspace.export(dataset_ids) if dataset_ids else {}
            datasets.update(_parse_inline_datasets(inline_datasets_json, limits))
        except ValueError as exc:
            emit_progress(
                step="run_python",
                status="failed",
                phase="main_agent",
                error_code=str(exc),
            )
            return _tool_error(str(exc))
        except KeyError:
            emit_progress(
                step="run_python",
                status="failed",
                phase="main_agent",
                error_code="dataset_not_registered",
            )
            return _tool_error("dataset_not_registered")

        try:
            client = SandboxClient.from_env()
            payload = client.execute(
                code,
                datasets,
                limits.as_sandbox_limits(),
                require_result=False,
            )
        except RuntimeError as exc:
            error_code = _map_runtime_error(exc)
            emit_progress(
                step="run_python",
                status="failed",
                phase="main_agent",
                error_code=error_code,
            )
            return _tool_error(error_code)

        emit_progress(step="run_python", status="completed", phase="main_agent")
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)

    return [register_tool_dataset, run_python_script]
