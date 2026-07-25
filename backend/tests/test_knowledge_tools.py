import json
from unittest.mock import patch

from app.advisor.agent.tools import build_tools


def _tool_map(user_id: str = "u1"):
    return {t.name: t for t in build_tools(user_id)}


def test_knowledge_write_tools_registered():
    tools = _tool_map()
    assert "list_knowledge" in tools
    assert "save_knowledge" in tools
    assert "delete_knowledge" in tools


def test_list_knowledge_filters_by_query():
    tools = _tool_map()
    items = [
        {
            "id": "1",
            "title": "交易纪律",
            "mode": "always",
            "enabled": True,
            "description": "",
            "body": "x",
            "updated_at": "2",
        },
        {
            "id": "2",
            "title": "茅台",
            "mode": "on_demand",
            "enabled": True,
            "description": "d",
            "body": "y",
            "updated_at": "1",
        },
    ]
    with patch("app.advisor.knowledge.list_raw", return_value=items):
        raw = tools["list_knowledge"].invoke({"query": "纪律"})
    data = json.loads(raw)
    assert data["ok"] is True
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "1"
    assert "body" not in data["items"][0]


def test_save_knowledge_preview_does_not_persist():
    tools = _tool_map()
    with (
        patch("app.advisor.knowledge.list_raw", return_value=[]),
        patch("app.advisor.knowledge.create_item") as create,
    ):
        raw = tools["save_knowledge"].invoke(
            {
                "title": "纪律",
                "mode": "always",
                "body": "不加杠杆",
                "description": "",
                "confirm": False,
            }
        )
    data = json.loads(raw)
    assert data["needs_confirm"] is True
    assert data["ok"] is False
    create.assert_not_called()


def test_save_knowledge_confirm_creates():
    tools = _tool_map()
    fake = {
        "id": "k1",
        "title": "纪律",
        "mode": "always",
        "enabled": True,
        "description": "",
        "body": "不加杠杆",
    }
    with (
        patch("app.advisor.knowledge.list_raw", return_value=[]),
        patch("app.advisor.knowledge.create_item", return_value=fake) as create,
    ):
        raw = tools["save_knowledge"].invoke(
            {
                "title": "纪律",
                "mode": "always",
                "body": "不加杠杆",
                "description": "",
                "confirm": True,
            }
        )
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["item"]["id"] == "k1"
    create.assert_called_once()


def test_delete_knowledge_title_ambiguous():
    tools = _tool_map()
    items = [
        {
            "id": "a",
            "title": "纪律A",
            "mode": "always",
            "enabled": True,
            "description": "",
            "updated_at": "2",
        },
        {
            "id": "b",
            "title": "纪律B",
            "mode": "on_demand",
            "enabled": True,
            "description": "d",
            "updated_at": "1",
        },
    ]
    with patch("app.advisor.knowledge.list_raw", return_value=items):
        raw = tools["delete_knowledge"].invoke({"title": "纪律", "confirm": False})
    data = json.loads(raw)
    assert data["ok"] is False
    assert len(data["candidates"]) == 2


def test_system_prompt_mentions_knowledge_write_confirm():
    from app.advisor.agent.graph import SYSTEM_PROMPT

    assert "save_knowledge" in SYSTEM_PROMPT
    assert "confirm=true" in SYSTEM_PROMPT
    assert "list_knowledge" in SYSTEM_PROMPT
