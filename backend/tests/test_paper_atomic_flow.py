from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from app.advisor import paper


NOW = datetime(2026, 7, 22, 3, tzinfo=timezone.utc)


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, item) for item in expected):
                return False
            continue
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$exists" in expected and (key in document) is not expected["$exists"]:
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$lt" in expected and (actual is None or actual >= expected["$lt"]):
                return False
            if "$gt" in expected and (actual is None or actual <= expected["$gt"]):
                return False
            continue
        if actual != expected:
            return False
    return True


class Cursor(list):
    def sort(self, key, direction=None):
        if isinstance(key, list):
            rows = list(self)
            for field, order in reversed(key):
                rows.sort(key=lambda item: item.get(field), reverse=order < 0)
            return Cursor(rows)
        return Cursor(
            sorted(self, key=lambda item: item.get(key), reverse=direction < 0)
        )

    def limit(self, count):
        return Cursor(self[:count])


class Collection:
    def __init__(self, *, unique=()):
        self.docs = []
        self.unique = tuple(unique)
        self.fail_next_update = False

    def _check_unique(self, candidate, ignore=None):
        for fields in self.unique:
            if any(candidate.get(field) is None for field in fields):
                continue
            for document in self.docs:
                if document is ignore:
                    continue
                if all(document.get(field) == candidate.get(field) for field in fields):
                    raise DuplicateKeyError(str(fields))

    def insert_one(self, document, **_kwargs):
        value = deepcopy(document)
        value.setdefault("_id", f"id-{len(self.docs) + 1}")
        self._check_unique(value)
        self.docs.append(value)
        document["_id"] = value["_id"]
        return SimpleNamespace(acknowledged=True, inserted_id=value["_id"])

    def find(self, query, projection=None, **_kwargs):
        rows = deepcopy([item for item in self.docs if _matches(item, query)])
        if projection:
            for row in rows:
                for key, enabled in projection.items():
                    if enabled == 0:
                        row.pop(key, None)
        return Cursor(rows)

    def find_one(self, query, projection=None, sort=None, **_kwargs):
        rows = self.find(query, projection)
        if sort:
            rows = rows.sort(sort)
        return deepcopy(rows[0]) if rows else None

    def find_one_and_update(self, query, update, upsert=False, **_kwargs):
        for document in self.docs:
            if _matches(document, query):
                self._apply(document, update)
                self._check_unique(document, ignore=document)
                return deepcopy(document)
        if not upsert:
            return None
        seed = {
            key: value
            for key, value in query.items()
            if not key.startswith("$") and not isinstance(value, dict)
        }
        self._apply(seed, update)
        self.insert_one(seed)
        return deepcopy(seed)

    def update_one(self, query, update, upsert=False, **_kwargs):
        if self.fail_next_update:
            self.fail_next_update = False
            raise RuntimeError("simulated standalone crash")
        for document in self.docs:
            if _matches(document, query):
                self._apply(document, update)
                self._check_unique(document, ignore=document)
                return SimpleNamespace(
                    matched_count=1, modified_count=1, upserted_id=None
                )
        if not upsert:
            return SimpleNamespace(
                matched_count=0, modified_count=0, upserted_id=None
            )
        seed = {
            key: value
            for key, value in query.items()
            if not key.startswith("$") and not isinstance(value, dict)
        }
        self._apply(seed, update)
        result = self.insert_one(seed)
        return SimpleNamespace(
            matched_count=0,
            modified_count=0,
            upserted_id=result.inserted_id,
        )

    def update_many(self, query, update, **_kwargs):
        count = 0
        for document in self.docs:
            if _matches(document, query):
                self._apply(document, update)
                count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

    def delete_one(self, query, **_kwargs):
        for index, document in enumerate(self.docs):
            if _matches(document, query):
                self.docs.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, query, **_kwargs):
        before = len(self.docs)
        self.docs = [item for item in self.docs if not _matches(item, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    @staticmethod
    def _apply(document, update):
        for key, value in update.get("$setOnInsert", {}).items():
            document.setdefault(key, deepcopy(value))
        for key, value in update.get("$set", {}).items():
            document[key] = deepcopy(value)
        for key in update.get("$unset", {}):
            document.pop(key, None)
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value


class Database:
    def __init__(self, *, cash=100_000, position=None):
        self.paper_accounts = Collection(unique=(("user_id",),))
        self.paper_positions = Collection(unique=(("user_id", "symbol"),))
        self.paper_trades = Collection(
            unique=(("user_id", "order_id"), ("user_id", "external_idempotency_key"))
        )
        self.paper_account_snapshots = Collection(
            unique=(("user_id", "data_as_of", "account_version"),)
        )
        self.paper_mutations = Collection(
            unique=(("user_id", "mutation_id"), ("user_id", "external_idempotency_key"))
        )
        self.paper_mutation_counters = Collection(unique=(("user_id",),))
        self.committee_approvals = Collection(
            unique=(("user_id", "idempotency_key"),)
        )
        self.paper_accounts.insert_one(
            {
                "user_id": "u",
                "cash": cash,
                "initial_cash": 100_000,
                "account_version": 0,
                "mutation_pending": False,
                "updated_at": NOW,
            }
        )
        if position:
            self.paper_positions.insert_one(position)
            self.paper_trades.insert_one(
                {
                    "user_id": "u",
                    "symbol": position["symbol"],
                    "side": "buy",
                    "qty": position["qty"],
                    "price": position["cost"],
                    "amount": position["qty"] * position["cost"],
                    "source": "seed",
                    "created_at": NOW - timedelta(days=2),
                }
            )

    def __getitem__(self, name):
        return getattr(self, name)


def _run(monkeypatch, database, *, key, orders, expected=0):
    monkeypatch.setattr(paper, "get_db", lambda: database)
    monkeypatch.setattr(paper, "_now", lambda: NOW)
    return paper.place_orders_atomic(
        user_id="u",
        orders=orders,
        external_idempotency_key=key,
        mutation_source="committee_approval:r",
        expected_account_version=expected,
        lease_owner="approval-owner",
        lease_renew=lambda: None,
    )


def test_atomic_flow_first_buy_and_idempotent_replay(monkeypatch):
    database = Database()
    result = _run(
        monkeypatch,
        database,
        key="buy-key",
        orders=[
            {
                "symbol": "510300",
                "side": "buy",
                "qty": 100,
                "price": 10,
                "name": "ETF",
            }
        ],
    )
    replay = _run(
        monkeypatch,
        database,
        key="buy-key",
        orders=[
            {
                "symbol": "510300",
                "side": "buy",
                "qty": 100,
                "price": 10,
                "name": "ETF",
            }
        ],
    )
    account = database.paper_accounts.find_one({"user_id": "u"})
    position = database.paper_positions.find_one(
        {"user_id": "u", "symbol": "510300"}
    )
    mutation = database.paper_mutations.find_one(
        {"user_id": "u", "external_idempotency_key": "buy-key"}
    )
    assert result == replay
    assert account["cash"] == 99_000
    assert account["account_version"] == 1
    assert position["qty"] == 100
    assert len(database.paper_trades.docs) == 1
    assert mutation["status"] == "completed"
    assert database.paper_account_snapshots.docs


def test_atomic_flow_full_sell_deletes_position(monkeypatch):
    database = Database(
        cash=99_000,
        position={
            "user_id": "u",
            "symbol": "600000",
            "name": "Stock",
            "qty": 100,
            "cost": 10,
            "last": 10,
            "marked_at": NOW,
            "updated_at": NOW,
        },
    )
    result = _run(
        monkeypatch,
        database,
        key="sell-key",
        orders=[
            {
                "symbol": "600000",
                "side": "sell",
                "qty": 100,
                "price": 11,
                "name": "Stock",
            }
        ],
    )
    account = database.paper_accounts.find_one({"user_id": "u"})
    mutation = database.paper_mutations.find_one(
        {"user_id": "u", "external_idempotency_key": "sell-key"}
    )
    assert result["trades"][0]["side"] == "sell"
    assert database.paper_positions.docs == []
    assert account["cash"] == 100_100
    assert account["account_version"] == 1
    assert mutation["status"] == "completed"


def test_standalone_crash_recovers_then_retries_without_duplicate(monkeypatch):
    database = Database()
    database.paper_trades.fail_next_update = True
    with pytest.raises(RuntimeError, match="simulated"):
        _run(
            monkeypatch,
            database,
            key="crash-key",
            orders=[
                {
                    "symbol": "510300",
                    "side": "buy",
                    "qty": 100,
                    "price": 10,
                    "name": "ETF",
                }
            ],
        )
    recovered = database.paper_mutations.find_one(
        {"user_id": "u", "external_idempotency_key": "crash-key"}
    )
    assert recovered["status"] == "recovered"
    result = _run(
        monkeypatch,
        database,
        key="crash-key",
        expected=0,
        orders=[
            {
                "symbol": "510300",
                "side": "buy",
                "qty": 100,
                "price": 10,
                "name": "ETF",
            }
        ],
    )
    assert result["account"]["cash"] == 99_000
    assert len(
        [
            item
            for item in database.paper_trades.docs
            if not item.get("voided")
        ]
    ) == 1
