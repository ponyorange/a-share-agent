import json
from unittest.mock import patch

from app.advisor.agent.data_agent.delegate import build_delegate_data_tool
from app.advisor.agent.data_agent.models import DataAgentResult
from app.advisor.agent.graph import SYSTEM_PROMPT
from app.advisor.agent.tools import build_tools


def _success_result() -> DataAgentResult:
    return DataAgentResult(
        answer="ok",
        data={},
        sources=[],
        computation=[],
        warnings=[],
        failures=[],
    )


def test_delegate_tool_is_read_only_uses_system_budget_and_cleans_workspace(tmp_path):
    root = tmp_path / "workspace"
    tool = build_delegate_data_tool("user-1")
    assert tool.name == "delegate_data_task"
    assert "Provider 外部数据" in tool.description
    assert "跨源" in tool.description
    assert "Python" in tool.description

    with (
        patch(
            "app.advisor.agent.data_agent.delegate.default_config",
            return_value={"data_agent": {"max_agent_steps": 7}},
        ) as defaults,
        patch(
            "app.advisor.agent.data_agent.workspace.tempfile.mkdtemp",
            return_value=str(root),
        ),
        patch(
            "app.advisor.agent.data_agent.delegate.SandboxClient.from_env",
            return_value=object(),
        ),
        patch(
            "app.advisor.agent.data_agent.delegate.run_data_agent",
            return_value=_success_result(),
        ) as run,
    ):
        payload = json.loads(tool.invoke({"request": "计算两源收益率差"}))

    assert payload["answer"] == "ok"
    defaults.assert_called_once_with()
    workspace = run.call_args.args[3]
    assert workspace.limits.max_agent_steps == 7
    assert not root.exists()


def test_delegate_creates_a_fresh_workspace_for_every_call(tmp_path):
    roots = [tmp_path / "first", tmp_path / "second"]
    seen = []

    def run(*args):
        seen.append(args[3])
        return _success_result()

    with (
        patch(
            "app.advisor.agent.data_agent.delegate.default_config",
            return_value={"data_agent": {}},
        ),
        patch(
            "app.advisor.agent.data_agent.workspace.tempfile.mkdtemp",
            side_effect=[str(path) for path in roots],
        ),
        patch(
            "app.advisor.agent.data_agent.delegate.SandboxClient.from_env",
            return_value=object(),
        ),
        patch(
            "app.advisor.agent.data_agent.delegate.run_data_agent",
            side_effect=run,
        ),
    ):
        tool = build_delegate_data_tool("u")
        tool.invoke({"request": "one"})
        tool.invoke({"request": "two"})

    assert seen[0] is not seen[1]
    assert all(not path.exists() for path in roots)


def test_delegate_sanitizes_failures_and_cleans_when_sandbox_setup_fails(tmp_path):
    root = tmp_path / "workspace"
    secret = "SANDBOX_TOKEN=secret-value"
    with (
        patch(
            "app.advisor.agent.data_agent.delegate.default_config",
            return_value={"data_agent": {}},
        ),
        patch(
            "app.advisor.agent.data_agent.workspace.tempfile.mkdtemp",
            return_value=str(root),
        ),
        patch(
            "app.advisor.agent.data_agent.delegate.SandboxClient.from_env",
            side_effect=RuntimeError(secret),
        ),
    ):
        payload = json.loads(
            build_delegate_data_tool("u").invoke({"request": "private request"})
        )

    assert payload["failures"][0]["code"] == "sandbox_failure"
    assert secret not in json.dumps(payload, ensure_ascii=False)
    assert "private request" not in json.dumps(payload, ensure_ascii=False)
    assert not root.exists()


def test_delegate_sanitizes_unexpected_agent_failure(tmp_path):
    secret = "raw model text and code"
    with (
        patch(
            "app.advisor.agent.data_agent.delegate.default_config",
            return_value={"data_agent": {}},
        ),
        patch(
            "app.advisor.agent.data_agent.delegate.SandboxClient.from_env",
            return_value=object(),
        ),
        patch(
            "app.advisor.agent.data_agent.delegate.run_data_agent",
            side_effect=RuntimeError(secret),
        ),
    ):
        payload = json.loads(build_delegate_data_tool("u").invoke({"request": "request"}))
    assert payload["failures"][0]["code"] == "data_agent_failure"
    assert secret not in json.dumps(payload, ensure_ascii=False)


def test_main_agent_registers_delegate_last_and_preserves_specialized_rules():
    names = [item.name for item in build_tools("u")]
    assert names[-1] == "delegate_data_task"
    assert "自动调用 delegate_data_task" in SYSTEM_PROMPT
    assert "持仓、模拟盘、策略和推荐归档仍使用现有专用工具" in SYSTEM_PROMPT
    assert "fetch_market_indices" in SYSTEM_PROMPT
    assert "fetch_symbol_daily_ma" in SYSTEM_PROMPT


def test_data_agent_package_has_formal_exports():
    from app.advisor.agent.data_agent import (
        DataAgentFailure,
        DataAgentLimits,
        DataAgentResult,
        DatasetWorkspace,
        build_delegate_data_tool,
        build_provider_tools,
        build_python_tool,
        parse_data_agent_result,
        run_data_agent,
    )

    assert all(
        value is not None
        for value in (
            DataAgentFailure,
            DataAgentLimits,
            DataAgentResult,
            DatasetWorkspace,
            build_delegate_data_tool,
            build_provider_tools,
            build_python_tool,
            parse_data_agent_result,
            run_data_agent,
        )
    )
