from __future__ import annotations

import uuid
from typing import Any

from langchain_core.tools import BaseTool, tool

from ..progress import ProgressValidationError, emit_progress
from ...config_loader import default_config
from .graph import run_data_agent
from .models import DataAgentFailure, DataAgentLimits, DataAgentResult
from .sandbox import SandboxClient
from .workspace import DatasetWorkspace


def _failure(code: str, message: str) -> DataAgentResult:
    return DataAgentResult(
        answer="",
        data={},
        sources=[],
        computation=[],
        warnings=[],
        failures=[DataAgentFailure(code=code, message=message)],
    )


def _has_delegate_data(value: Any) -> bool:
    return value not in (None, {}, [])


def _result_error_code(result: DataAgentResult) -> str | None:
    if not result.failures or _has_delegate_data(result.data):
        return None
    return result.failures[0].code


def _emit_delegate_failed(error_code: str) -> None:
    try:
        emit_progress(step="delegate", status="failed", error_code=error_code)
    except ProgressValidationError:
        emit_progress(
            step="delegate",
            status="failed",
            error_code="data_agent_failure",
        )


def build_delegate_data_tool(user_id: str) -> BaseTool:
    @tool
    def delegate_data_task(request: str) -> str:
        """委派只读数据任务。适用于 Provider 外部数据、跨源、跨表及需要 Python 计算的任务。"""
        emit_progress(step="delegate", status="started")
        error_code: str | None = None
        try:
            system_data_agent = default_config().get("data_agent")
            config: dict[str, Any] = (
                system_data_agent if isinstance(system_data_agent, dict) else {}
            )
            limits = DataAgentLimits.from_config(config)
        except Exception:
            error_code = "config_failure"
            result = _failure(error_code, "数据子 Agent 配置无效")
        else:
            request_id = str(uuid.uuid4())
            try:
                with DatasetWorkspace(limits) as workspace:
                    try:
                        sandbox_client = SandboxClient.from_env()
                    except Exception:
                        error_code = "sandbox_failure"
                        result = _failure(error_code, "计算服务配置不可用")
                    else:
                        try:
                            result = run_data_agent(
                                user_id,
                                request,
                                request_id,
                                workspace,
                                sandbox_client,
                            )
                        except Exception:
                            error_code = "data_agent_failure"
                            result = _failure(error_code, "数据子 Agent 执行失败")
            except Exception:
                error_code = "workspace_failure"
                result = _failure(error_code, "数据子 Agent 工作区不可用")

        result_error_code = error_code or _result_error_code(result)
        if result_error_code is None:
            emit_progress(step="delegate", status="completed")
        else:
            _emit_delegate_failed(result_error_code)
        return result.to_tool_json()

    return delegate_data_task
