import json

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_rows_per_fetch": 5_001},
        {"max_total_rows": 50_001},
        {"max_input_bytes": 50 * 1024 * 1024 + 1},
        {"sandbox_timeout_seconds": 31},
        {"sandbox_memory_mb": 513},
        {"max_output_bytes": 1024 * 1024 + 1},
        {"max_python_retries": 3},
    ],
    ids=[
        "max_rows_per_fetch",
        "max_total_rows",
        "max_input_bytes",
        "sandbox_timeout_seconds",
        "sandbox_memory_mb",
        "max_output_bytes",
        "max_python_retries",
    ],
)
def test_data_agent_limits_reject_overrides_above_hard_caps(overrides):
    with pytest.raises(ValidationError):
        DataAgentLimits.from_config(overrides)


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
