from __future__ import annotations

import json
import math
import os
from typing import Any

import httpx
from langchain_core.tools import BaseTool, tool

from ..progress import emit_progress
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
    "sandbox_rejected": "计算失败",
    "python_retry_limit_exceeded": "Python 分析重试次数已达上限",
    "generated_code_failed": "生成代码执行失败",
    "result_not_assigned": "必须把最终结果赋值给变量 result",
    "syntax_error": "代码语法错误",
    "import_not_allowed": "不允许的 import",
    "output_too_large": "计算结果过大",
    "result_not_finite": "结果包含非有限数值",
    "runner_failed": "沙箱运行失败",
    "invalid_output_limit": "输出上限无效",
}
_SAFE_RUNNER_ERROR_CODES = frozenset(
    {
        "generated_code_failed",
        "import_not_allowed",
        "invalid_output_limit",
        "output_too_large",
        "result_not_assigned",
        "result_not_finite",
        "runner_failed",
        "syntax_error",
    }
)
SAFE_EXCEPTION_TYPES = frozenset(
    {
        "ArithmeticError",
        "AttributeError",
        "Exception",
        "ImportError",
        "IndexError",
        "KeyError",
        "LookupError",
        "ModuleNotFoundError",
        "NameError",
        "RuntimeError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    }
)


class SandboxRejected(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        exception_type: str | None = None,
        line: int | None = None,
    ) -> None:
        self.code = code
        self.exception_type = (
            exception_type if exception_type in SAFE_EXCEPTION_TYPES else None
        )
        self.line = (
            line
            if isinstance(line, int) and not isinstance(line, bool) and line > 0
            else None
        )
        super().__init__(f"sandbox_rejected:{code}")


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

    @classmethod
    def from_env(cls) -> "SandboxClient":
        base_url = (os.environ.get("SANDBOX_URL") or "").strip()
        token = (os.environ.get("SANDBOX_TOKEN") or "").strip()
        if not base_url or not token:
            raise RuntimeError("sandbox_config_missing") from None
        try:
            url = httpx.URL(base_url)
        except httpx.InvalidURL:
            raise RuntimeError("sandbox_config_invalid") from None
        if url.scheme not in {"http", "https"} or not url.host or len(token) < 32:
            raise RuntimeError("sandbox_config_invalid") from None
        return cls(base_url=base_url, token=token)

    def execute(
        self,
        code: str,
        datasets: dict[str, list[dict[str, Any]]],
        limits: DataAgentLimits,
        *,
        require_result: bool = True,
    ) -> Any:
        body = {
            "code": code,
            "datasets": datasets,
            "timeout_seconds": limits.sandbox_timeout_seconds,
            "memory_mb": limits.sandbox_memory_mb,
            "max_output_bytes": limits.max_output_bytes,
            "require_result": require_result,
        }
        try:
            response = self._client.post(
                "/v1/execute",
                json=body,
                headers={"X-Sandbox-Token": self.token},
                timeout=httpx.Timeout(limits.sandbox_timeout_seconds + 10, connect=5),
            )
        except httpx.TimeoutException:
            raise RuntimeError("sandbox_timeout") from None
        except httpx.HTTPError:
            raise RuntimeError("sandbox_unavailable") from None

        if len(response.content) > limits.max_output_bytes + _RESPONSE_OVERHEAD_BYTES:
            raise RuntimeError("sandbox_invalid_output") from None
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("sandbox_invalid_output") from None
        if not isinstance(payload, dict):
            raise RuntimeError("sandbox_invalid_output") from None

        if response.status_code >= 400 or not payload.get("ok"):
            error = payload.get("error") or {}
            if isinstance(error, str):
                code_value = error
            elif isinstance(error, dict):
                code_value = error.get("code")
            else:
                code_value = None
            code = str(code_value or response.status_code)
            if code == "execution_timeout":
                raise RuntimeError("sandbox_timeout") from None
            if code == "sandbox_failed":
                raise RuntimeError("sandbox_unavailable") from None
            exception_type = payload.get("exception_type")
            line = payload.get("line")
            raise SandboxRejected(
                code,
                exception_type=exception_type if isinstance(exception_type, str) else None,
                line=line if isinstance(line, int) and not isinstance(line, bool) else None,
            ) from None

        result = payload.get("result")
        self.last_metrics = _safe_metrics(payload.get("metrics"))
        if require_result:
            _validate_value(result)
            return result

        stdout = payload.get("stdout") or ""
        stderr = payload.get("stderr") or ""
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            raise RuntimeError("sandbox_invalid_output") from None
        if result is not None:
            _validate_value(result)
        return {"result": result, "stdout": stdout, "stderr": stderr}


def _parse_dataset_ids(dataset_ids_json: str) -> list[str]:
    try:
        dataset_ids = json.loads(dataset_ids_json)
    except ValueError:
        raise ValueError("invalid_dataset_ids") from None
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


def _map_runtime_error_code(exc: RuntimeError) -> str:
    code_value = str(exc)
    if code_value.startswith("sandbox_rejected:"):
        runner_code = code_value.split(":", 1)[1]
        if runner_code in _SAFE_RUNNER_ERROR_CODES:
            return runner_code
        return "sandbox_rejected"
    return code_value


def format_sandbox_tool_error(exc: RuntimeError) -> str:
    code = _map_runtime_error_code(exc)
    error: dict[str, Any] = {
        "code": code,
        "message": _ERROR_MESSAGES.get(code, "计算失败"),
    }
    if isinstance(exc, SandboxRejected):
        if code == "generated_code_failed" and exc.exception_type in SAFE_EXCEPTION_TYPES:
            error["exception_type"] = exc.exception_type
            error["message"] = f"生成代码执行失败：{exc.exception_type}"
        if (
            code == "syntax_error"
            and isinstance(exc.line, int)
            and not isinstance(exc.line, bool)
            and exc.line > 0
        ):
            error["line"] = exc.line
    return json.dumps({"error": error}, ensure_ascii=False)


def build_python_tool(workspace: DatasetWorkspace, client: SandboxClient) -> BaseTool:
    @tool
    def run_python_analysis(code: str, dataset_ids_json: str) -> str:
        """在沙箱中运行只读 Python 分析。已预置 pd/np（推荐直接用，也可 import pandas/numpy）；
        用 datasets['dataset_id'] 取 DataFrame；必须赋值给 result。
        仅允许 pandas/numpy/math/statistics/datetime/time/zoneinfo/json/re/collections/itertools/functools。
        失败时按 error.code / exception_type / line 改代码。
        禁止 read_csv/打开文件或访问网络。"""
        emit_progress(step="sandbox", status="started")
        if not workspace.begin_python_analysis():
            emit_progress(
                step="sandbox",
                status="failed",
                error_code="python_retry_limit_exceeded",
            )
            return _tool_error("python_retry_limit_exceeded")
        try:
            dataset_ids = _parse_dataset_ids(dataset_ids_json)
            datasets = workspace.export(dataset_ids)
        except ValueError:
            workspace.abort_python_analysis()
            emit_progress(
                step="sandbox",
                status="failed",
                error_code="invalid_dataset_ids",
            )
            return _tool_error("invalid_dataset_ids")
        except KeyError:
            workspace.abort_python_analysis()
            emit_progress(
                step="sandbox",
                status="failed",
                error_code="dataset_not_in_request",
            )
            return _tool_error("dataset_not_in_request")

        try:
            result = client.execute(code, datasets, workspace.limits)
            try:
                evidence = workspace.record_sandbox_result(result)
            except ValueError:
                emit_progress(
                    step="sandbox",
                    status="failed",
                    error_code="sandbox_invalid_output",
                )
                return _tool_error("sandbox_invalid_output")
            emit_progress(step="sandbox", status="completed")
            return json.dumps(
                {
                    "result_id": evidence.result_id,
                    "result_summary": evidence.summary,
                    "result": evidence.result,
                },
                ensure_ascii=False,
                allow_nan=False,
            )
        except RuntimeError as exc:
            error_code = _map_runtime_error_code(exc)
            emit_progress(
                step="sandbox",
                status="failed",
                error_code=error_code,
            )
            return format_sandbox_tool_error(exc)

    return run_python_analysis
