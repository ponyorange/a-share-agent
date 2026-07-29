import json
import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.advisor.agent.progress import bind_progress_sink
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
    assert result.failures[0].code == "invalid_agent_schema"


def test_parse_data_agent_result_rejects_more_than_one_mibibyte():
    result = parse_data_agent_result(_result_json(answer="x" * (1024 * 1024)))
    assert result.failures[0].code in {"invalid_agent_result", "invalid_agent_schema"}


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

    assert result.failures[0].code == "source_not_in_request_evidence"
    assert result.data == {}
    assert result.sources == []


def test_parse_data_agent_result_rejects_success_data_without_request_evidence(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        result = parse_data_agent_result(_result_json(sources=[]), workspace=workspace)

    assert result.failures[0].code == "data_not_in_request_evidence"
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

    assert result.failures[0].code == "data_not_in_request_evidence"
    assert result.data == {}


def test_parse_data_agent_result_rejects_forged_data_with_valid_source(tmp_path):
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
            _result_json(),
            workspace=workspace,
        )

    assert result.failures[0].code == "data_not_in_request_evidence"
    assert result.data == {}


def test_parse_data_agent_result_rejects_forged_data_after_any_sandbox_success(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        workspace.record_sandbox_result({"mean": 2.0})
        result = parse_data_agent_result(
            _result_json(data={"mean": 999.0}, sources=[]),
            workspace=workspace,
        )

    # 改写后的 data 会被回绑到最近一次沙箱成功结果，避免 submit 死循环
    assert result.failures == []
    assert result.data["payload"] == {"mean": 2.0}
    assert "result_id" in result.data


def test_parse_data_agent_result_accepts_result_id_only(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        evidence = workspace.record_sandbox_result({"price": 871.0, "symbol": "Au99.99"})
        result = parse_data_agent_result(
            _result_json(data={"result_id": evidence.result_id}, sources=[]),
            workspace=workspace,
        )

    assert result.failures == []
    assert result.data == {
        "result_id": evidence.result_id,
        "payload": {"price": 871.0, "symbol": "Au99.99"},
    }


def test_data_agent_prompt_mentions_sge_gold_symbol():
    from app.advisor.agent.data_agent.graph import DATA_AGENT_PROMPT

    assert "spot_quotations_sge" in DATA_AGENT_PROMPT
    assert "Au99.99" in DATA_AGENT_PROMPT


def test_parse_data_agent_result_accepts_exact_sandbox_data_and_result_reference(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        evidence = workspace.record_sandbox_result({"rows": [{"value": 2.0}], "count": 1})
        exact = parse_data_agent_result(
            _result_json(data={"count": 1, "rows": [{"value": 2.0}]}, sources=[]),
            workspace=workspace,
        )
        referenced = parse_data_agent_result(
            _result_json(
                data={
                    "result_id": evidence.result_id,
                    "payload": {"count": 1, "rows": [{"value": 2.0}]},
                },
                sources=[],
            ),
            workspace=workspace,
        )

    assert exact.data == {"count": 1, "rows": [{"value": 2.0}]}
    assert referenced.data == {
        "result_id": evidence.result_id,
        "payload": {"count": 1, "rows": [{"value": 2.0}]},
    }


def test_parse_data_agent_result_allows_real_sandbox_data_with_partial_failures(tmp_path):
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
        workspace.record_sandbox_result({"close": 10.5, "pe_ttm": 12.3})
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


def test_data_agent_prompt_documents_sandbox_datasets_contract():
    assert "datasets[" in DATA_AGENT_PROMPT
    assert "result =" in DATA_AGENT_PROMPT or "赋值给 result" in DATA_AGENT_PROMPT
    assert "read_csv" in DATA_AGENT_PROMPT or "csv" in DATA_AGENT_PROMPT.lower()
    assert "stock_zh_index" in DATA_AGENT_PROMPT or "index_hist" in DATA_AGENT_PROMPT
    assert "submit_data_result" in DATA_AGENT_PROMPT


def test_data_agent_prompt_prefers_stable_etf_industry_ipo_interfaces():
    assert "fund_etf" in DATA_AGENT_PROMPT
    assert "fund_etf_hist_sina" in DATA_AGENT_PROMPT
    assert "industry_spot" in DATA_AGENT_PROMPT
    assert "stock_board_industry_spot_em" in DATA_AGENT_PROMPT
    assert "stock_board_industry_hist_em" in DATA_AGENT_PROMPT
    assert "第一选择" in DATA_AGENT_PROMPT or "不要把" in DATA_AGENT_PROMPT
    assert "ipo_declare" in DATA_AGENT_PROMPT or "stock_ipo_declare_em" in DATA_AGENT_PROMPT
    assert "provider_error" in DATA_AGENT_PROMPT


def test_submit_data_result_tool_accepts_valid_payload(tmp_path):
    from app.advisor.agent.data_agent.graph import build_submit_tool

    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        meta = workspace.create_dataset(
            "akshare",
            "demo",
            {"symbol": "sh000300"},
            {
                "columns": ["close"],
                "rows": [{"close": 1}],
                "returned": 1,
                "total": 1,
                "truncated": False,
            },
        )
        evidence = workspace.record_sandbox_result({"latest_close": 10.5})
        tool = build_submit_tool(workspace)
        payload = json.loads(
            tool.invoke(
                {
                    "answer": "ok",
                    "data_json": json.dumps(
                        {
                            "result_id": evidence.result_id,
                            "payload": {"latest_close": 10.5},
                        }
                    ),
                    "sources_json": json.dumps(
                        [
                            {
                                "source": "akshare",
                                "interface": "demo",
                                "params_summary": {"symbol": "sh000300"},
                                "rows": meta.returned,
                                "truncated": False,
                            }
                        ]
                    ),
                }
            )
        )

    assert payload == {"ok": True}
    assert workspace.submitted_result is not None
    assert workspace.submitted_result.data == {
        "result_id": evidence.result_id,
        "payload": {"latest_close": 10.5},
    }


def test_submit_data_result_tool_emits_started_and_completed_progress(tmp_path):
    from app.advisor.agent.data_agent.graph import build_submit_tool

    events = []
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        evidence = workspace.record_sandbox_result({"latest_close": 10.5})
        tool = build_submit_tool(workspace)
        with bind_progress_sink(events.append):
            payload = json.loads(
                tool.invoke(
                    {
                        "answer": "ok",
                        "data_json": json.dumps(
                            {
                                "result_id": evidence.result_id,
                                "payload": {"latest_close": 10.5},
                            }
                        ),
                        "sources_json": "[]",
                    }
                )
            )

    assert payload == {"ok": True}
    assert [(event["step"], event["status"]) for event in events] == [
        ("submit", "started"),
        ("submit", "completed"),
    ]


def test_submit_data_result_tool_failure_progress_uses_stable_error_code(tmp_path):
    from app.advisor.agent.data_agent.graph import build_submit_tool

    events = []
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        tool = build_submit_tool(workspace)
        with bind_progress_sink(events.append):
            payload = json.loads(
                tool.invoke(
                    {
                        "answer": "raw answer should not leak",
                        "data_json": "{not-json",
                        "sources_json": "[]",
                    }
                )
            )

    assert payload["error"] == "invalid_json_fields"
    assert events == [
        {
            "step": "submit",
            "status": "started",
            "phase": "data_agent",
            "message": "正在校验来源与结果",
        },
        {
            "step": "submit",
            "status": "failed",
            "error_code": "invalid_json_fields",
            "phase": "data_agent",
            "message": "结果校验失败",
        },
    ]
    assert "raw answer should not leak" not in json.dumps(events, ensure_ascii=False)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_submit_data_result_tool_rejects_non_finite_json_with_failed_progress(
    tmp_path, constant
):
    from app.advisor.agent.data_agent.graph import build_submit_tool

    events = []
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        tool = build_submit_tool(workspace)
        with bind_progress_sink(events.append):
            payload = json.loads(
                tool.invoke(
                    {
                        "answer": f"raw answer {constant} should not leak",
                        "data_json": constant,
                        "sources_json": "[]",
                    }
                )
            )

    assert payload == {
        "ok": False,
        "error": "invalid_json_fields",
        "message": "提交字段不是合法 JSON",
    }
    assert [(event["step"], event["status"]) for event in events] == [
        ("submit", "started"),
        ("submit", "failed"),
    ]
    assert events[-1]["error_code"] == "invalid_json_fields"
    encoded = json.dumps(events, ensure_ascii=False)
    assert constant not in encoded
    assert "raw answer" not in encoded


def test_parse_data_agent_result_maps_step_limit_sentinel():
    result = parse_data_agent_result("Sorry, need more steps to process this request.")
    assert result.failures[0].code == "agent_step_limit"
    assert result.data == {}


def test_parse_data_agent_result_accepts_fenced_json_with_trailing_prose():
    payload = {
        "answer": "ok",
        "data": {},
        "sources": [],
        "computation": [],
        "warnings": [],
        "failures": [{"code": "no_data", "message": "未取得数据"}],
    }
    raw = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n\n仅供参考"
    result = parse_data_agent_result(raw)
    assert result.answer == "ok"
    assert result.failures[0].code == "no_data"


def test_parse_normalizes_source_extra_fields_and_computation_object_notes(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        meta = workspace.create_dataset(
            "akshare",
            "stock_zh_index_daily_tx",
            {"symbol": "sh000300"},
            {
                "columns": ["close"],
                "rows": [{"close": i} for i in range(20)],
                "returned": 20,
                "total": 20,
                "truncated": False,
                "data_time": "2026-07-15",
            },
        )
        evidence = workspace.record_sandbox_result(
            {"latest_close": 4019.06, "period_high": 4019.06}
        )
        result = parse_data_agent_result(
            json.dumps(
                {
                    "answer": "ok",
                    "data": {
                        "result_id": evidence.result_id,
                        "payload": {"latest_close": 4019.06, "period_high": 4019.06},
                    },
                    "sources": [
                        {
                            "source": "akshare",
                            "interface": "stock_zh_index_daily_tx",
                            "params_summary": {"symbol": "sh000300"},
                            "data_time": "2026-07-15",
                            "rows": "20",
                            "truncated": "false",
                            "dataset_id": meta.dataset_id,
                            "columns": ["close"],
                            "sample": [{"close": 1}],
                        }
                    ],
                    "computation": [
                        {"step": "取近20日收盘"},
                        "pandas describe",
                    ],
                    "warnings": [{"note": "仅供参考"}],
                    "failures": [],
                },
                ensure_ascii=False,
            ),
            workspace=workspace,
        )

    assert result.failures == []
    assert result.sources[0].rows == 20
    assert result.sources[0].truncated is False
    assert result.computation == ["取近20日收盘", "pandas describe"]
    assert result.warnings == ["仅供参考"]


def test_submit_data_result_tool_returns_field_hints_on_schema_error(tmp_path):
    from app.advisor.agent.data_agent.graph import build_submit_tool

    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        tool = build_submit_tool(workspace)
        payload = json.loads(
            tool.invoke(
                {
                    "answer": "ok",
                    "data_json": "{}",
                    "sources_json": '[{"source":"","interface":"demo"}]',
                    "computation_json": "[]",
                    "warnings_json": "[]",
                    "failures_json": "[]",
                }
            )
        )

    assert payload["ok"] is False
    assert payload["error"] == "invalid_agent_schema"
    assert "sources" in payload["message"]
    assert "string_too_short" in payload["message"] or "too_short" in payload["message"]

def test_parse_normalizes_params_summary_string_and_computation_result_reference(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        meta = workspace.create_dataset(
            "akshare",
            "stock_zh_index_daily_tx",
            {
                "symbol": "sh000300",
                "start_date": "20250501",
                "end_date": "20250718",
            },
            {
                "columns": ["close"],
                "rows": [{"close": 1}],
                "returned": 53,
                "total": 53,
                "truncated": False,
            },
        )
        evidence = workspace.record_sandbox_result(
            {"latest_close": 4058.55, "period_high": 4065.94}
        )
        raw = json.dumps(
            {
                "answer": "ok",
                "data": {"latest_close": 1},
                "sources": [
                    {
                        "source": "akshare",
                        "interface": "stock_zh_index_daily_tx",
                        "params_summary": (
                            "symbol=sh000300, start_date=20250501, end_date=20250718"
                        ),
                        "rows": meta.returned,
                        "truncated": False,
                    }
                ],
                "computation": {
                    "result_id": evidence.result_id,
                    "method": "取近20日高低收",
                },
                "warnings": [],
                "failures": [],
            },
            ensure_ascii=False,
        )
        result = parse_data_agent_result(raw, workspace=workspace)

    assert result.data == {
        "result_id": evidence.result_id,
        "payload": {"latest_close": 4058.55, "period_high": 4065.94},
    }
    assert result.sources[0].params_summary == {
        "symbol": "sh000300",
        "start_date": "20250501",
        "end_date": "20250718",
    }
    assert result.computation == ["取近20日高低收"]


def test_parse_data_agent_result_maps_evidence_failures_explicitly(tmp_path):
    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        workspace.create_dataset(
            "akshare",
            "demo",
            {"symbol": "000001"},
            {
                "columns": ["close"],
                "rows": [{"close": 1}],
                "returned": 1,
                "total": 1,
                "truncated": False,
            },
        )
        forged = parse_data_agent_result(
            _result_json(
                sources=[
                    {
                        "source": "akshare",
                        "interface": "demo",
                        "params_summary": {"symbol": "forged"},
                        "rows": 1,
                    }
                ]
            ),
            workspace=workspace,
        )
    assert forged.failures[0].code == "source_not_in_request_evidence"


def test_run_data_agent_maps_step_limit_final_message(tmp_path):
    class FakeAgent:
        def invoke(self, payload, config):
            return {
                "messages": [
                    AIMessage(content="Sorry, need more steps to process this request.")
                ]
            }

    with DatasetWorkspace(DataAgentLimits(), root=tmp_path / "w") as workspace:
        with (
            patch("app.advisor.agent.data_agent.graph.build_chat_model", return_value="model"),
            patch(
                "app.advisor.agent.data_agent.graph.create_react_agent",
                return_value=FakeAgent(),
            ),
        ):
            result = run_data_agent("u", "request", "r", workspace, FakeSandbox())

    assert result.failures[0].code == "agent_step_limit"
    assert result.failures[0].message == "数据子 Agent 步数已达上限"


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
                                "failures": [
                                    {"code": "no_data", "message": "未取得数据"}
                                ],
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
        "submit_data_result",
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
