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


def test_format_sections_split_always_and_catalog():
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
    always = kn.format_always_knowledge_section(items)
    catalog = kn.format_on_demand_catalog_section(items)
    system = kn.format_knowledge_prompt_section(items)

    assert "不加杠杆" in always
    assert "用户必选知识" in always
    assert "茅台笔记" not in always

    assert "茅台笔记" in catalog
    assert "贵州茅台基本面" in catalog
    assert "长文" not in catalog
    assert "不加杠杆" not in catalog
    assert "load_knowledge" in catalog

    assert system == catalog
    assert "不应出现" not in always and "不应出现" not in catalog


def test_match_by_title_substring_case_insensitive():
    items = [
        {"id": "1", "title": "交易纪律", "updated_at": "2026-01-02"},
        {"id": "2", "title": "茅台笔记", "updated_at": "2026-01-03"},
        {"id": "3", "title": "纪律补充", "updated_at": "2026-01-04"},
    ]
    hits = kn.match_by_title(items, "纪律")
    assert [x["id"] for x in hits] == ["3", "1"]


def test_match_by_title_empty_query_returns_empty():
    assert kn.match_by_title([{"id": "1", "title": "A"}], "  ") == []


def test_summarize_item_omits_body_by_default():
    doc = {
        "id": "1",
        "title": "t",
        "mode": "always",
        "enabled": True,
        "description": "d",
        "body": "SECRET",
    }
    out = kn.summarize_item(doc)
    assert out["id"] == "1"
    assert "body" not in out


def test_load_knowledge_tool_registered():
    from app.advisor.agent.tools import build_tools

    tools = {t.name: t for t in build_tools("u")}
    assert "load_knowledge" in tools
