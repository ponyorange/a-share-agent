from copy import deepcopy

import pytest

from app.advisor import ui_settings as ui


class FakeCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    def find_one(self, query, projection=None):
        doc = self.docs.get(query["user_id"])
        return deepcopy(doc) if doc else None

    def update_one(self, query, update, upsert=False):
        uid = query["user_id"]
        current = self.docs.get(uid, {})
        if uid not in self.docs:
            current.update(deepcopy(update.get("$setOnInsert", {})))
        current.update(deepcopy(update["$set"]))
        self.docs[uid] = current


class FakeDb:
    def __init__(self):
        self.user_ui_settings = FakeCollection()


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(ui, "get_db", lambda: db)
    return db


def test_missing_settings_returns_modern_data_without_writing(fake_db):
    result = ui.get_ui_settings("u1")
    assert result["active_template"] == "modern_data"
    assert result["colors"] == ui.DEFAULT_THEMES["modern_data"]
    assert fake_db.user_ui_settings.docs == {}


def test_save_normalizes_hex_and_isolates_users(fake_db):
    first = dict(ui.DEFAULT_THEMES["modern_data"], brand="#abcdef")
    second = dict(ui.DEFAULT_THEMES["classic_market"], brand="#123456")
    saved = ui.save_ui_settings("u1", active_template="modern_data", colors=first)
    ui.save_ui_settings("u2", active_template="classic_market", colors=second)
    assert saved["colors"]["brand"] == "#ABCDEF"
    assert ui.get_ui_settings("u1")["colors"]["brand"] == "#ABCDEF"
    assert ui.get_ui_settings("u2")["colors"]["brand"] == "#123456"


def test_save_accepts_deep_navy(fake_db):
    colors = {
        "page_bg": "#101724",
        "surface": "#192335",
        "text_primary": "#F2F5FA",
        "text_muted": "#99A7BB",
        "border": "#303E55",
        "brand": "#8793FF",
        "market_up": "#70A9F8",
        "market_down": "#F1B85B",
        "success": "#61C28F",
        "error": "#F17C8E",
    }
    saved = ui.save_ui_settings("u1", active_template="deep_navy", colors=colors)
    assert saved["active_template"] == "deep_navy"
    assert saved["colors"]["page_bg"] == "#101724"
    assert ui.get_ui_settings("u1")["active_template"] == "deep_navy"


@pytest.mark.parametrize(
    "colors",
    [
        {},
        dict(ui.DEFAULT_THEMES["modern_data"], brand="red"),
        dict(ui.DEFAULT_THEMES["modern_data"], extra="#FFFFFF"),
    ],
)
def test_save_rejects_incomplete_invalid_or_extra_colors(fake_db, colors):
    with pytest.raises(ValueError):
        ui.save_ui_settings("u1", active_template="modern_data", colors=colors)


def test_save_rejects_unknown_template(fake_db):
    with pytest.raises(ValueError, match="模板"):
        ui.save_ui_settings(
            "u1",
            active_template="dark",
            colors=ui.DEFAULT_THEMES["modern_data"],
        )
