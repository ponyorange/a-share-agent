"""Mongo-backed permanent audit repository for committee runs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import math
from typing import Any, Protocol
from uuid import uuid4

from bson import ObjectId
from bson.decimal128 import Decimal128
from pydantic import BaseModel
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from .models import CommitteeRun, RunStatus, utc_now


class RepositoryError(RuntimeError):
    pass


class RunNotFound(RepositoryError):
    pass


class IllegalStatusTransition(RepositoryError):
    pass


class VersionConflict(RepositoryError):
    pass


class PersistenceError(RepositoryError):
    pass


ACTIVE_STATUSES = frozenset(
    {
        RunStatus.CREATED,
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.PENDING,
        RunStatus.COLLECTING,
        RunStatus.ANALYZING,
        RunStatus.DEBATING,
        RunStatus.PROPOSING,
        RunStatus.BACKTESTING,
        RunStatus.RISK_REVIEW,
    }
)

ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset(
        {RunStatus.QUEUED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.QUEUED: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.RUNNING: frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.PENDING: frozenset(
        {RunStatus.COLLECTING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.COLLECTING: frozenset(
        {RunStatus.ANALYZING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.ANALYZING: frozenset(
        {RunStatus.DEBATING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.DEBATING: frozenset(
        {RunStatus.PROPOSING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.PROPOSING: frozenset(
        {RunStatus.BACKTESTING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.BACKTESTING: frozenset(
        {RunStatus.RISK_REVIEW, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.RISK_REVIEW: frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class CommitteeRepositoryProtocol(Protocol):
    def create_run(self, run: CommitteeRun) -> CommitteeRun: ...

    def get_run(self, user_id: str, run_id: str) -> CommitteeRun: ...

    def soft_delete_run(
        self,
        user_id: str,
        run_id: str,
        *,
        deleted_at: datetime,
        deleted_by: str,
    ) -> CommitteeRun: ...

    def transition_status(
        self,
        user_id: str,
        run_id: str,
        *,
        expected_version: int,
        new_status: RunStatus,
        **fields: Any,
    ) -> CommitteeRun: ...

    def update_run(
        self,
        user_id: str,
        run_id: str,
        *,
        expected_version: int,
        expected_status: RunStatus,
        updates: Mapping[str, Any],
    ) -> CommitteeRun: ...


@dataclass(frozen=True, slots=True)
class RunDetail:
    run: CommitteeRun
    artifacts: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]


def _finite_float(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("numeric values must be finite")
    return value


def encode_bson(value: Any) -> Any:
    """Encode a copied value for BSON without API stringification."""
    if isinstance(value, BaseModel):
        return encode_bson(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return encode_bson(value.value)
    if value is None or isinstance(value, (str, bool, int, ObjectId, Decimal128)):
        return value
    if isinstance(value, float):
        return _finite_float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("BSON datetime must be timezone-aware")
        return value.astimezone(timezone.utc)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("numeric values must be finite")
        return Decimal128(value)
    if isinstance(value, Mapping):
        return {
            str(key): encode_bson(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [encode_bson(item) for item in value]
    if isinstance(value, (set, frozenset)):
        encoded = [encode_bson(item) for item in value]
        return sorted(encoded, key=lambda item: repr(encode_api(item)))
    raise TypeError(f"unsupported BSON value: {type(value).__name__}")


def encode_api(value: Any) -> Any:
    """Encode BSON/domain values for API output without leaking Mongo types."""
    if isinstance(value, BaseModel):
        return encode_api(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return encode_api(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return _finite_float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("API datetime must be timezone-aware")
        return (
            value.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    if isinstance(value, Decimal128):
        return str(value.to_decimal())
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("numeric values must be finite")
        return str(value)
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): encode_api(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_api(item) for item in value]
    if isinstance(value, (set, frozenset)):
        encoded = [encode_api(item) for item in value]
        return sorted(encoded, key=repr)
    raise TypeError(f"unsupported API value: {type(value).__name__}")


def _decode_bson(value: Any) -> Any:
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, Mapping):
        return {key: _decode_bson(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_bson(item) for item in value]
    return value


def _without_mongo_id(document: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(document)
    clean.pop("_id", None)
    return _decode_bson(clean)


class CommitteeRepository:
    RUNS = "committee_runs"
    ARTIFACTS = "committee_artifacts"
    EVENTS = "committee_events"
    COUNTERS = "committee_counters"
    UPDATE_FIELDS = frozenset(
        {"status", "snapshot_id", "error_code", "error_message"}
    )

    def __init__(self, database: Any, *, clock=utc_now) -> None:
        self._database = database
        self._runs = database[self.RUNS]
        self._artifacts = database[self.ARTIFACTS]
        self._events = database[self.EVENTS]
        self._clock = clock

    @classmethod
    def from_default_database(cls) -> CommitteeRepository:
        from ...db import get_db

        return cls(get_db())

    def create_run(self, run: CommitteeRun) -> CommitteeRun:
        document = encode_bson(run)
        self._checked_insert(self._runs, document)
        return run

    @staticmethod
    def _checked_insert(collection: Any, document: Mapping[str, Any]) -> Any:
        result = collection.insert_one(deepcopy(dict(document)))
        if not getattr(result, "acknowledged", False):
            raise PersistenceError("Mongo insert was not acknowledged")
        if getattr(result, "inserted_id", None) is None:
            raise PersistenceError("Mongo insert did not return inserted_id")
        return result

    def ensure_indexes(self) -> None:
        self._runs.create_index(
            [("user_id", ASCENDING), ("run_id", ASCENDING)],
            unique=True,
            name="committee_run_user_run_unique",
        )
        self._runs.create_index(
            [("user_id", ASCENDING), ("created_at", ASCENDING)],
            name="committee_run_user_created",
        )
        self._runs.create_index(
            [("user_id", ASCENDING), ("idempotency_key", ASCENDING)],
            unique=True,
            partialFilterExpression={
                "idempotency_key": {"$type": "string"}
            },
            name="committee_run_user_idempotency_unique",
        )
        self._runs.create_index(
            [
                ("user_id", ASCENDING),
                ("parent_run_id", ASCENDING),
                ("attempt", ASCENDING),
            ],
            unique=True,
            partialFilterExpression={"parent_run_id": {"$type": "string"}},
            name="committee_retry_parent_attempt_unique",
        )
        self._runs.create_index(
            [("status", ASCENDING), ("job_heartbeat_at", ASCENDING)],
            name="committee_run_watchdog",
        )
        self._runs.create_index(
            [("status", ASCENDING), ("execution_lease_expires_at", ASCENDING)],
            name="committee_run_execution_lease",
        )
        self._artifacts.create_index(
            [
                ("user_id", ASCENDING),
                ("run_id", ASCENDING),
                ("created_at", ASCENDING),
            ],
            name="committee_artifact_audit",
        )
        self._events.create_index(
            [
                ("user_id", ASCENDING),
                ("run_id", ASCENDING),
                ("created_at", ASCENDING),
            ],
            name="committee_event_audit",
        )
        self._events.create_index(
            [
                ("user_id", ASCENDING),
                ("run_id", ASCENDING),
                ("event_id", ASCENDING),
            ],
            unique=True,
            name="committee_event_stream_id_unique",
        )
        self._events.create_index(
            [
                ("user_id", ASCENDING),
                ("run_id", ASCENDING),
                ("event_key", ASCENDING),
            ],
            unique=True,
            partialFilterExpression={"event_key": {"$type": "string"}},
            name="committee_event_key_unique",
        )
        self._artifacts.create_index(
            [
                ("user_id", ASCENDING),
                ("run_id", ASCENDING),
                ("artifact_id", ASCENDING),
            ],
            unique=True,
            name="committee_artifact_id_unique",
        )
        try:
            self._database[self.COUNTERS].create_index(
                [("user_id", ASCENDING), ("run_id", ASCENDING)],
                unique=True,
                name="committee_counter_user_run_unique",
            )
        except (AttributeError, KeyError):
            pass

    def get_run(self, user_id: str, run_id: str) -> CommitteeRun:
        document = self._runs.find_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "deleted_at": None,
            },
            {"_id": 0},
        )
        if document is None:
            raise RunNotFound(f"committee run {run_id!r} was not found")
        return CommitteeRun.model_validate(_without_mongo_id(document))

    def list_runs(self, user_id: str, *, limit: int = 50) -> list[CommitteeRun]:
        bounded_limit = max(1, min(int(limit), 200))
        cursor = (
            self._runs.find(
                {"user_id": user_id, "deleted_at": None},
                {"_id": 0},
            )
            .sort("created_at", -1)
            .limit(bounded_limit)
        )
        return [
            CommitteeRun.model_validate(_without_mongo_id(document))
            for document in cursor
        ]

    def find_idempotent_run(
        self, user_id: str, idempotency_key: str
    ) -> CommitteeRun | None:
        document = self._runs.find_one(
            {"user_id": user_id, "idempotency_key": idempotency_key},
            {"_id": 0},
        )
        if document is None:
            return None
        return CommitteeRun.model_validate(_without_mongo_id(document))

    def transition_status(
        self,
        user_id: str,
        run_id: str,
        *,
        expected_version: int,
        new_status: RunStatus,
        **fields: Any,
    ) -> CommitteeRun:
        attempted = set(fields).difference(
            {"snapshot_id", "error_code", "error_message"}
        )
        if attempted:
            raise ValueError(
                "protected transition fields cannot be overwritten: "
                + ", ".join(sorted(attempted))
            )
        current = self.get_run(user_id, run_id)
        return self.update_run(
            user_id,
            run_id,
            expected_version=expected_version,
            expected_status=current.status,
            updates={"status": new_status, **fields},
        )

    def update_run(
        self,
        user_id: str,
        run_id: str,
        *,
        expected_version: int,
        expected_status: RunStatus,
        updates: Mapping[str, Any],
    ) -> CommitteeRun:
        unexpected = set(updates).difference(self.UPDATE_FIELDS)
        if unexpected:
            raise ValueError(
                "only allowed run fields may be updated: "
                + ", ".join(sorted(unexpected))
            )
        current = self.get_run(user_id, run_id)
        resolved_expected = RunStatus(expected_status)
        if current.version != expected_version:
            raise VersionConflict(
                f"expected version {expected_version}, found {current.version}"
            )
        if current.status is not resolved_expected:
            raise VersionConflict(
                f"expected status {resolved_expected.value}, "
                f"found {current.status.value}"
            )
        target_status = RunStatus(updates.get("status", current.status))
        if (
            target_status is not current.status
            and target_status not in ALLOWED_TRANSITIONS[current.status]
        ):
            raise IllegalStatusTransition(
                f"cannot transition {current.status.value} to {target_status.value}"
            )
        now = self._clock()
        candidate_data = current.model_dump(mode="python")
        candidate_data.update(dict(updates))
        candidate_data.update(
            {
                "status": target_status,
                "version": expected_version + 1,
                "updated_at": now,
            }
        )
        if target_status in {
            RunStatus.COLLECTING,
            RunStatus.RUNNING,
        } and current.started_at is None:
            candidate_data["started_at"] = now
        if target_status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            candidate_data["completed_at"] = now
        candidate = CommitteeRun.model_validate(candidate_data)
        mutable_fields = {
            field: getattr(candidate, field)
            for field in (
                "status",
                "snapshot_id",
                "updated_at",
                "started_at",
                "completed_at",
                "error_code",
                "error_message",
            )
        }
        document = self._runs.find_one_and_update(
            {
                "user_id": user_id,
                "run_id": run_id,
                "version": expected_version,
                "status": resolved_expected.value,
            },
            {
                "$set": encode_bson(mutable_fields),
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            latest = self.get_run(user_id, run_id)
            if latest.version != expected_version:
                raise VersionConflict(
                    f"expected version {expected_version}, found {latest.version}"
                )
            raise IllegalStatusTransition("run status changed concurrently")
        return CommitteeRun.model_validate(_without_mongo_id(document))

    def soft_delete_run(
        self,
        user_id: str,
        run_id: str,
        *,
        deleted_at: datetime,
        deleted_by: str,
    ) -> CommitteeRun:
        current = self.get_run(user_id, run_id)
        terminal = {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        if current.status not in terminal:
            raise IllegalStatusTransition(
                "only completed, failed, or cancelled runs can be deleted"
            )
        candidate = current.model_copy(
            update={
                "deleted_at": deleted_at,
                "deleted_by": deleted_by,
                "updated_at": deleted_at,
                "version": current.version + 1,
            }
        )
        candidate = CommitteeRun.model_validate(
            candidate.model_dump(mode="python")
        )
        document = self._runs.find_one_and_update(
            {
                "user_id": user_id,
                "run_id": run_id,
                "version": current.version,
                "status": current.status.value,
                "deleted_at": None,
            },
            {
                "$set": encode_bson(
                    {
                        "deleted_at": candidate.deleted_at,
                        "deleted_by": candidate.deleted_by,
                        "updated_at": candidate.updated_at,
                    }
                ),
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            try:
                latest = self.get_run(user_id, run_id)
            except RunNotFound:
                raise
            if latest.status not in terminal:
                raise IllegalStatusTransition(
                    "run status changed before deletion"
                )
            raise VersionConflict("run changed while it was deleted")
        return CommitteeRun.model_validate(_without_mongo_id(document))

    def request_cancel(
        self,
        user_id: str,
        run_id: str,
        *,
        expected_version: int,
    ) -> CommitteeRun:
        current = self.get_run(user_id, run_id)
        if current.status is RunStatus.CANCELLED:
            return current
        if current.status not in ACTIVE_STATUSES:
            raise IllegalStatusTransition("only active runs can be cancelled")
        document = self._runs.find_one_and_update(
            {
                "user_id": user_id,
                "run_id": run_id,
                "version": expected_version,
                "status": current.status.value,
            },
            {
                "$set": {
                    "status": RunStatus.CANCELLED.value,
                    "cancel_requested": True,
                    "updated_at": encode_bson(self._clock()),
                    "completed_at": encode_bson(self._clock()),
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise VersionConflict("run changed while cancellation was requested")
        return CommitteeRun.model_validate(_without_mongo_id(document))

    def record_job_started(
        self,
        user_id: str,
        run_id: str,
        *,
        job_id: str,
        deadline_at: datetime,
    ) -> CommitteeRun:
        document = self._runs.find_one_and_update(
            {
                "user_id": user_id,
                "run_id": run_id,
                "status": RunStatus.RUNNING.value,
            },
            {
                "$set": {
                    "queue_job_id": job_id,
                    "job_heartbeat_at": self._clock(),
                    "job_deadline_at": deadline_at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise VersionConflict("run is no longer running")
        return CommitteeRun.model_validate(_without_mongo_id(document))

    def claim_execution_lease(
        self,
        user_id: str,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int,
    ) -> CommitteeRun:
        now = self._clock()
        document = self._runs.find_one_and_update(
            {
                "user_id": user_id,
                "run_id": run_id,
                "status": {
                    "$in": [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                    ]
                },
                "$or": [
                    {"execution_owner": owner},
                    {"execution_owner": None},
                    {"execution_lease_expires_at": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "execution_owner": owner,
                    "execution_lease_expires_at": now
                    + timedelta(seconds=lease_seconds),
                    "execution_heartbeat_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise VersionConflict("run execution lease is active")
        return CommitteeRun.model_validate(_without_mongo_id(document))

    def renew_execution_lease(
        self,
        user_id: str,
        run_id: str,
        *,
        owner: str,
        lease_seconds: int,
    ) -> bool:
        now = self._clock()
        result = self._runs.update_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "status": {
                    "$in": [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                    ]
                },
                "execution_owner": owner,
            },
            {
                "$set": {
                    "execution_lease_expires_at": now
                    + timedelta(seconds=lease_seconds),
                    "execution_heartbeat_at": now,
                }
            },
        )
        return bool(getattr(result, "modified_count", 0))

    def release_execution_lease(
        self,
        user_id: str,
        run_id: str,
        *,
        owner: str,
    ) -> None:
        self._runs.update_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "execution_owner": owner,
            },
            {
                "$set": {
                    "execution_owner": None,
                    "execution_lease_expires_at": None,
                }
            },
        )

    def touch_job_heartbeat(
        self,
        user_id: str,
        run_id: str,
        *,
        job_id: str,
    ) -> bool:
        result = self._runs.update_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "status": RunStatus.RUNNING.value,
                "queue_job_id": job_id,
            },
            {"$set": {"job_heartbeat_at": self._clock()}},
        )
        return bool(getattr(result, "modified_count", 0))

    def record_resume_enqueued(
        self,
        user_id: str,
        run_id: str,
        *,
        expected_resume_attempts: int,
        queue_job_id: str,
        next_resume_at: datetime,
        job_deadline_at: datetime,
    ) -> CommitteeRun:
        document = self._runs.find_one_and_update(
            {
                "user_id": user_id,
                "run_id": run_id,
                "status": {
                    "$in": [RunStatus.QUEUED.value, RunStatus.RUNNING.value]
                },
                "resume_attempts": (
                    {"$in": [0, None]}
                    if expected_resume_attempts == 0
                    else expected_resume_attempts
                ),
            },
            {
                "$inc": {"resume_attempts": 1, "version": 1},
                "$set": {
                    "status": RunStatus.QUEUED.value,
                    "queue_job_id": queue_job_id,
                    "next_resume_at": next_resume_at,
                    "job_deadline_at": job_deadline_at,
                    "job_heartbeat_at": self._clock(),
                    "execution_owner": None,
                    "execution_lease_expires_at": None,
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise VersionConflict("run resume state changed concurrently")
        return CommitteeRun.model_validate(_without_mongo_id(document))

    def list_stale_runs(
        self,
        *,
        heartbeat_before: datetime,
        limit: int = 100,
    ) -> list[CommitteeRun]:
        now = self._clock()
        cursor = self._runs.find(
            {
                "status": {
                    "$in": [
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                    ]
                },
                "$or": [
                    {"job_heartbeat_at": {"$lt": heartbeat_before}},
                    {"job_heartbeat_at": None},
                    {"execution_heartbeat_at": {"$lt": heartbeat_before}},
                    {"execution_lease_expires_at": {"$lte": now}},
                    {"job_deadline_at": {"$lte": now}},
                ],
            },
            {"_id": 0},
        ).limit(max(1, min(limit, 500)))
        return [
            CommitteeRun.model_validate(_without_mongo_id(item))
            for item in cursor
        ]

    def allocate_retry_attempt(
        self,
        user_id: str,
        parent_run_id: str,
    ) -> int:
        document = self._runs.find_one_and_update(
            {
                "user_id": user_id,
                "run_id": parent_run_id,
                "status": {
                    "$in": [
                        RunStatus.FAILED.value,
                        RunStatus.CANCELLED.value,
                    ]
                },
            },
            {"$inc": {"next_attempt": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise IllegalStatusTransition("parent run is not retryable")
        return int(document["next_attempt"]) - 1

    def append_artifact(
        self,
        user_id: str,
        run_id: str,
        *,
        kind: str,
        payload: Any,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_run(user_id, run_id)
        document = {
            "artifact_id": artifact_id or uuid4().hex,
            "user_id": user_id,
            "run_id": run_id,
            "kind": kind,
            "payload": encode_bson(payload),
            "created_at": encode_bson(self._clock()),
        }
        self._checked_insert(self._artifacts, document)
        return encode_api(document)

    def append_event(
        self,
        user_id: str,
        run_id: str,
        *,
        event_type: str,
        payload: Any,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_run(user_id, run_id)
        document = {
            "event_id": event_id or uuid4().hex,
            "user_id": user_id,
            "run_id": run_id,
            "event_type": event_type,
            "payload": encode_bson(payload),
            "created_at": encode_bson(self._clock()),
        }
        self._checked_insert(self._events, document)
        return encode_api(document)

    def append_outbox_event(
        self,
        user_id: str,
        run_id: str,
        *,
        attempt: int,
        node: str,
        event_type: str,
        event_key: str,
        payload: Any,
    ) -> dict[str, Any]:
        self.get_run(user_id, run_id)
        existing = self._events.find_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "event_key": event_key,
            },
            {"_id": 0},
        )
        if existing is not None:
            return encode_api(_without_mongo_id(existing))
        counters = self._database[self.COUNTERS]
        try:
            counter = counters.find_one_and_update(
                {"user_id": user_id, "run_id": run_id},
                {"$inc": {"sequence": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            counter = counters.find_one_and_update(
                {"user_id": user_id, "run_id": run_id},
                {"$inc": {"sequence": 1}},
                return_document=ReturnDocument.AFTER,
            )
        if counter is None:
            raise PersistenceError("outbox sequence allocation failed")
        sequence = int(counter["sequence"])
        document = {
            "event_id": str(sequence),
            "sequence": sequence,
            "event_key": event_key,
            "attempt": int(attempt),
            "node": node,
            "user_id": user_id,
            "run_id": run_id,
            "event_type": event_type,
            "payload": encode_bson(payload),
            "published": False,
            "created_at": encode_bson(self._clock()),
        }
        try:
            self._checked_insert(self._events, document)
        except DuplicateKeyError:
            raced = self._events.find_one(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "event_key": event_key,
                },
                {"_id": 0},
            )
            if raced is None:
                raise
            return encode_api(_without_mongo_id(raced))
        return encode_api(document)

    def mark_event_published(
        self,
        user_id: str,
        run_id: str,
        event_key: str,
    ) -> None:
        self._events.update_one(
            {
                "user_id": user_id,
                "run_id": run_id,
                "event_key": event_key,
            },
            {"$set": {"published": True, "published_at": self._clock()}},
        )

    def list_unpublished_events(
        self,
        user_id: str,
        run_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            encode_api(_without_mongo_id(document))
            for document in self._events.find(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "published": {"$ne": True},
                    "sequence": {"$exists": True},
                },
                {"_id": 0},
            ).sort("sequence", 1).limit(max(1, min(limit, 1000)))
        ]

    def upsert_artifact(
        self,
        user_id: str,
        run_id: str,
        *,
        kind: str,
        artifact_id: str,
        payload: Any,
        attempt: int,
        node: str,
    ) -> dict[str, Any]:
        self.get_run(user_id, run_id)
        document = {
            "artifact_id": artifact_id,
            "user_id": user_id,
            "run_id": run_id,
            "attempt": int(attempt),
            "node": node,
            "kind": kind,
            "payload": encode_bson(payload),
            "created_at": encode_bson(self._clock()),
        }
        try:
            self._checked_insert(self._artifacts, document)
            return encode_api(document)
        except DuplicateKeyError:
            existing = self._artifacts.find_one(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "artifact_id": artifact_id,
                },
                {"_id": 0},
            )
            if existing is None:
                raise
            if encode_api(existing.get("payload")) != encode_api(document["payload"]):
                raise PersistenceError("artifact replay payload mismatch")
            return encode_api(_without_mongo_id(existing))

    def get_detail(self, user_id: str, run_id: str) -> RunDetail:
        run = self.get_run(user_id, run_id)
        artifact_query = {"user_id": user_id, "run_id": run_id}
        event_query = {"user_id": user_id, "run_id": run_id}
        artifacts = tuple(
            encode_api(_without_mongo_id(document))
            for document in self._artifacts.find(
                artifact_query, {"_id": 0}
            ).sort("created_at", 1)
        )
        events = tuple(
            encode_api(_without_mongo_id(document))
            for document in self._events.find(
                event_query, {"_id": 0}
            ).sort("created_at", 1)
        )
        return RunDetail(run=run, artifacts=artifacts, events=events)

    def list_events(
        self,
        user_id: str,
        run_id: str,
        *,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        self.get_run(user_id, run_id)
        return [
            encode_api(_without_mongo_id(document))
            for document in self._events.find(
                {"user_id": user_id, "run_id": run_id},
                {"_id": 0},
            ).sort("created_at", 1).limit(max(1, min(limit, 5000)))
        ]

    def list_events_after(
        self,
        user_id: str,
        run_id: str,
        *,
        after_sequence: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.get_run(user_id, run_id)
        bounded = max(1, min(int(limit), 1000))
        return [
            encode_api(_without_mongo_id(document))
            for document in self._events.find(
                {
                    "user_id": user_id,
                    "run_id": run_id,
                    "sequence": {"$gt": int(after_sequence)},
                },
                {"_id": 0},
            ).sort("sequence", 1).limit(bounded)
        ]

    def latest_artifact(
        self,
        user_id: str,
        run_id: str,
        kind: str,
    ) -> dict[str, Any] | None:
        self.get_run(user_id, run_id)
        document = self._artifacts.find_one(
            {"user_id": user_id, "run_id": run_id, "kind": kind},
            {"_id": 0},
            sort=[("created_at", -1)],
        )
        return (
            None
            if document is None
            else encode_api(_without_mongo_id(document))
        )
