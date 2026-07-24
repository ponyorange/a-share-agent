from __future__ import annotations

import json
import math
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool

from .models import DataAgentLimits
from .workspace import DatasetWorkspace


_MAX_JSON_DEPTH = 20
_RESPONSE_OVERHEAD_BYTES = 16_384
_ALLOWED_METRIC_KEYS = {
    "elapsed_ms",
    "cpu_time_ms",
    "memory_peak_mb",
    "output_bytes",
}
_ERROR_MESSAGES = {
    "invalid_dataset_ids": "数据集参数错误",
    "dataset_not_in_request": "数据集不可用",
    "sandbox_timeout": "计算超时",
    "sandbox_unavailable": "计算服务暂不可用",
    "sandbox_invalid_output": "计算结果无效",
}


def _validate_value(value: Any, depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise RuntimeError("sandbox_invalid_output")
    if isinstance(value, float) and not math.isfinite(value):
        raise RuntimeError("sandbox_invalid_output")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise RuntimeError("sandbox_invalid_output")
            _validate_value(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_value(child, depth + 1)


def _safe_metrics(metrics: Any) -> dict[str, int | float]:
    if not isinstance(metrics, dict):
        return {}
    safe: dict[str, int | float] = {}
    for key in _ALLOWED_METRIC_KEYS:
        value = metrics.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)):
            safe[key] = value
    return safe


class SandboxClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.token = token
        self.last_metrics: dict[str, int | float] = {}
        self._client = httpx.Client(base_url=base_url, transport=transport)

    def execute(
        self,
        code: str,
        datasets: dict[str, list[dict[str, Any]]],
        limits: DataAgentLimits,
    ) -> Any:
        body = {
            "code": code,
            "datasets": datasets,
            "timeout_seconds": limits.sandbox_timeout_seconds,
            "memory_mb": limits.sandbox_memory_mb,
            "max_output_bytes": limits.max_output_bytes,
        }
        try:
            response = self._client.post(
                "/v1/execute",
                json=body,
                headers={"X-Sandbox-Token": self.token},
                timeout=httpx.Timeout(limits.sandbox_timeout_seconds + 10, connect=5),
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("sandbox_timeout") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("sandbox_unavailable") from exc

        if len(response.content) > limits.max_output_bytes + _RESPONSE_OVERHEAD_BYTES:
            raise RuntimeError("sandbox_invalid_output")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("sandbox_invalid_output") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("sandbox_invalid_output")

        if response.status_code >= 400 or not payload.get("ok"):
            error = payload.get("error") or {}
            code_value = error.get("code") if isinstance(error, dict) else None
            code = str(code_value or response.status_code)
            raise RuntimeError(f"sandbox_rejected:{code}")

        result = payload.get("result")
        _validate_value(result)
        self.last_metrics = _safe_metrics(payload.get("metrics"))
        return result


def _parse_dataset_ids(dataset_ids_json: str) -> list[str]:
    try:
        dataset_ids = json.loads(dataset_ids_json)
    except ValueError as exc:
        raise ValueError("invalid_dataset_ids") from exc
    if not isinstance(dataset_ids, list) or not dataset_ids:
        raise ValueError("invalid_dataset_ids")
    if any(not isinstance(dataset_id, str) for dataset_id in dataset_ids):
        raise ValueError("invalid_dataset_ids")
    if len(set(dataset_ids)) != len(dataset_ids):
        raise ValueError("invalid_dataset_ids")
    return dataset_ids


def _tool_error(code: str) -> str:
    return json.dumps(
        {"error": {"code": code, "message": _ERROR_MESSAGES.get(code, "计算失败")}},
        ensure_ascii=False,
    )


def build_python_tool(workspace: DatasetWorkspace, client: SandboxClient) -> BaseTool:
    @tool
    def run_python_analysis(code: str, dataset_ids_json: str) -> str:
        """在沙箱中运行只读 Python 分析代码，仅可使用本次请求已保存的数据集。"""
        try:
            dataset_ids = _parse_dataset_ids(dataset_ids_json)
            datasets = workspace.export(dataset_ids)
            result = client.execute(code, datasets, workspace.limits)
            return json.dumps({"result": result}, ensure_ascii=False)
        except ValueError:
            return _tool_error("invalid_dataset_ids")
        except KeyError:
            return _tool_error("dataset_not_in_request")
        except RuntimeError as exc:
            code_value = str(exc)
            if code_value.startswith("sandbox_rejected:"):
                code_value = "sandbox_rejected"
            return _tool_error(code_value)

    return run_python_analysis
