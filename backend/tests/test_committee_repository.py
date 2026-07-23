from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

import pytest
from bson import ObjectId
from bson.decimal128 import Decimal128

from app.advisor.committee.models import CommitteeRun, RunStatus
from app.advisor.committee.repository import (
    CommitteeRepository,
    IllegalStatusTransition,
    PersistenceError,
    RunNotFound,
    VersionConflict,
    encode_api,
    encode_bson,
)


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _matches(document, query):
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor(list):
    def sort(self, key, direction):
        reverse = direction < 0
        return FakeCursor(sorted(self, key=lambda item: item.get(key), reverse=reverse))

    def limit(self, count):
        return FakeCursor(self[:count])


class FakeCollection:
    def __init__(self, *, acknowledged=True):
        self.documents = []
        self.queries = []
        self.find_one_queries = []
        self.find_queries = []
        self.update_calls = 0
        self.before_find_one_and_update = None
        self.indexes = []
        self.acknowledged = acknowledged

    def insert_one(self, document):
        document["_id"] = ObjectId()
        self.documents.append(deepcopy(document))
        return type(
            "InsertResult",
            (),
            {
                "acknowledged": self.acknowledged,
                "inserted_id": document["_id"] if self.acknowledged else None,
            },
        )()

    def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name", "index")

    def find_one(self, query, *_args):
        self.queries.append(deepcopy(query))
        self.find_one_queries.append(deepcopy(query))
        return next(
            (deepcopy(doc) for doc in self.documents if _matches(doc, query)),
            None,
        )

    def find(self, query, *_args):
        self.queries.append(deepcopy(query))
        self.find_queries.append(deepcopy(query))
        return FakeCursor(
            [deepcopy(doc) for doc in self.documents if _matches(doc, query)]
        )

    def find_one_and_update(self, query, update, **_kwargs):
        self.update_calls += 1
        self.queries.append(deepcopy(query))
        before_update = self.before_find_one_and_update
        self.before_find_one_and_update = None
        if before_update is not None:
            before_update(self)
        for document in self.documents:
            if _matches(document, query):
                document.update(deepcopy(update.get("$set", {})))
                for key, value in update.get("$inc", {}).items():
                    document[key] = document.get(key, 0) + value
                return deepcopy(document)
        return None


class FakeDatabase:
    def __init__(self, *, acknowledged=True):
        self.collections = {
            name: FakeCollection(acknowledged=acknowledged)
            for name in (
                "committee_runs",
                "committee_artifacts",
                "committee_events",
            )
        }

    def __getitem__(self, name):
        return self.collections[name]


