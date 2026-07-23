import pytest

from app.advisor import knowledge as kn


def test_validate_on_demand_requires_description():
    with pytest.raises(ValueError, match="description"):
        kn.validate_payload(
            {
                "title": "笔记",
                "mode": "on_demand",
                "enabled": True,
                "description": "  ",
                "body": "正文",
            },
            existing_enabled=[],
            exclude_id=None,
        )


def test_validate_always_body_budget():
    existing = [
        {
            "id": "a",
            "mode": "always",
            "enabled": True,
            "body": "x" * 5000,
        }
    ]
    with pytest.raises(ValueError, match="6000"):
        kn.validate_payload(
            {
                "title": "纪律",
                "mode": "always",
                "enabled": True,
                "description": "",
                "body": "y" * 1500,
            },
            existing_enabled=existing,
            exclude_id=None,
        )


def test_build_prompt_section_splits_modes():
    items = [
        {
            "id": "1",
            "title": "纪律",
            "mode": "always",
            "enabled": True,
            "description": "",
            "body": "不加杠杆",
        },
        {
            "id": "2",
            "title": "茅台笔记",
            "mode": "on_demand",
            "enabled": True,
            "description": "贵州茅台基本面",
            "body": "长文…",
        },
        {
            "id": "3",
            "title": "关闭",
            "mode": "always",
            "enabled": False,
            "description": "",
            "body": "不应出现",
        },
    ]
    text = kn.format_knowledge_prompt_section(items)
    assert "不加杠杆" in text
    assert "茅台笔记" in text
    assert "贵州茅台基本面" in text
    assert "长文" not in text
    assert "不应出现" not in text
    assert "load_knowledge" in text


def test_load_knowledge_tool_registered():
    from app.advisor.agent.tools import build_tools

    tools = {t.name: t for t in build_tools("u")}
    assert "load_knowledge" in tools
