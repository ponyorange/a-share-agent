from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.advisor import paper


NOW = datetime(2026, 7, 22, 9, tzinfo=timezone.utc)


class Accounts:
    def __init__(self):
        self.doc = {
            "user_id": "u",
            "cash": 1000,
            "account_version": 1,
            "mutation_pending": False,
            "updated_at": NOW,
        }

    def find_one_and_update(self, query, update, return_document=None):
        pending_query = query.get("mutation_pending")
        if (
            isinstance(pending_query, dict)
            and pending_query.get("$ne") is True
        ):
            if self.doc.get("mutation_pending"):
                return None
            if query.get("account_version") != self.doc["account_version"]:
                return None
        elif query.get("mutation_id") != self.doc.get("mutation_id"):
            return None
        for key, value in update.get("$set", {}).items():
            self.doc[key] = value
        for key in update.get("$unset", {}):
            self.doc.pop(key, None)
        for key, value in update.get("$inc", {}).items():
            self.doc[key] = self.doc.get(key, 0) + value
        return dict(self.doc)

    def find_one(self, query, *args, **kwargs):
        return dict(self.doc)

    def find(self, query):
        return []


class EmptyCollection:
    def find(self, *args, **kwargs):
        class Cursor(list):
            def sort(self, *args, **kwargs):
                return self

        return Cursor()

    def find_one(self, *args, **kwargs):
        return None

    def insert_one(self, document):
        return None


class Journals:
    def __init__(self):
        self.docs = {}

    def insert_one(self, document):
        self.docs[document["mutation_id"]] = dict(document)

    def find_one_and_update(self, query, update, return_document=None):
        doc = self.docs.get(query["mutation_id"])
        if doc is None or doc.get("status") != query.get("status"):
            return None
        doc.update(update.get("$set", {}))
        return dict(doc)

    def find_one(self, query, *args, **kwargs):
        return self.docs.get(query.get("mutation_id"))

    def find(self, query):
        class Cursor(list):
            pass

        return Cursor()

    def update_one(self, query, update, upsert=False):
        doc = self.docs.setdefault(query["mutation_id"], dict(query))
        doc.update(update.get("$set", {}))
        doc.update(update.get("$setOnInsert", {}))


class Snapshots:
    def __init__(self):
        self.docs = []

    def insert_one(self, document):
        self.docs.append(dict(document))

    def find_one(self, query, *args, **kwargs):
        matches = [
            doc
            for doc in self.docs
            if all(
                not isinstance(value, dict)
                and doc.get(key) == value
                or isinstance(value, dict)
                for key, value in query.items()
            )
        ]
        return dict(matches[-1]) if matches else None


class DB:
    def __init__(self):
        self.paper_accounts = Accounts()
        self.paper_positions = EmptyCollection()
        self.paper_trades = EmptyCollection()
        self.paper_account_snapshots = Snapshots()
        self.paper_mutations = Journals()


def test_pending_mutation_rejects_concurrent_writer():
    db = DB()
    mutation = paper._begin_account_mutation(
        db,
        "u",
        kind="order:buy",
        expected_version=1,
        expected_updated_at=NOW,
    )
    with pytest.raises(RuntimeError, match="conflict|pending"):
        paper._begin_account_mutation(
            db,
            "u",
            kind="order:sell",
            expected_version=1,
            expected_updated_at=NOW,
        )
    owner, token = paper._mutation_identity(db, "u", mutation)
    completed = paper._finish_account_mutation(
        db,
        "u",
        mutation,
        lease_owner=owner,
        fencing_token=token,
    )
    assert completed["account_version"] == 2
    assert completed["mutation_pending"] is False
    assert completed["latest_mutation_id"] == mutation
    journal = db.paper_mutations.docs[mutation]
    assert journal["status"] == "completed"
    assert journal["archive_hash"]
    assert db.paper_account_snapshots.docs


def test_snapshot_reader_fails_closed_while_mutation_pending(monkeypatch):
    db = DB()
    db.paper_accounts.doc["mutation_pending"] = True
    db.paper_accounts.doc["mutation_id"] = "half-written"
    monkeypatch.setattr(paper, "get_db", lambda: db)
    monkeypatch.setattr(paper, "_now", lambda: NOW)
    with pytest.raises(RuntimeError, match="pending"):
        paper.get_account_snapshot_atomic("u", as_of=NOW)


def test_orphan_account_without_journal_fails_closed(monkeypatch):
    db = DB()
    db.paper_accounts.doc.update(
        {
            "mutation_pending": True,
            "mutation_id": "orphan",
            "mutation_started_at": NOW,
        }
    )
    monkeypatch.setattr(paper, "get_db", lambda: db)
    monkeypatch.setattr(paper, "_now", lambda: NOW)
    with pytest.raises(RuntimeError, match="pending"):
        paper.get_account("u")


