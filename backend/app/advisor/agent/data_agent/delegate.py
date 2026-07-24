from __future__ import annotations

import uuid
from typing import Any

from langchain_core.tools import BaseTool, tool

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


def build_delegate_data_tool(user_id: str) -> BaseTool:
    @tool
    def delegate_data_task(request: str) -> str:
        """委派只读数据任务。适用于 Provider 外部数据、跨源、跨表及需要 Python 计算的任务。"""
        try:
            system_data_agent = default_config().get("data_agent")
            config: dict[str, Any] = (
                system_data_agent if isinstance(system_data_agent, dict) else {}
            )
            limits = DataAgentLimits.from_config(config)
        except Exception:
            return _failure("config_failure", "数据子 Agent 配置无效").to_tool_json()

        request_id = str(uuid.uuid4())
        try:
            with DatasetWorkspace(limits) as workspace:
                try:
                    sandbox_client = SandboxClient.from_env()
                except Exception:
                    result = _failure("sandbox_failure", "计算服务配置不可用")
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
                        result = _failure("data_agent_failure", "数据子 Agent 执行失败")
        except Exception:
            result = _failure("workspace_failure", "数据子 Agent 工作区不可用")
        return result.to_tool_json()

    return delegate_data_task
