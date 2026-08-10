"""Tests for limit-up promote daily archive and T+1 accuracy."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.advisor import limitup_promote_store as store


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}

    def create_index(self, *_a, **_k):
        return "ok"

    def find_one(self, query, *_a, **_k):
        key = (query.get("user_id"), query.get("trade_date"))
        doc = self.docs.get(key)
        return dict(doc) if doc else None

    def update_one(self, query, update, upsert=False):
        key = (query.get("user_id"), query.get("trade_date"))
        payload = dict(update.get("$set") or {})
        if key in self.docs:
            self.docs[key].update(payload)
        elif upsert:
            self.docs[key] = payload
        return MagicMock()

    def find(self, query, *_a, **_k):
        uid = query.get("user_id")
        rows = [
            dict(v)
            for (u, _d), v in self.docs.items()
            if u == uid and (query.get("status") is None or v.get("status") == query.get("status"))
        ]
        rows.sort(key=lambda r: r.get("trade_date") or "", reverse=True)

        class _Cursor:
            def __init__(self, data):
                self._data = data

            def sort(self, *_a, **_k):
                return self

            def limit(self, n):
                self._data = self._data[:n]
                return self

            def __iter__(self):
                return iter(self._data)

        return _Cursor(rows)


@pytest.fixture()
def fake_col(monkeypatch):
    col = _FakeCollection()
    monkeypatch.setattr(store, "_col", lambda: col)
    monkeypatch.setattr(store, "ensure_indexes", lambda: None)
    return col


def test_upsert_and_get_daily_overwrite(fake_col):
    store.upsert_daily(
        "u1",
        "2026-08-07",
        {
            "status": "ready",
            "summary": "v1",
            "picks": [{"symbol": "000001", "name": "平安", "board_count": 2, "score": 4, "reason": "a"}],
            "candidate_count": 10,
        },
    )
    store.upsert_daily(
        "u1",
        "2026-08-07",
        {
            "status": "ready",
            "summary": "v2",
            "picks": [{"symbol": "600000", "name": "浦发", "board_count": 1, "score": 3, "reason": "b"}],
            "candidate_count": 12,
        },
    )
    doc = store.get_daily("u1", "2026-08-07")
    assert doc["status"] == "ready"
    assert doc["summary"] == "v2"
    assert [p["symbol"] for p in doc["picks"]] == ["600000"]


def test_compute_accuracy_marks_broken(fake_col, monkeypatch):
    store.upsert_daily(
        "u1",
        "2026-08-06",
        {
            "status": "ready",
            "summary": "s",
            "picks": [
                {"symbol": "000001", "name": "A", "board_count": 2, "score": 5, "reason": "x"},
                {"symbol": "000002", "name": "B", "board_count": 1, "score": 4, "reason": "y"},
                {"symbol": "000003", "name": "C", "board_count": 1, "score": 3, "reason": "z"},
            ],
            "candidate_count": 3,
        },
    )
    monkeypatch.setattr(
        store,
        "next_trading_day",
        lambda d: "2026-08-07",
    )
    monkeypatch.setattr(store, "parse_date", lambda s: date.fromisoformat(str(s)[:10]))
    monkeypatch.setattr(
        store,
        "get_limit_up_status_map_for_date",
        lambda _d: {"000001": "sealed", "000002": "broken"},
    )

    acc = store.compute_accuracy("u1", "2026-08-06", persist=True)
    assert acc["ok"] is True
    assert acc["hit_count"] == 2
    assert acc["broken_hit_count"] == 1
    assert acc["miss_count"] == 1
    assert acc["hit_rate"] == pytest.approx(2 / 3)
    broken_syms = {r["symbol"] for r in acc["broken_hits"]}
    assert broken_syms == {"000002"}
    stored = store.get_daily("u1", "2026-08-06")
    assert stored["outcome"]["broken_hit_count"] == 1


def test_start_refresh_requires_credentials(fake_col, monkeypatch):
    monkeypatch.setattr(
        store,
        "resolve_llm_credentials",
        lambda _uid: (_ for _ in ()).throw(ValueError("请先配置 DeepSeek API Key")),
    )
    with pytest.raises(ValueError, match="DeepSeek"):
        store.start_refresh("u1", force=True)


def test_list_dates_ready_only(fake_col):
    store.upsert_daily(
        "u1",
        "2026-08-05",
        {"status": "ready", "summary": "a", "picks": [{"symbol": "000001"}], "candidate_count": 1},
    )
    store.upsert_daily(
        "u1",
        "2026-08-06",
        {"status": "running", "summary": "", "picks": [], "candidate_count": 0},
    )
    items = store.list_dates("u1", limit=10)
    assert [i["trade_date"] for i in items] == ["2026-08-05"]
