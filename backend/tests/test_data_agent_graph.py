import json
import logging
from unittest.mock import patch

import pytest
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


def _result_json(**overrides):
    payload = {
        "answer": "完成",
        "data": {"close": 10.5, "pe_ttm": 12.3},
        "sources": [
            {
                "source": "akshare",
                "interface": "stock_zh_a_hist",
                "params_summary": {"symbol": "600519", "period": "daily"},
                "data_time": "2026-07-24",
            }
        ],
        "computation": ["按交易日对齐"],
        "warnings": [],
        "failures": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_parse_data_agent_result_accepts_fenced_json():
    result = parse_data_agent_result(
        """```json
        {"answer":"完成","data":{"x":1},"sources":[],"computation":[],
         "warnings":[],"failures":[]}
        ```"""
    )
    assert result.answer == "完成"
    assert result.data == {"x": 1}


def test_parse_data_agent_result_accepts_normal_market_and_financial_data():
    result = parse_data_agent_result(_result_json())
    assert result.data == {"close": 10.5, "pe_ttm": 12.3}
    assert result.sources[0].params_summary == {
        "symbol": "600519",
        "period": "daily",
    }


def test_parse_data_agent_result_rejects_extra_fields():
    result = parse_data_agent_result(_result_json(debug_dump={"raw": "not allowed"}))
    assert result.failures[0].code == "invalid_agent_result"


def test_parse_data_agent_result_rejects_more_than_one_mibibyte():
    result = parse_data_agent_result(_result_json(answer="x" * (1024 * 1024)))
    assert result.failures[0].code == "invalid_agent_result"


def test_parse_data_agent_result_rejects_depth_21():
    nested = "leaf"
    for _ in range(21):
        nested = {"level": nested}
    result = parse_data_agent_result(_result_json(data=nested))
    assert result.failures[0].code == "invalid_agent_result"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_parse_data_agent_result_rejects_non_finite_numbers(constant):
    raw = _result_json().replace("10.5", constant, 1)
    result = parse_data_agent_result(raw)
    assert result.failures[0].code == "invalid_agent_result"


@pytest.mark.parametrize(
    "sensitive_key",
    ["api_key", "token", "authorization", "password", "secret", "credential"],
)
def test_parse_data_agent_result_rejects_sensitive_keys(sensitive_key):
    result = parse_data_agent_result(
        _result_json(data={"quote": 1, "nested": {sensitive_key: "do-not-return"}})
    )
    assert result.failures[0].code == "invalid_agent_result"
    assert "do-not-return" not in result.to_tool_json()


def test_parse_data_agent_result_filters_sensitive_params_summary_recursively():
    result = parse_data_agent_result(
        _result_json(
            sources=[
                {
                    "source": "tushare",
                    "interface": "daily",
                    "params_summary": {
                        "ts_code": "600519.SH",
                        "nested": {"token": "secret-value", "period": "D"},
                    },
                }
            ]
        )
    )
    assert result.failures == []
    assert result.sources[0].params_summary == {
        "ts_code": "600519.SH",
        "nested": {"period": "D"},
    }
    assert "secret-value" not in result.to_tool_json()


def test_parse_data_agent_result_returns_stable_failure_without_raw_text():
    secret = "TOKEN=secret-value"
    result = parse_data_agent_result(f"not json {secret}")
    assert result.failures[0].code == "invalid_agent_result"
    assert result.failures[0].message == "数据子 Agent 未返回有效 JSON"
    assert secret not in result.to_tool_json()


@pytest.mark.parametrize(
    "source_override",
    [
        {"source": "tushare"},
        {"interface": "daily"},
        {"params_summary": {"symbol": "000001", "period": "daily"}},
        {"data_time": "2026-07-23"},
    ],
)
def test_parse_data_agent_result_rejects_sources_not_created_in_workspace(
    tmp_path, source_override
):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        workspace.create_dataset(
            "akshare",
            "stock_zh_a_hist",
            {"symbol": "600519", "period": "daily"},
            {
                "columns": ["close"],
                "rows": [{"close": 10.5}],
                "returned": 1,
                "total": 1,
                "truncated": False,
                "data_time": "2026-07-24",
            },
        )
        claimed_source = {
            "source": "akshare",
            "interface": "stock_zh_a_hist",
            "params_summary": {"symbol": "600519", "period": "daily"},
            "data_time": "2026-07-24",
        }
        claimed_source.update(source_override)
        result = parse_data_agent_result(
            _result_json(sources=[claimed_source]),
            workspace=workspace,
        )

    assert result.failures[0].code == "invalid_agent_result"
    assert result.data == {}
    assert result.sources == []


def test_parse_data_agent_result_rejects_success_data_without_request_evidence(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        result = parse_data_agent_result(_result_json(sources=[]), workspace=workspace)

    assert result.failures[0].code == "incomplete_agent_result"
    assert result.data == {}


def test_parse_data_agent_result_requires_source_for_direct_provider_data(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        workspace.create_dataset(
            "akshare",
            "stock_zh_a_hist",
            {"symbol": "600519"},
            {
                "columns": ["close"],
                "rows": [{"close": 10.5}],
                "returned": 1,
                "total": 1,
                "truncated": False,
            },
        )
        result = parse_data_agent_result(_result_json(sources=[]), workspace=workspace)

    assert result.failures[0].code == "incomplete_agent_result"
    assert result.data == {}


def test_parse_data_agent_result_allows_partial_real_data_with_failures(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        workspace.create_dataset(
            "akshare",
            "stock_zh_a_hist",
            {"symbol": "600519", "period": "daily", "token": "secret"},
            {
                "columns": ["close"],
                "rows": [{"close": 10.5}],
                "returned": 1,
                "total": 1,
                "truncated": False,
                "data_time": "2026-07-24",
            },
        )
        result = parse_data_agent_result(
            _result_json(
                warnings=["另一数据源不可用"],
                failures=[
                    {
                        "code": "source_unavailable",
                        "message": "数据源暂不可用",
                        "source": "tushare",
                        "interface": "daily",
                    }
                ],
            ),
            workspace=workspace,
        )

    assert result.data == {"close": 10.5, "pe_ttm": 12.3}
    assert result.sources[0].params_summary == {
        "symbol": "600519",
        "period": "daily",
    }
    assert result.failures[0].code == "source_unavailable"
    assert result.warnings == ["另一数据源不可用"]


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
