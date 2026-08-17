from unittest.mock import MagicMock, patch

import pytest

from app.advisor import agent_config as ac


def test_validate_rejects_over_limit():
    with pytest.raises(ValueError, match="6000"):
        ac.validate_system_prompt("x" * 6001)


def test_validate_accepts_empty_and_limit():
    assert ac.validate_system_prompt("") == ""
    assert ac.validate_system_prompt("  hello  ") == "hello"
    assert len(ac.validate_system_prompt("y" * 6000)) == 6000


def test_get_system_prompt_missing_returns_empty():
    col = MagicMock()
    col.find_one.return_value = None
    with patch.object(ac, "_col", return_value=col):
        assert ac.get_system_prompt("u1") == ""


def test_save_system_prompt_upserts():
    col = MagicMock()
    col.find_one.return_value = {
        "user_id": "u1",
        "system_prompt": "自称小顾",
        "updated_at": None,
    }
    with patch.object(ac, "_col", return_value=col):
        out = ac.save_system_prompt("u1", "自称小顾")
    col.update_one.assert_called_once()
    assert out["system_prompt"] == "自称小顾"


def test_build_system_prompt_appends_user_and_catalog():
    from app.advisor.agent import graph as agent_graph

    with (
        patch(
            "app.advisor.agent_config.get_system_prompt",
            return_value="请自称小顾。",
        ),
        patch(
            "app.advisor.knowledge.build_knowledge_prompt_section",
            return_value="## 用户可选知识目录\n- id: x",
        ),
    ):
        text = agent_graph.build_system_prompt("u1")

    assert text.startswith(agent_graph.SYSTEM_PROMPT.rstrip()[:20])
    assert "请自称小顾。" in text
    assert "优先级高于上文默认自称" in text
    assert "必须以用户设定为准" in agent_graph.SYSTEM_PROMPT
    assert "用户可选知识目录" in text
    assert "必选知识已在系统提示中" not in agent_graph.SYSTEM_PROMPT
    assert "消息上下文" in agent_graph.SYSTEM_PROMPT
    assert "## 当前时间" in text
    assert "Asia/Shanghai" in text
    assert "## 运行配置" in text
    assert "主对话模型" in text


def test_runtime_config_section_includes_model():
    from app.advisor.agent import graph as agent_graph

    with patch(
        "app.advisor.llm_settings.public_llm_settings",
        return_value={
            "configured": True,
            "slots": {
                "agent": {"provider": "deepseek", "model": "deepseek-v4-pro"},
            },
        },
    ):
        text = agent_graph._runtime_config_section("u1")
    assert "deepseek-v4-pro" in text
    assert "已配置" in text
    assert "deepseek" in text


def test_current_time_section_uses_shanghai():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.advisor.agent import graph as agent_graph

    fixed = datetime(2026, 7, 26, 14, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    text = agent_graph._current_time_section(now=fixed)
    assert "2026-07-26 14:30:00" in text
    assert "星期日" in text
    assert "Asia/Shanghai" in text
