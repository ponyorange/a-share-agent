import json

from app.advisor.agent.data_agent.models import (
    DataAgentFailure,
    DataAgentLimits,
    DataAgentResult,
)


def test_data_agent_limits_match_approved_defaults():
    limits = DataAgentLimits.from_config({})
    assert limits.max_rows_per_fetch == 5_000
    assert limits.max_total_rows == 50_000
    assert limits.max_input_bytes == 50 * 1024 * 1024
    assert limits.sandbox_timeout_seconds == 30
    assert limits.sandbox_memory_mb == 512
    assert limits.max_output_bytes == 1024 * 1024
    assert limits.max_python_retries == 2


def test_tool_json_keeps_provenance_and_failures():
    result = DataAgentResult(
        answer="两源收益率差为 0.3 个百分点",
        data={"difference_pct_points": 0.3},
        sources=[{"source": "akshare", "interface": "stock_zh_a_hist"}],
        computation=["按日期内连接", "计算区间收益率"],
        warnings=["Tushare 返回复权口径不同"],
        failures=[DataAgentFailure(code="source_unavailable", source="baostock", message="down")],
    )
    payload = json.loads(result.to_tool_json())
    assert payload["data"]["difference_pct_points"] == 0.3
    assert payload["failures"][0]["code"] == "source_unavailable"