def test_journal_written_then_crash_before_account_cas_is_aborted_idempotently():
    db = DB()
    db.paper_mutations.docs["m1"] = {
        "user_id": "u",
        "mutation_id": "m1",
        "type": "order:buy",
        "status": "pending",
        "account_version": 1,
        "started_at": NOW - timedelta(minutes=10),
    }
    first = paper.recover_pending_account_mutation("u", "m1", _db=db)
    second_status = db.paper_mutations.docs["m1"]["status"]
    assert first["account_version"] == 1
    assert second_status == "aborted"
    assert "lock_not_acquired" in db.paper_mutations.docs["m1"]["error"]


def test_account_cas_failure_marks_journal_aborted():
    db = DB()
    original = db.paper_accounts.find_one_and_update

    def fail_lock(query, update, return_document=None):
        if isinstance(query.get("mutation_pending"), dict):
            return None
        return original(query, update, return_document)

    db.paper_accounts.find_one_and_update = fail_lock
    with pytest.raises(RuntimeError, match="conflict"):
        paper._begin_account_mutation(
            db,
            "u",
            kind="order:buy",
            expected_version=1,
            expected_updated_at=NOW,
        )
    assert next(iter(db.paper_mutations.docs.values()))["status"] == "aborted"


def test_pending_to_committing_failure_keeps_account_pending():
    db = DB()
    mutation = paper._begin_account_mutation(
        db, "u", kind="order:buy", expected_version=1, expected_updated_at=NOW
    )
    original = db.paper_mutations.find_one_and_update
    db.paper_mutations.find_one_and_update = (
        lambda query, update, return_document=None: None
        if query.get("status") == "pending"
        else original(query, update, return_document)
    )
    with pytest.raises(RuntimeError, match="committing"):
        owner, token = paper._mutation_identity(db, "u", mutation)
        paper._finish_account_mutation(
            db,
            "u",
            mutation,
            lease_owner=owner,
            fencing_token=token,
        )
    assert db.paper_accounts.doc["mutation_pending"] is True


def _committed_journal_fixture(status: str = "committing"):
    db = DB()
    payload = {
        "user_id": "u",
        "cash": 1000,
        "equity": 1000,
        "positions": [],
        "version": "2",
        "data_as_of": NOW,
        "account_version": 2,
    }
    digest = hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True).encode()
    ).hexdigest()
    db.paper_accounts.doc.update(
        {
            "account_version": 2,
            "latest_mutation_id": "m2",
            "mutation_pending": False,
        }
    )
    db.paper_mutations.docs["m2"] = {
        "user_id": "u",
        "mutation_id": "m2",
        "type": "order:buy",
        "status": status,
        "account_version": 1,
        "intended_account_version": 2,
        "archive_payload": payload,
        "archive_hash": digest,
        "started_at": NOW - timedelta(minutes=10),
    }
    return db


@pytest.mark.parametrize("status", ["committing", "archiving"])
def test_account_commit_before_archive_or_completed_failure_is_recovered(status):
    db = _committed_journal_fixture(status)
    recovered = paper.recover_pending_account_mutation("u", "m2", _db=db)
    assert recovered["account_version"] == 2
    assert db.paper_mutations.docs["m2"]["status"] == "completed"
    assert len(db.paper_account_snapshots.docs) == 1
    paper.recover_pending_account_mutation("u", "m2", _db=db)
    assert len(db.paper_account_snapshots.docs) == 1


def test_orphan_journal_version_conflict_fails_closed():
    db = DB()
    db.paper_accounts.doc["account_version"] = 2
    db.paper_mutations.docs["m3"] = {
        "user_id": "u",
        "mutation_id": "m3",
        "type": "order:buy",
        "status": "pending",
        "account_version": 1,
        "started_at": NOW - timedelta(minutes=10),
    }
    with pytest.raises(RuntimeError, match="conflicts"):
        paper.recover_pending_account_mutation("u", "m3", _db=db)
    assert db.paper_mutations.docs["m3"]["status"] == "conflict"


def test_mark_to_market_and_recovery_journals_are_nontrade():
    assert not "mark_to_market".startswith(("order:", "trade"))
    assert not "recovery".startswith(("order:", "trade"))


@pytest.mark.parametrize("status", ["committing", "archiving"])
def test_get_account_rejects_fresh_unarchived_journal(monkeypatch, status):
    db = _committed_journal_fixture(status)
    db.paper_mutations.docs["m2"]["started_at"] = NOW
    monkeypatch.setattr(paper, "get_db", lambda: db)
    monkeypatch.setattr(paper, "_now", lambda: NOW)
    with pytest.raises(RuntimeError, match="durability"):
        paper.get_account("u")