def _run(user_id, run_id):
    return CommitteeRun(
        user_id=user_id,
        run_id=run_id,
        strategy_version="v1",
        universe=("510300",),
        as_of=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _terminal_run(user_id: str, run_id: str, status: RunStatus) -> CommitteeRun:
    fields = {
        "status": status,
        "started_at": NOW,
        "completed_at": NOW,
    }
    if status is RunStatus.FAILED:
        fields.update(error_code="boom", error_message="failed")
    return _run(user_id, run_id).model_copy(update=fields)


def test_soft_delete_terminal_run_hides_it_but_preserves_audit_rows():
    database = FakeDatabase()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    repository.create_run(_terminal_run("alice", "run-1", RunStatus.FAILED))
    repository.append_event(
        "alice", "run-1", event_type="failed", payload={"reason": "boom"}
    )
    repository.append_artifact(
        "alice", "run-1", kind="errors", payload=[{"code": "boom"}]
    )

    deleted = repository.soft_delete_run(
        "alice", "run-1", deleted_at=NOW, deleted_by="alice"
    )

    assert deleted.deleted_at == NOW
    assert deleted.deleted_by == "alice"
    assert deleted.version == 2
    assert repository.list_runs("alice") == []
    with pytest.raises(RunNotFound):
        repository.get_run("alice", "run-1")
    assert len(database["committee_events"].documents) == 1
    assert len(database["committee_artifacts"].documents) == 1
    run_collection = database["committee_runs"]
    assert run_collection.find_one_queries
    assert all(
        "deleted_at" in query and query["deleted_at"] is None
        for query in run_collection.find_one_queries
    )
    assert run_collection.find_queries
    assert all(
        "deleted_at" in query and query["deleted_at"] is None
        for query in run_collection.find_queries
    )


def test_soft_delete_rejects_active_foreign_and_already_deleted_runs():
    repository = CommitteeRepository(FakeDatabase(), clock=lambda: NOW)
    repository.create_run(_run("alice", "active"))
    repository.create_run(_terminal_run("alice", "terminal", RunStatus.CANCELLED))

    with pytest.raises(IllegalStatusTransition):
        repository.soft_delete_run(
            "alice", "active", deleted_at=NOW, deleted_by="alice"
        )
    with pytest.raises(RunNotFound):
        repository.soft_delete_run(
            "mallory", "terminal", deleted_at=NOW, deleted_by="mallory"
        )

    repository.soft_delete_run(
        "alice", "terminal", deleted_at=NOW, deleted_by="alice"
    )
    with pytest.raises(RunNotFound):
        repository.soft_delete_run(
            "alice", "terminal", deleted_at=NOW, deleted_by="alice"
        )


def test_soft_delete_version_race_preserves_concurrent_write():
    database = FakeDatabase()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    repository.create_run(_terminal_run("alice", "run-1", RunStatus.FAILED))
    concurrent_time = NOW + timedelta(seconds=1)

    def change_version(collection):
        collection.documents[0]["version"] = 2
        collection.documents[0]["updated_at"] = concurrent_time

    database["committee_runs"].before_find_one_and_update = change_version

    with pytest.raises(VersionConflict):
        repository.soft_delete_run(
            "alice", "run-1", deleted_at=NOW, deleted_by="alice"
        )

    stored = database["committee_runs"].documents[0]
    assert stored["version"] == 2
    assert stored["updated_at"] == concurrent_time
    assert stored["deleted_at"] is None
    assert stored["deleted_by"] is None


def test_soft_delete_status_race_preserves_concurrent_write():
    database = FakeDatabase()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    repository.create_run(_terminal_run("alice", "run-1", RunStatus.FAILED))
    concurrent_time = NOW + timedelta(seconds=1)

    def change_status(collection):
        document = collection.documents[0]
        document.update(
            {
                "status": RunStatus.PENDING.value,
                "started_at": None,
                "completed_at": None,
                "error_code": None,
                "error_message": None,
                "updated_at": concurrent_time,
            }
        )

    database["committee_runs"].before_find_one_and_update = change_status

    with pytest.raises(
        IllegalStatusTransition, match="status changed before deletion"
    ):
        repository.soft_delete_run(
            "alice", "run-1", deleted_at=NOW, deleted_by="alice"
        )

    stored = database["committee_runs"].documents[0]
    assert stored["status"] == RunStatus.PENDING.value
    assert stored["updated_at"] == concurrent_time
    assert stored["deleted_at"] is None
    assert stored["deleted_by"] is None


def test_soft_delete_race_with_prior_delete_preserves_winner():
    database = FakeDatabase()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    repository.create_run(_terminal_run("alice", "run-1", RunStatus.CANCELLED))

    def delete_first(collection):
        collection.documents[0].update(
            {
                "deleted_at": NOW,
                "deleted_by": "concurrent-user",
                "updated_at": NOW,
            }
        )

    database["committee_runs"].before_find_one_and_update = delete_first

    with pytest.raises(RunNotFound):
        repository.soft_delete_run(
            "alice", "run-1", deleted_at=NOW, deleted_by="alice"
        )

    stored = database["committee_runs"].documents[0]
    assert stored["version"] == 1
    assert stored["deleted_at"] == NOW
    assert stored["deleted_by"] == "concurrent-user"


@pytest.mark.parametrize(
    "updates",
    [
        {"deleted_at": NOW},
        {"deleted_by": "alice"},
    ],
)
def test_committee_run_requires_delete_fields_together(updates):
    data = _terminal_run(
        "alice", "run-1", RunStatus.CANCELLED
    ).model_dump(mode="python")
    data.update(updates)

    with pytest.raises(
        ValueError, match="deleted_at and deleted_by must be set together"
    ):
        CommitteeRun.model_validate(data)


def test_committee_run_rejects_deletion_in_non_terminal_status():
    data = _run("alice", "run-1").model_dump(mode="python")
    data.update(deleted_at=NOW, deleted_by="alice")

    with pytest.raises(ValueError, match="only terminal runs may be deleted"):
        CommitteeRun.model_validate(data)


def test_committee_run_rejects_deletion_before_completion():
    data = _terminal_run(
        "alice", "run-1", RunStatus.CANCELLED
    ).model_dump(mode="python")
    data.update(
        deleted_at=NOW - timedelta(seconds=1),
        deleted_by="alice",
    )

    with pytest.raises(
        ValueError, match="deleted_at cannot precede completion"
    ):
        CommitteeRun.model_validate(data)


@pytest.mark.parametrize(
    "deleted_at",
    [
        NOW.replace(tzinfo=None),
        NOW.astimezone(timezone(timedelta(hours=8))),
    ],
)
def test_committee_run_rejects_non_utc_deletion_time(deleted_at):
    data = _terminal_run(
        "alice", "run-1", RunStatus.CANCELLED
    ).model_dump(mode="python")
    data.update(deleted_at=deleted_at, deleted_by="alice")

    with pytest.raises(ValueError, match="datetime must"):
        CommitteeRun.model_validate(data)


def test_repository_isolates_every_read_by_user_id():
    database = FakeDatabase()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    repository.create_run(_run("alice", "same-id"))
    repository.create_run(_run("bob", "same-id"))
    repository.append_artifact(
        "alice", "same-id", kind="report", payload={"score": 0.7}
    )
    repository.append_event(
        "alice", "same-id", event_type="created", payload={"ok": True}
    )

    assert [run.user_id for run in repository.list_runs("alice")] == ["alice"]
    detail = repository.get_detail("alice", "same-id")
    assert detail.run.user_id == "alice"
    assert len(detail.artifacts) == 1
    assert len(detail.events) == 1
    with pytest.raises(RunNotFound):
        repository.get_run("mallory", "same-id")

    for collection in database.collections.values():
        assert all("user_id" in query for query in collection.queries)


def test_atomic_transition_rejects_illegal_status_and_version_conflict():
    repository = CommitteeRepository(FakeDatabase(), clock=lambda: NOW)
    repository.create_run(_run("alice", "run-1"))

    with pytest.raises(IllegalStatusTransition):
        repository.transition_status(
            "alice",
            "run-1",
            expected_version=1,
            new_status=RunStatus.COMPLETED,
        )

    collecting = repository.transition_status(
        "alice",
        "run-1",
        expected_version=1,
        new_status=RunStatus.COLLECTING,
    )
    assert collecting.version == 2
    assert collecting.status is RunStatus.COLLECTING

    with pytest.raises(VersionConflict):
        repository.transition_status(
            "alice",
            "run-1",
            expected_version=1,
            new_status=RunStatus.ANALYZING,
        )


def test_transition_cannot_overwrite_identity_or_version_fields():
    repository = CommitteeRepository(FakeDatabase(), clock=lambda: NOW)
    repository.create_run(_run("alice", "run-1"))

    with pytest.raises(ValueError, match="protected"):
        repository.transition_status(
            "alice",
            "run-1",
            expected_version=1,
            new_status=RunStatus.COLLECTING,
            version=99,
        )


def test_update_run_prevalidates_candidate_before_cas_write():
    database = FakeDatabase()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    repository.create_run(_run("alice", "run-1"))

    with pytest.raises(ValueError, match="allowed"):
        repository.update_run(
            "alice",
            "run-1",
            expected_version=1,
            expected_status=RunStatus.PENDING,
            updates={"arbitrary": "field"},
        )
    with pytest.raises(Exception):
        repository.update_run(
            "alice",
            "run-1",
            expected_version=1,
            expected_status=RunStatus.PENDING,
            updates={
                "status": RunStatus.FAILED,
            },
        )

    assert database.collections["committee_runs"].update_calls == 0


def test_bson_and_api_encoding_are_separate_recursive_and_finite():
    class Choice(str, Enum):
        VALUE = "value"

    object_id = ObjectId()
    payload = {
        "when": NOW,
        "decimal": Decimal("1.25"),
        "choice": Choice.VALUE,
        "object_id": object_id,
        "nested": [{"value": Decimal("2.5")}],
    }

    bson_payload = encode_bson(payload)
    assert bson_payload["when"] == NOW
    assert bson_payload["decimal"] == Decimal128("1.25")
    assert bson_payload["object_id"] == object_id

    api_payload = encode_api(bson_payload)
    assert api_payload["when"] == "2026-07-21T12:00:00Z"
    assert api_payload["decimal"] == "1.25"
    assert api_payload["choice"] == "value"
    assert api_payload["object_id"] == str(object_id)
    with pytest.raises(ValueError, match="finite"):
        encode_bson({"bad": float("nan")})


def test_inserts_use_copies_hide_mongo_id_and_check_acknowledgement():
    database = FakeDatabase()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    run = _run("alice", "run-1")
    repository.create_run(run)
    result = repository.append_event(
        "alice", "run-1", event_type="created", payload={"ok": True}
    )

    assert "_id" not in result
    assert "_id" in database.collections["committee_events"].documents[0]
    assert run.model_dump().get("_id") is None

    rejected = CommitteeRepository(
        FakeDatabase(acknowledged=False), clock=lambda: NOW
    )
    with pytest.raises(PersistenceError, match="acknowledged"):
        rejected.create_run(_run("alice", "run-2"))


def test_ensure_indexes_creates_unique_tenant_run_index():
    database = FakeDatabase()
    repository = CommitteeRepository(database)

    repository.ensure_indexes()

    run_indexes = database.collections["committee_runs"].indexes
    assert any(
        keys == [("user_id", 1), ("run_id", 1)]
        and options.get("unique") is True
        for keys, options in run_indexes
    )


def test_repository_restores_utc_for_pymongo_naive_bson_datetimes():
    database = FakeDatabase()
    repository = CommitteeRepository(database)
    repository.create_run(_run("alice", "run-1"))
    stored = database.collections["committee_runs"].documents[0]
    for field in ("as_of", "created_at", "updated_at"):
        stored[field] = stored[field].replace(tzinfo=None)

    loaded = repository.get_run("alice", "run-1")

    assert loaded.as_of.tzinfo is timezone.utc
    assert loaded.created_at.tzinfo is timezone.utc


def test_global_index_initialization_includes_committee_without_redis(
    monkeypatch,
):
    from app import db as db_module
    from app.advisor.committee import redis_client

    class Collection:
        def create_index(self, *_args, **_kwargs):
            return "index"

        def list_indexes(self):
            return []

    class Database:
        def __init__(self):
            self.collections = {}

        def __getattr__(self, name):
            return self[name]

        def __getitem__(self, name):
            return self.collections.setdefault(name, Collection())

    database = Database()
    calls = []
    monkeypatch.setattr(db_module, "get_db", lambda: database)
    monkeypatch.setattr(
        CommitteeRepository,
        "ensure_indexes",
        lambda self: calls.append(self),
    )
    monkeypatch.setattr(
        redis_client,
        "create_client",
        lambda *_args, **_kwargs: pytest.fail("Redis must not be initialized"),
    )

    db_module.ensure_indexes()

    assert len(calls) == 1
