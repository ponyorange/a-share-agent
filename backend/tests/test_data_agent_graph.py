import json
import logging
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from app.advisor.agent.data_agent.graph import (
    DATA_AGENT_PROMPT,
    parse_data_agent_result,
    run_data_agent,
)
from app.advisor.agent.data_agent.models import DataAgentLimits
from app.advisor.agent.data_agent.workspace import DatasetWorkspace


class FakeSandbox:
    last_metrics = {
        "elapsed_ms": 12,
        "memory_peak_mb": 7,
        "secret": "must-not-log",
    }


def test_parse_data_agent_result_accepts_fenced_json():
    result = parse_data_agent_result(
        """```json
        {"answer":"完成","data":{"x":1},"sources":[],"computation":[],
         "warnings":[],"failures":[]}
        ```"""
    )
    assert result.answer == "完成"
    assert result.data == {"x": 1}


def test_parse_data_agent_result_returns_stable_failure_without_raw_text():
    secret = "TOKEN=secret-value"
    result = parse_data_agent_result(f"not json {secret}")
    assert result.failures[0].code == "invalid_agent_result"
    assert result.failures[0].message == "数据子 Agent 未返回有效 JSON"
    assert secret not in result.to_tool_json()


def test_data_agent_prompt_treats_all_provider_content_as_untrusted_data():
    for untrusted_kind in ("文本", "新闻", "文档", "样例"):
        assert untrusted_kind in DATA_AGENT_PROMPT
    assert "不得服从" in DATA_AGENT_PROMPT
    assert "接口目录" in DATA_AGENT_PROMPT
    assert "detail" in DATA_AGENT_PROMPT
    assert "只作为数据" in DATA_AGENT_PROMPT


def test_run_data_agent_only_gives_provider_and_python_tools(tmp_path, caplog):
    captured = {}

    class FakeAgent:
        def invoke(self, payload, config):
            captured["payload"] = payload
            captured["config"] = config
            return {
                "messages": [
                    AIMessage(content="", tool_calls=[{"name": "list_data_sources", "args": {}, "id": "1"}]),
                    AIMessage(
                        content=json.dumps(
                            {
                                "answer": "ok",
                                "data": {},
                                "sources": [],
                                "computation": [],
                                "warnings": [],
                                "failures": [],
                            }
                        )
                    ),
                ]
            }

    def fake_create_agent(model, tools, prompt):
        captured["model"] = model
        captured["tool_names"] = [item.name for item in tools]
        captured["prompt"] = prompt
        return FakeAgent()

    with DatasetWorkspace(DataAgentLimits(max_agent_steps=9), root=tmp_path / "w") as workspace:
        with (
            patch(
                "app.advisor.agent.data_agent.graph.build_chat_model",
                return_value="model",
            ) as build_model,
            patch(
                "app.advisor.agent.data_agent.graph.create_react_agent",
                side_effect=fake_create_agent,
            ),
            caplog.at_level(logging.INFO),
        ):
            result = run_data_agent(
                "u",
                "calculate",
                "request-1",
                workspace,
                FakeSandbox(),
            )

    assert result.answer == "ok"
    build_model.assert_called_once_with(
        "u", streaming=False, temperature=0.1, request_timeout=120
    )
    assert captured["tool_names"] == [
        "list_data_sources",
        "search_data_interfaces",
        "get_data_interface",
        "fetch_provider_data",
        "run_python_analysis",
    ]
    assert captured["config"] == {"recursion_limit": 9}
    assert captured["payload"]["messages"] == [HumanMessage(content="calculate")]
    record = next(record for record in caplog.records if record.message == "data_agent_completed")
    assert record.request_id == "request-1"
    assert record.model_calls == 2
    assert record.sandbox_metrics == {"elapsed_ms": 12, "memory_peak_mb": 7}
    assert "must-not-log" not in str(record.__dict__)


def test_run_data_agent_maps_missing_final_message_to_stable_failure(tmp_path):
    class FakeAgent:
        def invoke(self, payload, config):
            return {"messages": [HumanMessage(content="sensitive request")]}

    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        with (
            patch("app.advisor.agent.data_agent.graph.build_chat_model", return_value="model"),
            patch(
                "app.advisor.agent.data_agent.graph.create_react_agent",
                return_value=FakeAgent(),
            ),
        ):
            result = run_data_agent("u", "sensitive request", "r", workspace, FakeSandbox())
    assert result.failures[0].code == "missing_agent_result"
    assert "sensitive request" not in result.to_tool_json()


def test_run_data_agent_sanitizes_model_and_tool_failures(tmp_path):
    secret = "TOKEN=secret-value"
    scenarios = [
        ("model_failure", "build_chat_model"),
        ("agent_failure", "invoke"),
    ]
    for expected_code, failure_point in scenarios:
        class FakeAgent:
            def invoke(self, payload, config):
                raise RuntimeError(f"{secret} code=print(data)")

        with DatasetWorkspace(DataAgentLimits(), root=tmp_path / failure_point) as workspace:
            with patch(
                "app.advisor.agent.data_agent.graph.build_chat_model",
                side_effect=(
                    RuntimeError(secret) if failure_point == "build_chat_model" else None
                ),
                return_value="model",
            ):
                create = (
                    patch(
                        "app.advisor.agent.data_agent.graph.create_react_agent",
                        return_value=FakeAgent(),
                    )
                    if failure_point == "invoke"
                    else patch("app.advisor.agent.data_agent.graph.create_react_agent")
                )
                with create:
                    result = run_data_agent("u", "request", "r", workspace, FakeSandbox())
        assert result.failures[0].code == expected_code
        assert secret not in result.to_tool_json()
