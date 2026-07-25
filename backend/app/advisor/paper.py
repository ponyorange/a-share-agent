"""Paper trading (模拟盘)."""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
from typing import Any, Literal
import uuid
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..db import get_db
from ..kline import normalize_symbol
from .features import fetch_daily_df

LOT = 100  # A-share board lot
# 一键买入默认只买 ETF + 沪深，不买科创
ONE_CLICK_BOARDS = ("etf", "hs")


def _next_fencing_token(db: Any, user_id: str) -> int:
    try:
        counters = db["paper_mutation_counters"]
    except (AttributeError, TypeError):
        tokens = [
            int(item.get("fencing_token", 0))
            for item in getattr(db.paper_mutations, "docs", {}).values()
            if item.get("user_id") == user_id
        ]
        return max(tokens, default=0) + 1
    try:
        document = counters.find_one_and_update(
            {"user_id": user_id},
            {"$inc": {"fencing_token": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        document = counters.find_one_and_update(
            {"user_id": user_id},
            {"$inc": {"fencing_token": 1}},
            return_document=ReturnDocument.AFTER,
        )
    if document is None:
        raise RuntimeError("paper fencing token allocation failed")
    return int(document["fencing_token"])


def _mutation_identity(
    db: Any,
    user_id: str,
    mutation_id: str,
) -> tuple[str, int]:
    journal = db.paper_mutations.find_one(
        {"user_id": user_id, "mutation_id": mutation_id}
    )
    if journal is None:
        raise RuntimeError("paper mutation journal is missing")
    return str(journal["lease_owner"]), int(journal["fencing_token"])


def _position_fencing_plan(
    *,
    original_positions: dict[str, dict[str, Any]],
    final_positions: dict[str, dict[str, Any]],
    touched: set[str],
) -> dict[str, set[str]]:
    existing = touched.intersection(original_positions)
    new = touched.difference(original_positions)
    deleted = existing.difference(final_positions)
    updated = existing.intersection(final_positions)
    return {
        "existing": existing,
        "new": new,
        "delete": deleted,
        "update": updated,
    }


def _fencing_matches(
    document: dict[str, Any],
    owner: str,
    token: int,
) -> bool:
    return (
        document.get("mutation_lease_owner") == owner
        and int(document.get("mutation_fencing_token", -1)) == int(token)
    )


def _trade_fencing_allows(
    document: dict[str, Any] | None,
    token: int,
) -> bool:
    return document is None or int(document.get("fencing_token", -1)) <= int(
        token
    )


def _begin_account_mutation(
    db: Any,
    user_id: str,
    *,
    kind: str,
    expected_version: int | None,
    expected_updated_at: datetime | None,
    external_idempotency_key: str | None = None,
    lease_owner: str | None = None,
    lease_seconds: int = 300,
    reuse_mutation_id: str | None = None,
) -> str:
    recover_stale_pending_mutations(
        300,
        user_id=user_id,
        _db=db,
    )
    mutation_id = reuse_mutation_id or uuid.uuid4().hex
    owner = lease_owner or mutation_id
    fencing_token = _next_fencing_token(db, user_id)
    pre_snapshot = get_account_snapshot_atomic(
        user_id,
        as_of=_now(),
        _db=db,
    )
    pre_snapshot["account_version"] = int(expected_version or 0)
    _insert_archive_payload(db, pre_snapshot)
    pre_hash = hashlib.sha256(
        json.dumps(
            pre_snapshot, default=str, sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    started_at = _now()
    lease_expires_at = datetime.fromtimestamp(
        started_at.timestamp() + lease_seconds,
        tz=timezone.utc,
    )
    journal = {
            "user_id": user_id,
            "mutation_id": mutation_id,
            "type": kind,
            "status": "pending",
            "account_version": int(expected_version or 0),
            "pre_snapshot": pre_snapshot,
            "pre_snapshot_hash": pre_hash,
            "started_at": started_at,
            "lease_owner": owner,
            "lease_expires_at": lease_expires_at,
            "fencing_token": fencing_token,
            "completed_at": None,
            "error": None,
            "trade_ids": [],
        }
    if external_idempotency_key:
        journal["external_idempotency_key"] = external_idempotency_key
    if reuse_mutation_id is not None:
        reused = db.paper_mutations.find_one_and_update(
            {
                "user_id": user_id,
                "mutation_id": reuse_mutation_id,
                "status": {"$in": ["recovered", "aborted"]},
                "external_idempotency_key": external_idempotency_key,
            },
            {
                "$set": journal,
                "$unset": {
                    "result_payload": "",
                    "archive_payload": "",
                    "archive_hash": "",
                    "intended_account_version": "",
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if reused is None:
            raise RuntimeError("recoverable mutation claim was lost")
    else:
        try:
            db.paper_mutations.insert_one(journal)
        except DuplicateKeyError as exc:
            raise RuntimeError("external idempotency key conflict") from exc
    query: dict[str, Any] = {
        "user_id": user_id,
        "mutation_pending": {"$ne": True},
    }
    if expected_version is not None:
        query["account_version"] = expected_version
    elif expected_updated_at is not None:
        query["updated_at"] = expected_updated_at
    updated = db.paper_accounts.find_one_and_update(
        query,
        {
            "$set": {
                "mutation_pending": True,
                "mutation_id": mutation_id,
                "mutation_kind": kind,
                "mutation_started_at": started_at,
                "mutation_lease_owner": owner,
                "mutation_lease_expires_at": lease_expires_at,
                "mutation_fencing_token": fencing_token,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        db.paper_mutations.update_one(
            {"user_id": user_id, "mutation_id": mutation_id},
            {
                "$set": {
                    "status": "aborted",
                    "completed_at": _now(),
                    "error": "account CAS lock failed",
                }
            },
        )
        raise RuntimeError("account mutation conflict or pending mutation")
    return mutation_id


def _finish_account_mutation(
    db: Any,
    user_id: str,
    mutation_id: str,
    *,
    lease_owner: str,
    fencing_token: int,
) -> dict[str, Any]:
    completed = _now()
    account = db.paper_accounts.find_one(
        {
            "user_id": user_id,
            "mutation_pending": True,
            "mutation_id": mutation_id,
            "mutation_lease_owner": lease_owner,
            "mutation_fencing_token": fencing_token,
        }
    )
    if account is None:
        raise RuntimeError("account mutation ownership was lost")
    intended_version = int(account.get("account_version", 0)) + 1
    archive_payload = get_account_snapshot_atomic(
        user_id,
        as_of=completed,
        _db=db,
        _allow_pending_mutation_id=mutation_id,
    )
    archive_payload["account_version"] = intended_version
    archive_hash = hashlib.sha256(
        json.dumps(
            archive_payload,
            default=str,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    trade_ids = [
        str(item.get("_id"))
        for item in db.paper_trades.find(
            {"user_id": user_id, "mutation_id": mutation_id},
            {"_id": 1},
        )
    ]
    committing = db.paper_mutations.find_one_and_update(
        {
            "user_id": user_id,
            "mutation_id": mutation_id,
            "status": "pending",
            "lease_owner": lease_owner,
            "fencing_token": fencing_token,
        },
        {
            "$set": {
                "status": "committing",
                "intended_account_version": intended_version,
                "archive_payload": archive_payload,
                "archive_hash": archive_hash,
                "trade_ids": trade_ids,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if committing is None:
        raise RuntimeError("mutation journal committing transition failed")
    updated = db.paper_accounts.find_one_and_update(
        {
            "user_id": user_id,
            "mutation_pending": True,
            "mutation_id": mutation_id,
            "mutation_lease_owner": lease_owner,
            "mutation_fencing_token": fencing_token,
        },
        {
            "$set": {
                "mutation_pending": False,
                "latest_mutation_id": mutation_id,
                "mutation_completed_at": completed,
                "updated_at": completed,
                "account_version": intended_version,
            },
            "$unset": {
                "mutation_id": "",
                "mutation_kind": "",
                "mutation_started_at": "",
                "mutation_lease_owner": "",
                "mutation_lease_expires_at": "",
                "mutation_fencing_token": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise RuntimeError("account mutation ownership was lost")
    archiving = db.paper_mutations.find_one_and_update(
        {
            "user_id": user_id,
            "mutation_id": mutation_id,
            "status": "committing",
            "lease_owner": lease_owner,
            "fencing_token": fencing_token,
        },
        {
            "$set": {
                "status": "archiving",
                "account_version": intended_version,
                "error": None,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if archiving is None:
        raise RuntimeError("mutation journal archiving transition failed")
    _insert_archive_payload(db, archive_payload)
    journal = db.paper_mutations.find_one_and_update(
        {
            "user_id": user_id,
            "mutation_id": mutation_id,
            "status": "archiving",
            "archive_hash": archive_hash,
            "lease_owner": lease_owner,
            "fencing_token": fencing_token,
        },
        {
            "$set": {
                "status": "completed",
                "completed_at": completed,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if journal is None:
        raise RuntimeError("mutation journal completion failed")
    return updated


def _archive_account_snapshot(
    db: Any,
    user_id: str,
    account_version: int,
) -> None:
    snapshot = get_account_snapshot_atomic(
        user_id,
        as_of=_now(),
        _db=db,
    )
    document = {
        **snapshot,
        "account_version": account_version,
    }
    _insert_archive_payload(db, document)


def _insert_archive_payload(db: Any, document: dict[str, Any]) -> None:
    try:
        db.paper_account_snapshots.insert_one(document)
    except DuplicateKeyError:
        existing = db.paper_account_snapshots.find_one(
            {
                "user_id": document["user_id"],
                "data_as_of": document["data_as_of"],
                "account_version": document["account_version"],
            },
            {"_id": 0},
        )
        if existing is None:
            raise


def recover_pending_account_mutation(
    user_id: str,
    mutation_id: str,
    *,
    _db: Any | None = None,
    recovery_owner: str | None = None,
) -> dict[str, Any]:
    """Rollback a stuck fallback mutation to its latest immutable snapshot."""
    db = _db if _db is not None else get_db()
    journal = db.paper_mutations.find_one(
        {"user_id": user_id, "mutation_id": mutation_id}
    )
    if journal is None:
        raise ValueError("pending mutation journal was not found")
    current_owner = journal.get("lease_owner")
    current_token = int(journal.get("fencing_token", 0))
    lease_expiry = journal.get("lease_expires_at")
    if isinstance(lease_expiry, datetime):
        if lease_expiry.tzinfo is None:
            lease_expiry = lease_expiry.replace(tzinfo=timezone.utc)
        if lease_expiry > _now() and journal.get("lease_owner") != recovery_owner:
            raise RuntimeError("active mutation lease cannot be recovered")
    account = db.paper_accounts.find_one({"user_id": user_id})
    intended = journal.get("intended_account_version")
    if (
        journal.get("status") == "completed"
        and account is not None
        and account.get("latest_mutation_id") == mutation_id
        and int(account.get("account_version", -1))
        == int(journal.get("account_version", -2))
    ):
        return account
    if (
        journal.get("status") in {"committing", "archiving"}
        and account is not None
        and not account.get("mutation_pending")
        and account.get("latest_mutation_id") == mutation_id
        and int(account.get("account_version", -1)) == int(intended)
    ):
        payload = journal.get("archive_payload")
        if not isinstance(payload, dict):
            raise RuntimeError("committing journal archive payload is missing")
        payload_hash = hashlib.sha256(
            json.dumps(
                payload, default=str, sort_keys=True
            ).encode("utf-8")
        ).hexdigest()
        if payload_hash != journal.get("archive_hash"):
            raise RuntimeError("committing journal archive hash mismatch")
        _insert_archive_payload(db, payload)
        db.paper_mutations.update_one(
            {
                "user_id": user_id,
                "mutation_id": mutation_id,
                "lease_owner": current_owner,
                "fencing_token": current_token,
            },
            {
                "$set": {
                    "status": "completed",
                    "account_version": int(intended),
                    "completed_at": _now(),
                    "error": None,
                }
            },
        )
        return account
    if journal.get("status") == "pending" and (
        account is None
        or not account.get("mutation_pending")
        or account.get("mutation_id") != mutation_id
    ):
        pre_version = int(journal.get("account_version", 0))
        current_version = (
            -1 if account is None else int(account.get("account_version", 0))
        )
        if account is not None and current_version == pre_version:
            db.paper_mutations.update_one(
                {
                    "user_id": user_id,
                    "mutation_id": mutation_id,
                    "lease_owner": current_owner,
                    "fencing_token": current_token,
                },
                {
                    "$set": {
                        "status": "aborted",
                        "completed_at": _now(),
                        "error": "lock_not_acquired/crash_before_cas",
                    }
                },
            )
            return account
        db.paper_mutations.update_one(
            {
                "user_id": user_id,
                "mutation_id": mutation_id,
                "lease_owner": current_owner,
                "fencing_token": current_token,
            },
            {
                "$set": {
                    "status": "conflict",
                    "completed_at": _now(),
                    "error": (
                        "pending journal account version changed "
                        f"from {pre_version} to {current_version}"
                    ),
                }
            },
        )
        raise RuntimeError("pending mutation journal conflicts with account version")
    if (
        account is None
        or not account.get("mutation_pending")
        or account.get("mutation_id") != mutation_id
    ):
        raise ValueError("pending mutation was not found")
    archived = db.paper_account_snapshots.find_one(
        {
            "user_id": user_id,
            "account_version": {
                "$lte": int(account.get("account_version", 0))
            },
        },
        {"_id": 0},
        sort=[("account_version", -1), ("data_as_of", -1)],
    )
    if archived is None:
        raise RuntimeError("pending mutation has no rollback snapshot")
    db.paper_positions.delete_many({"user_id": user_id})
    for item in archived.get("positions") or []:
        db.paper_positions.insert_one(
            {
                "user_id": user_id,
                "symbol": item["symbol"],
                "qty": float(item["quantity"]),
                "cost": float(item["cost"]),
                "last": float(item["last_price"]),
                "marked_at": item["price_as_of"],
                "updated_at": archived["data_as_of"],
            }
        )
    db.paper_trades.update_many(
        {"user_id": user_id, "mutation_id": mutation_id},
        {
            "$set": {
                "voided": True,
                "void_reason": "mutation_recovery",
                "voided_at": _now(),
            }
        },
    )
    recovered_id = f"recovery:{mutation_id}"
    updated = db.paper_accounts.find_one_and_update(
        {
            "user_id": user_id,
            "mutation_pending": True,
            "mutation_id": mutation_id,
            "mutation_lease_owner": current_owner,
            "mutation_fencing_token": current_token,
        },
        {
            "$set": {
                "cash": float(archived["cash"]),
                "mutation_pending": False,
                "latest_mutation_id": recovered_id,
                "updated_at": _now(),
            },
            "$unset": {
                "mutation_id": "",
                "mutation_kind": "",
                "mutation_started_at": "",
                "mutation_lease_owner": "",
                "mutation_lease_expires_at": "",
            },
            "$inc": {"account_version": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise RuntimeError("pending mutation changed during recovery")
    completed_at = _now()
    db.paper_mutations.update_one(
        {
            "user_id": user_id,
            "mutation_id": mutation_id,
            "lease_owner": current_owner,
            "fencing_token": current_token,
        },
        {
            "$set": {
                "status": "recovered",
                "completed_at": completed_at,
                "error": "rolled back by recovery",
            }
        },
    )
    db.paper_mutations.update_one(
        {"user_id": user_id, "mutation_id": recovered_id},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "mutation_id": recovered_id,
                "type": "recovery",
                "status": "completed",
                "account_version": int(updated["account_version"]),
                "started_at": completed_at,
                "completed_at": completed_at,
                "error": None,
                "trade_ids": [],
                "recovered_mutation_id": mutation_id,
            }
        },
        upsert=True,
    )
    recovery_archive = {
        **archived,
        "account_version": int(updated["account_version"]),
        "version": str(updated["account_version"]),
        "latest_mutation_id": recovered_id,
        "data_as_of": completed_at,
    }
    _insert_archive_payload(db, recovery_archive)
    return updated


def recover_stale_pending_mutations(
    max_age_seconds: float,
    user_id: str | None = None,
    *,
    _db: Any | None = None,
) -> list[str]:
    """Recover only pending mutations older than the explicit age threshold."""
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    db = _db if _db is not None else get_db()
    cutoff = datetime.fromtimestamp(
        _now().timestamp() - max_age_seconds,
        tz=timezone.utc,
    )
    query: dict[str, Any] = {
        "status": {"$in": ["pending", "committing", "archiving"]},
        "started_at": {"$lte": cutoff},
        "$or": [
            {"lease_expires_at": {"$lte": _now()}},
            {"lease_expires_at": None},
        ],
    }
    if user_id is not None:
        query["user_id"] = user_id
    recovered = []
    for journal in db.paper_mutations.find(query):
        mutation_id = journal.get("mutation_id")
        if not mutation_id:
            continue
        recovery_owner = f"recovery:{uuid.uuid4().hex}"
        old_token = int(journal.get("fencing_token", 0))
        recovery_token = _next_fencing_token(
            db, str(journal["user_id"])
        )
        claimed = db.paper_mutations.find_one_and_update(
            {
                "user_id": journal["user_id"],
                "mutation_id": mutation_id,
                "status": journal.get("status"),
                "lease_owner": journal.get("lease_owner"),
                "lease_expires_at": journal.get("lease_expires_at"),
                "fencing_token": old_token,
            },
            {
                "$set": {
                    "lease_owner": recovery_owner,
                    "lease_expires_at": datetime.fromtimestamp(
                        _now().timestamp() + 300,
                        tz=timezone.utc,
                    ),
                    "fencing_token": recovery_token,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if claimed is None:
            continue
        db.paper_accounts.update_one(
            {
                "user_id": journal["user_id"],
                "mutation_id": mutation_id,
                "mutation_lease_owner": journal.get("lease_owner"),
                "mutation_fencing_token": old_token,
            },
            {
                "$set": {
                    "mutation_lease_owner": recovery_owner,
                    "mutation_fencing_token": recovery_token,
                }
            },
        )
        db.paper_positions.update_many(
            {
                "user_id": journal["user_id"],
                "mutation_id": mutation_id,
                "mutation_lease_owner": journal.get("lease_owner"),
                "mutation_fencing_token": old_token,
            },
            {
                "$set": {
                    "mutation_lease_owner": recovery_owner,
                    "mutation_fencing_token": recovery_token,
                }
            },
        )
        recover_pending_account_mutation(
            str(journal["user_id"]),
            str(mutation_id),
            _db=db,
            recovery_owner=recovery_owner,
        )
        recovered.append(str(mutation_id))
    account_query: dict[str, Any] = {
        "mutation_pending": True,
        "mutation_started_at": {"$lte": cutoff},
        "$or": [
            {"mutation_lease_expires_at": {"$lte": _now()}},
            {"mutation_lease_expires_at": None},
        ],
    }
    if user_id is not None:
        account_query["user_id"] = user_id
    for account in db.paper_accounts.find(account_query):
        mutation_id = str(account.get("mutation_id") or "")
        if not mutation_id or mutation_id in recovered:
            continue
        journal = db.paper_mutations.find_one(
            {
                "user_id": account["user_id"],
                "mutation_id": mutation_id,
            }
        )
        if journal is not None:
            continue
        archived = db.paper_account_snapshots.find_one(
            {
                "user_id": account["user_id"],
                "account_version": {
                    "$lte": int(account.get("account_version", 0))
                },
            },
            {"_id": 0},
            sort=[("account_version", -1), ("data_as_of", -1)],
        )
        if archived is None:
            continue
        db.paper_mutations.insert_one(
            {
                "user_id": account["user_id"],
                "mutation_id": mutation_id,
                "type": "orphan_recovery",
                "status": "pending",
                "account_version": int(account.get("account_version", 0)),
                "pre_snapshot": archived,
                "started_at": account["mutation_started_at"],
                "error": "orphan pending account recovered",
                "trade_ids": [],
            }
        )
        recover_pending_account_mutation(
            str(account["user_id"]),
            mutation_id,
            _db=db,
        )
        recovered.append(mutation_id)
    return recovered


def _validate_account_journal_for_read(
    db: Any,
    user_id: str,
    account: dict[str, Any],
) -> dict[str, Any]:
    mutation_id = account.get("latest_mutation_id")
    if not mutation_id:
        if int(account.get("account_version", 0)) != 0:
            raise RuntimeError("versioned account has no mutation journal")
        return account
    journal = db.paper_mutations.find_one(
        {"user_id": user_id, "mutation_id": mutation_id},
        {"_id": 0},
    )
    if journal is None:
        raise RuntimeError("account mutation journal is missing")
    status = journal.get("status")
    if status in {"committing", "archiving"}:
        started = journal.get("started_at")
        if (
            started is not None
            and (_now() - started).total_seconds() >= 300
        ):
            recover_stale_pending_mutations(
                300, user_id=user_id, _db=db
            )
            refreshed = db.paper_accounts.find_one({"user_id": user_id})
            if refreshed is None:
                raise RuntimeError("account disappeared during recovery")
            return _validate_account_journal_for_read(
                db, user_id, refreshed
            )
        raise RuntimeError(
            "account mutation archive durability is not completed"
        )
    if (
        status != "completed"
        or int(journal.get("account_version", -1))
        != int(account.get("account_version", 0))
    ):
        raise RuntimeError("account mutation journal/version mismatch")
    archived = db.paper_account_snapshots.find_one(
        {
            "user_id": user_id,
            "account_version": int(account.get("account_version", 0)),
        },
        {"_id": 0},
        sort=[("data_as_of", -1)],
    )
    if archived is None:
        raise RuntimeError("completed mutation archive is missing")
    expected_hash = journal.get("archive_hash")
    if expected_hash:
        actual_hash = hashlib.sha256(
            json.dumps(
                archived, default=str, sort_keys=True
            ).encode("utf-8")
        ).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError("completed mutation archive hash mismatch")
    return account


class PaperResetBody(BaseModel):
    cash: float = Field(default=100_000, gt=0)


class PaperOrderBody(BaseModel):
    model_config = {"extra": "forbid"}

    symbol: str
    side: Literal["buy", "sell"]
    qty: float = Field(..., gt=0)
    price: float | None = Field(default=None, gt=0)
    name: str | None = None
    asset_type: Literal["etf", "stock"] | None = None
    quote_price: float | None = Field(default=None, gt=0)
    executed_price: float | None = Field(default=None, gt=0)
    gross_amount: float | None = Field(default=None, gt=0)
    commission: float | None = Field(default=None, ge=0)
    stamp_tax: float | None = Field(default=None, ge=0)
    slippage: float | None = Field(default=None, ge=0)
    total_fees: float | None = Field(default=None, ge=0)
    net_cash: float | None = None
    market_status_hash: str | None = None
    market_status_expires_at: str | None = None


def _now():
    return datetime.now(timezone.utc)


@contextmanager
def _transaction_scope(db: Any):
    """Use a real Mongo transaction only on replica-set deployments."""
    try:
        hello = db.command("hello")
        client = db.client
    except (AttributeError, RuntimeError, PyMongoError):
        yield None
        return
    if not hello.get("setName"):
        yield None
        return
    with client.start_session() as session:
        with session.start_transaction():
            yield session


def _json_safe(obj: Any) -> Any:
    """Recursively convert datetime / ObjectId for JSON / SSE."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    # bson ObjectId
    if type(obj).__name__ == "ObjectId":
        return str(obj)
    return obj


def _latest_price(symbol: str) -> tuple[str, float]:
    name, df = fetch_daily_df(symbol)
    if df is None or df.empty:
        raise ValueError(f"无法获取价格: {symbol}")
    return name, float(df.iloc[-1]["close"])


def get_account(user_id: str, *, mark_to_market: bool = False) -> dict[str, Any]:
    """默认读库缓存价（last），不实时拉行情；mark_to_market=True 才逐只取最新价。"""
    db = get_db()
    acc = db.paper_accounts.find_one({"user_id": user_id})
    if acc and acc.get("mutation_pending"):
        started = acc.get("mutation_started_at")
        if (
            started is not None
            and (_now() - started).total_seconds() >= 300
        ):
            recover_stale_pending_mutations(
                300, user_id=user_id, _db=db
            )
            acc = db.paper_accounts.find_one({"user_id": user_id})
        if acc and acc.get("mutation_pending"):
            raise RuntimeError("account mutation is pending")
    if not acc:
        db.paper_accounts.insert_one(
            {
                "user_id": user_id,
                "cash": 100_000.0,
                "initial_cash": 100_000.0,
                "account_version": 0,
                "mutation_pending": False,
                "updated_at": _now(),
            }
        )
        acc = db.paper_accounts.find_one({"user_id": user_id})
    acc = _validate_account_journal_for_read(db, user_id, acc)
    positions = list(db.paper_positions.find({"user_id": user_id}, {"_id": 0}))
    total_mv = 0.0
    enriched = []
    for p in positions:
        cost = float(p.get("cost") or 0)
        qty = float(p.get("qty") or 0)
        if mark_to_market:
            try:
                _, px = _latest_price(p["symbol"])
            except Exception:
                px = float(p.get("last") or cost or 0)
        else:
            px = float(p.get("last") or cost or 0)
        mv = px * qty
        total_mv += mv
        row = {
            **p,
            "last": px,
            "market_value": round(mv, 2),
            "pnl": round((px - cost) * qty, 2) if cost else 0.0,
            "pnl_pct": None if not cost else round(px / cost - 1, 6),
            "marked": bool(p.get("last") is not None) if not mark_to_market else True,
        }
        if hasattr(row.get("updated_at"), "isoformat"):
            row["updated_at"] = row["updated_at"].isoformat()
        if hasattr(row.get("marked_at"), "isoformat"):
            row["marked_at"] = row["marked_at"].isoformat()
        enriched.append(row)
    cash = float(acc["cash"])
    return {
        "cash": round(cash, 2),
        "initial_cash": float(acc.get("initial_cash") or cash),
        "market_value": round(total_mv, 2),
        "equity": round(cash + total_mv, 2),
        "positions": enriched,
        "mark_to_market": mark_to_market,
    }


def iter_mark_to_market_events(user_id: str):
    """SSE：逐只刷新现价/市值/浮盈亏，并写回 paper_positions.last。"""
    db = get_db()
    account_doc = db.paper_accounts.find_one({"user_id": user_id})
    if account_doc is None:
        get_account(user_id, mark_to_market=False)
        account_doc = db.paper_accounts.find_one({"user_id": user_id})
    mutation_id = _begin_account_mutation(
        db,
        user_id,
        kind="mark_to_market",
        expected_version=account_doc.get("account_version"),
        expected_updated_at=account_doc.get("updated_at"),
    )
    positions = list(db.paper_positions.find({"user_id": user_id}))
    yield {
        "event": "meta",
        "data": {"total": len(positions)},
    }
    if not positions:
        owner, token = _mutation_identity(db, user_id, mutation_id)
        _finish_account_mutation(
            db,
            user_id,
            mutation_id,
            lease_owner=owner,
            fencing_token=token,
        )
        yield {
            "event": "done",
            "data": {"account": get_account(user_id, mark_to_market=False)},
        }
        return

    for i, pos in enumerate(positions):
        sym = pos["symbol"]
        cost = float(pos.get("cost") or 0)
        qty = float(pos.get("qty") or 0)
        try:
            name, px = _latest_price(sym)
            err = None
        except Exception as exc:
            name = pos.get("name") or sym
            px = float(pos.get("last") or cost or 0)
            err = str(exc)
        now = _now()
        db.paper_positions.update_one(
            {"_id": pos["_id"]},
            {
                "$set": {
                    "last": px,
                    "name": pos.get("name") or name,
                    "marked_at": now,
                    "updated_at": now,
                }
            },
        )
        mv = round(px * qty, 2)
        yield {
            "event": "position",
            "data": {
                "index": i,
                "done": i + 1,
                "total": len(positions),
                "symbol": sym,
                "name": pos.get("name") or name,
                "qty": qty,
                "cost": cost,
                "last": px,
                "market_value": mv,
                "pnl": round((px - cost) * qty, 2) if cost else 0.0,
                "pnl_pct": None if not cost else round(px / cost - 1, 6),
                "error": err,
            },
        }

    owner, token = _mutation_identity(db, user_id, mutation_id)
    _finish_account_mutation(
        db,
        user_id,
        mutation_id,
        lease_owner=owner,
        fencing_token=token,
    )
    yield {
        "event": "done",
        "data": {"account": get_account(user_id, mark_to_market=False)},
    }


def reset_account(user_id: str, cash: float) -> dict[str, Any]:
    db = get_db()
    db.paper_accounts.update_one(
        {"user_id": user_id},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "cash": 0.0,
                "initial_cash": 0.0,
                "account_version": 0,
                "mutation_pending": False,
                "updated_at": _now(),
            }
        },
        upsert=True,
    )
    account = db.paper_accounts.find_one({"user_id": user_id})
    mutation_id = _begin_account_mutation(
        db,
        user_id,
        kind="reset",
        expected_version=account.get("account_version"),
        expected_updated_at=account.get("updated_at"),
    )
    db.paper_accounts.update_one(
        {"user_id": user_id, "mutation_id": mutation_id},
        {
            "$set": {
                "cash": float(cash),
                "initial_cash": float(cash),
            }
        },
    )
    db.paper_positions.delete_many({"user_id": user_id})
    # keep trade history; optional: mark reset event
    db.paper_trades.insert_one(
        {
            "user_id": user_id,
            "side": "reset",
            "symbol": "",
            "qty": 0,
            "price": 0,
            "amount": float(cash),
            "source": "reset",
            "mutation_id": mutation_id,
            "created_at": _now(),
        }
    )
    owner, token = _mutation_identity(db, user_id, mutation_id)
    _finish_account_mutation(
        db,
        user_id,
        mutation_id,
        lease_owner=owner,
        fencing_token=token,
    )
    return get_account(user_id)


def place_order(
    user_id: str,
    body: PaperOrderBody,
    *,
    source: str = "manual",
    rec_date: str | None = None,
    mark_to_market: bool = False,
    external_idempotency_key: str | None = None,
    mutation_source: str | None = None,
) -> dict[str, Any]:
    symbol = normalize_symbol(body.symbol)
    qty = float(body.qty)
    if qty <= 0:
        raise ValueError("数量必须大于 0")
    # round down to lot for buys
    if body.side == "buy":
        qty = (qty // LOT) * LOT
        if qty < LOT:
            raise ValueError(f"买入数量至少 {LOT} 股")

    db = get_db()
    if external_idempotency_key:
        existing = db.paper_trades.find_one(
            {
                "user_id": user_id,
                "external_idempotency_key": external_idempotency_key,
                "voided": {"$ne": True},
            },
            {"_id": 0},
        )
        if existing is not None:
            trade = dict(existing)
            if hasattr(trade.get("created_at"), "isoformat"):
                trade["created_at"] = trade["created_at"].isoformat()
            return {
                "trade": trade,
                "account": get_account(
                    user_id,
                    mark_to_market=mark_to_market,
                ),
            }
    # Prefer caller-provided price/name to avoid slow kline fetch
    if body.price is not None and body.name:
        name, px = body.name, float(body.price)
    elif body.price is not None:
        try:
            name, _ = _latest_price(symbol)
        except Exception:
            name = body.name or symbol
        px = float(body.price)
    else:
        name, px = _latest_price(symbol)
        if body.name:
            name = body.name

    acc = db.paper_accounts.find_one({"user_id": user_id})
    if not acc:
        reset_account(user_id, 100_000)
        acc = db.paper_accounts.find_one({"user_id": user_id})
    cash = float(acc["cash"])
    pos = db.paper_positions.find_one({"user_id": user_id, "symbol": symbol})

    amount = px * qty
    if body.side == "buy" and amount > cash + 1e-6:
        raise ValueError(f"余额不足：需要 {amount:.2f}，可用 {cash:.2f}")
    if body.side == "sell" and (
        not pos or float(pos["qty"]) < qty - 1e-9
    ):
        raise ValueError("持仓不足，无法卖出")
    mutation_id = _begin_account_mutation(
        db,
        user_id,
        kind=mutation_source or f"order:{body.side}",
        expected_version=acc.get("account_version"),
        expected_updated_at=acc.get("updated_at"),
    )
    if body.side == "buy":
        new_cash = cash - amount
        if pos:
            old_qty = float(pos["qty"])
            old_cost = float(pos["cost"])
            new_qty = old_qty + qty
            new_cost = (old_cost * old_qty + px * qty) / new_qty
            db.paper_positions.update_one(
                {"_id": pos["_id"]},
                {
                    "$set": {
                        "qty": new_qty,
                        "cost": new_cost,
                        "name": name,
                        "last": px,
                        "marked_at": _now(),
                        "updated_at": _now(),
                    }
                },
            )
        else:
            db.paper_positions.insert_one(
                {
                    "user_id": user_id,
                    "symbol": symbol,
                    "name": name,
                    "qty": qty,
                    "cost": px,
                    "last": px,
                    "marked_at": _now(),
                    "updated_at": _now(),
                }
            )
    else:
        new_cash = cash + amount
        left = float(pos["qty"]) - qty
        if left <= 1e-9:
            db.paper_positions.delete_one({"_id": pos["_id"]})
        else:
            db.paper_positions.update_one(
                {"_id": pos["_id"]},
                {
                    "$set": {
                        "qty": left,
                        "last": px,
                        "marked_at": _now(),
                        "updated_at": _now(),
                    }
                },
            )

    db.paper_accounts.update_one(
        {"user_id": user_id},
        {"$set": {"cash": new_cash, "updated_at": _now()}},
    )
    trade = {
        "user_id": user_id,
        "symbol": symbol,
        "name": name,
        "side": body.side,
        "qty": qty,
        "price": px,
        "amount": round(amount, 2),
        "source": mutation_source or source,
        "rec_date": rec_date,
        "mutation_id": mutation_id,
        "created_at": _now(),
    }
    if external_idempotency_key:
        trade["external_idempotency_key"] = external_idempotency_key
    db.paper_trades.insert_one(trade)
    owner, token = _mutation_identity(db, user_id, mutation_id)
    _finish_account_mutation(
        db,
        user_id,
        mutation_id,
        lease_owner=owner,
        fencing_token=token,
    )
    trade.pop("_id", None)
    return {
        "trade": {**trade, "created_at": trade["created_at"].isoformat()},
        "account": get_account(user_id, mark_to_market=mark_to_market),
    }


def place_orders_atomic(
    *,
    user_id: str,
    orders: list[dict[str, Any]],
    external_idempotency_key: str,
    mutation_source: str,
    expected_account_version: int,
    lease_owner: str,
    lease_renew: Any | None = None,
) -> dict[str, Any]:
    """Execute a committee portfolio as one recoverable account mutation."""
    if not external_idempotency_key:
        raise ValueError("external idempotency key is required")
    if not orders:
        raise ValueError("orders cannot be empty")
    db = get_db()
    existing = db.paper_mutations.find_one(
        {
            "user_id": user_id,
            "external_idempotency_key": external_idempotency_key,
        }
    )
    reuse_mutation_id: str | None = None
    recovered_retry = False
    if existing is not None:
        status = existing.get("status")
        mutation_id = str(existing["mutation_id"])
        if status in {"pending", "committing", "archiving"}:
            lease_expiry = existing.get("lease_expires_at")
            if isinstance(lease_expiry, datetime):
                if lease_expiry.tzinfo is None:
                    lease_expiry = lease_expiry.replace(tzinfo=timezone.utc)
                if lease_expiry > _now():
                    raise RuntimeError("approval mutation lease is active")
            recovery_owner = f"recovery:{uuid.uuid4().hex}"
            old_token = int(existing.get("fencing_token", 0))
            recovery_token = _next_fencing_token(db, user_id)
            claimed = db.paper_mutations.find_one_and_update(
                {
                    "user_id": user_id,
                    "mutation_id": mutation_id,
                    "status": status,
                    "lease_owner": existing.get("lease_owner"),
                    "lease_expires_at": existing.get("lease_expires_at"),
                    "fencing_token": old_token,
                },
                {
                    "$set": {
                        "lease_owner": recovery_owner,
                        "lease_expires_at": datetime.fromtimestamp(
                            _now().timestamp() + 300,
                            tz=timezone.utc,
                        ),
                        "fencing_token": recovery_token,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if claimed is None:
                raise RuntimeError("approval mutation recovery claim lost")
            account_claim = db.paper_accounts.update_one(
                {
                    "user_id": user_id,
                    "mutation_id": mutation_id,
                    "mutation_lease_owner": existing.get("lease_owner"),
                    "mutation_fencing_token": old_token,
                },
                {
                    "$set": {
                        "mutation_lease_owner": recovery_owner,
                        "mutation_fencing_token": recovery_token,
                    }
                },
            )
            if getattr(account_claim, "matched_count", 1) == 0:
                raise RuntimeError("account fencing takeover failed")
            db.paper_positions.update_many(
                {
                    "user_id": user_id,
                    "mutation_id": mutation_id,
                    "mutation_lease_owner": existing.get("lease_owner"),
                    "mutation_fencing_token": old_token,
                },
                {
                    "$set": {
                        "mutation_lease_owner": recovery_owner,
                        "mutation_fencing_token": recovery_token,
                    }
                },
            )
            recover_pending_account_mutation(
                user_id,
                mutation_id,
                _db=db,
                recovery_owner=recovery_owner,
            )
            existing = db.paper_mutations.find_one(
                {"user_id": user_id, "mutation_id": mutation_id}
            )
            status = existing.get("status") if existing else None
        if status == "completed":
            result = existing.get("result_payload")
            if result is not None:
                return _json_safe(result)
            trades = list(
                db.paper_trades.find(
                    {
                        "user_id": user_id,
                        "mutation_id": mutation_id,
                        "voided": {"$ne": True},
                    },
                    {"_id": 0},
                ).sort("created_at", 1)
            )
            result = {
                "trades": _json_safe(trades),
                "account": get_account(user_id, mark_to_market=False),
                "mutation_id": mutation_id,
            }
            db.paper_mutations.update_one(
                {"user_id": user_id, "mutation_id": mutation_id},
                {"$set": {"result_payload": result}},
            )
            return result
        if status not in {"recovered", "aborted"}:
            raise RuntimeError("prior approval mutation is not recoverable")
        reuse_mutation_id = mutation_id
        recovered_retry = status == "recovered"

    account = db.paper_accounts.find_one({"user_id": user_id})
    if account is None:
        reset_account(user_id, 100_000)
        account = db.paper_accounts.find_one({"user_id": user_id})
    current_account_version = int(account.get("account_version", -1))
    recovery_matches_preview = (
        recovered_retry
        and existing is not None
        and int(existing.get("account_version", -1))
        == int(expected_account_version)
        and account.get("latest_mutation_id")
        == f"recovery:{reuse_mutation_id}"
    )
    if (
        current_account_version != int(expected_account_version)
        and not recovery_matches_preview
    ):
        raise RuntimeError("account version changed since approval preview")
    if lease_renew is not None:
        lease_renew()
    cash = float(account["cash"])
    positions = {
        str(item["symbol"]): dict(item)
        for item in db.paper_positions.find({"user_id": user_id})
    }
    original_positions = {
        symbol: dict(item) for symbol, item in positions.items()
    }
    prepared: list[dict[str, Any]] = []
    simulated_cash = cash
    for index, raw in enumerate(orders):
        body = PaperOrderBody.model_validate(raw)
        symbol = normalize_symbol(body.symbol)
        qty = float(body.qty)
        if body.side == "buy":
            qty = (qty // LOT) * LOT
            if qty < LOT:
                raise ValueError(f"买入数量至少 {LOT} 股")
        if body.price is None:
            name, price = _latest_price(symbol)
        else:
            name = body.name or symbol
            price = float(body.price)
        bound_execution = body.gross_amount is not None
        amount = (
            float(body.gross_amount)
            if bound_execution
            else price * qty
        )
        if bound_execution:
            required = (
                body.quote_price,
                body.executed_price,
                body.commission,
                body.stamp_tax,
                body.slippage,
                body.total_fees,
                body.net_cash,
                body.asset_type,
            )
            if any(value is None for value in required):
                raise ValueError("bound execution cost fields are incomplete")
            if abs(price - float(body.executed_price)) > 1e-9:
                raise ValueError("bound executed price mismatch")
            if abs(amount - price * qty) > 1e-6:
                raise ValueError("bound gross amount mismatch")
            if body.market_status_hash:
                if not body.market_status_expires_at:
                    raise ValueError("market status expiry is required")
                expires_at = datetime.fromisoformat(
                    body.market_status_expires_at.replace("Z", "+00:00")
                )
                if expires_at <= _now():
                    raise ValueError("market status validation expired")
                from .committee.routes import _current_market_status

                latest_status = _current_market_status(symbol)
                if (
                    bool(latest_status["suspended"])
                    or float(latest_status["volume"]) <= 0
                ):
                    raise ValueError("symbol is suspended or has no volume")
                if (
                    body.side == "buy"
                    and latest_status["limit_up"]
                    and latest_status["locked"]
                ):
                    raise ValueError("buy is blocked by locked limit-up")
                if (
                    body.side == "sell"
                    and latest_status["limit_down"]
                    and latest_status["locked"]
                ):
                    raise ValueError("sell is blocked by locked limit-down")
        position = positions.get(symbol)
        if body.side == "buy":
            cash_delta = (
                float(body.net_cash) if bound_execution else -amount
            )
            if -cash_delta > simulated_cash + 1e-6:
                raise ValueError("组合审批现金不足")
            simulated_cash += cash_delta
            old_qty = float(position.get("qty") or 0) if position else 0.0
            old_cost = float(position.get("cost") or 0) if position else 0.0
            new_qty = old_qty + qty
            positions[symbol] = {
                **(position or {}),
                "user_id": user_id,
                "symbol": symbol,
                "name": name,
                "qty": new_qty,
                "cost": (
                    (
                        old_cost * old_qty
                        + (
                            -cash_delta
                            if bound_execution
                            else amount
                        )
                    )
                    / new_qty
                    if new_qty
                    else price
                ),
                "last": price,
            }
        else:
            held = float(position.get("qty") or 0) if position else 0.0
            if held + 1e-9 < qty:
                raise ValueError("组合审批持仓不足")
            simulated_cash += (
                float(body.net_cash) if bound_execution else amount
            )
            left = held - qty
            if left <= 1e-9:
                positions.pop(symbol, None)
            else:
                positions[symbol] = {
                    **position,
                    "qty": left,
                    "last": price,
                }
        prepared_item = {
                "index": index,
                "symbol": symbol,
                "name": name,
                "side": body.side,
                "qty": qty,
                "price": price,
                "amount": round(amount, 2),
            }
        if bound_execution:
            prepared_item.update(
                {
                    field: getattr(body, field)
                    for field in (
                        "asset_type",
                        "quote_price",
                        "executed_price",
                        "gross_amount",
                        "commission",
                        "stamp_tax",
                        "slippage",
                        "total_fees",
                        "net_cash",
                        "market_status_hash",
                        "market_status_expires_at",
                    )
                }
            )
        prepared.append(prepared_item)

    mutation_id = _begin_account_mutation(
        db,
        user_id,
        kind=mutation_source,
        expected_version=current_account_version,
        expected_updated_at=account.get("updated_at"),
        external_idempotency_key=external_idempotency_key,
        lease_owner=lease_owner,
        reuse_mutation_id=reuse_mutation_id,
    )
    claimed_owner, fencing_token = _mutation_identity(
        db, user_id, mutation_id
    )
    if claimed_owner != lease_owner:
        raise RuntimeError("paper mutation owner mismatch")

    def renew_mutation_lease() -> None:
        now = _now()
        renewed = db.paper_mutations.find_one_and_update(
            {
                "user_id": user_id,
                "mutation_id": mutation_id,
                "status": {"$in": ["pending", "committing", "archiving"]},
                "lease_owner": lease_owner,
                "fencing_token": fencing_token,
            },
            {
                "$set": {
                    "lease_expires_at": datetime.fromtimestamp(
                        now.timestamp() + 300,
                        tz=timezone.utc,
                    )
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if renewed is None:
            raise RuntimeError("paper mutation lease was lost")

    try:
        if lease_renew is not None:
            lease_renew()
        renew_mutation_lease()
        touched = {item["symbol"] for item in prepared}
        position_plan = _position_fencing_plan(
            original_positions=original_positions,
            final_positions=positions,
            touched=touched,
        )
        now = _now()
        for symbol in position_plan["existing"]:
            marked = db.paper_positions.update_one(
                {"user_id": user_id, "symbol": symbol},
                {
                    "$set": {
                        "mutation_id": mutation_id,
                        "mutation_lease_owner": lease_owner,
                        "mutation_fencing_token": fencing_token,
                    }
                },
            )
            if getattr(marked, "matched_count", 1) == 0:
                raise RuntimeError("position fencing claim failed")
        trades = []
        with _transaction_scope(db) as session:
            session_args = {} if session is None else {"session": session}
            for symbol in touched:
                if lease_renew is not None:
                    lease_renew()
                renew_mutation_lease()
                value = positions.get(symbol)
                if value is None:
                    deleted = db.paper_positions.delete_one(
                        {
                            "user_id": user_id,
                            "symbol": symbol,
                            "mutation_id": mutation_id,
                            "mutation_lease_owner": lease_owner,
                            "mutation_fencing_token": fencing_token,
                        },
                        **session_args,
                    )
                    if getattr(deleted, "deleted_count", 1) == 0:
                        raise RuntimeError("position fencing token rejected")
                    continue
                position_query: dict[str, Any] = {
                    "user_id": user_id,
                    "symbol": symbol,
                }
                if symbol in position_plan["new"]:
                    position_query["mutation_fencing_token"] = {
                        "$exists": False
                    }
                else:
                    position_query.update(
                        {
                            "mutation_id": mutation_id,
                            "mutation_lease_owner": lease_owner,
                            "mutation_fencing_token": fencing_token,
                        }
                    )
                position_write = db.paper_positions.update_one(
                    position_query,
                    {
                        "$set": {
                            **value,
                            "marked_at": now,
                            "updated_at": now,
                            "mutation_id": mutation_id,
                            "mutation_lease_owner": lease_owner,
                            "mutation_fencing_token": fencing_token,
                        }
                    },
                    upsert=True,
                    **session_args,
                )
                if (
                    getattr(position_write, "matched_count", 1) == 0
                    and not getattr(position_write, "upserted_id", None)
                ):
                    raise RuntimeError("position fencing token rejected")
            account_write = db.paper_accounts.update_one(
                {
                    "user_id": user_id,
                    "mutation_id": mutation_id,
                    "mutation_lease_owner": lease_owner,
                    "mutation_fencing_token": fencing_token,
                },
                {"$set": {"cash": simulated_cash, "updated_at": now}},
                **session_args,
            )
            if getattr(account_write, "matched_count", 1) == 0:
                raise RuntimeError("account fencing token rejected")
            for item in prepared:
                if lease_renew is not None:
                    lease_renew()
                renew_mutation_lease()
                order_id = hashlib.sha256(
                    (
                        f"{user_id}\0{external_idempotency_key}\0"
                        f"{item['index']}"
                    ).encode()
                ).hexdigest()
                trade = {
                    "user_id": user_id,
                    **{
                        key: value
                        for key, value in item.items()
                        if key != "index"
                    },
                    "order_id": order_id,
                    "source": mutation_source,
                    "mutation_id": mutation_id,
                    "lease_owner": lease_owner,
                    "fencing_token": fencing_token,
                    "external_idempotency_key": (
                        f"{external_idempotency_key}:{item['index']}"
                    ),
                    "created_at": now,
                    "voided": False,
                }
                trade_write = db.paper_trades.update_one(
                    {
                        "user_id": user_id,
                        "order_id": order_id,
                        "$or": [
                            {"fencing_token": {"$lte": fencing_token}},
                            {"fencing_token": {"$exists": False}},
                        ],
                    },
                    {"$set": trade},
                    upsert=True,
                    **session_args,
                )
                if getattr(trade_write, "matched_count", 1) == 0 and not getattr(
                    trade_write, "upserted_id", None
                ):
                    raise RuntimeError("trade fencing token rejected")
                trade.pop("_id", None)
                trades.append(trade)
        _finish_account_mutation(
            db,
            user_id,
            mutation_id,
            lease_owner=lease_owner,
            fencing_token=fencing_token,
        )
        if lease_renew is not None:
            lease_renew()
        result = {
            "trades": _json_safe(trades),
            "account": get_account(user_id, mark_to_market=False),
            "mutation_id": mutation_id,
        }
        result_write = db.paper_mutations.update_one(
            {
                "user_id": user_id,
                "mutation_id": mutation_id,
                "lease_owner": lease_owner,
                "fencing_token": fencing_token,
                "status": "completed",
            },
            {"$set": {"result_payload": result}},
        )
        if getattr(result_write, "matched_count", 1) == 0:
            raise RuntimeError("journal fencing token rejected")
        return result
    except BaseException:
        try:
            recover_pending_account_mutation(
                user_id,
                mutation_id,
                _db=db,
                recovery_owner=lease_owner,
            )
        except Exception:
            pass
        raise


def list_trades(
    user_id: str,
    limit: int = 100,
    source: str | None = None,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """List trades. If page/page_size given, return {trades, total, page, page_size}."""
    db = get_db()
    q: dict[str, Any] = {
        "user_id": user_id,
        "side": {"$in": ["buy", "sell"]},
        "voided": {"$ne": True},
    }
    if source:
        q["source"] = source

    # name fallback from current positions
    name_map = {
        p["symbol"]: p.get("name")
        for p in db.paper_positions.find({"user_id": user_id}, {"_id": 0, "symbol": 1, "name": 1})
    }

    def _row(t: dict[str, Any]) -> dict[str, Any]:
        row = dict(t)
        if hasattr(row.get("created_at"), "isoformat"):
            row["created_at"] = row["created_at"].isoformat()
        if not row.get("name"):
            row["name"] = name_map.get(row.get("symbol") or "", "") or row.get("symbol")
        return row

    if page is not None and page_size is not None:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        total = db.paper_trades.count_documents(q)
        skip = (page - 1) * page_size
        cur = (
            db.paper_trades.find(q, {"_id": 0})
            .sort("created_at", -1)
            .skip(skip)
            .limit(page_size)
        )
        return {
            "trades": [_row(t) for t in cur],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size) if total else 1,
        }

    cur = db.paper_trades.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    return [_row(t) for t in cur]


def _collect_one_click_picks(
    recommendations: dict[str, Any],
    *,
    boards: tuple[str, ...] | list[str] | None = None,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    allow = set(boards or ONE_CLICK_BOARDS)
    picks: list[dict[str, Any]] = []
    for bid, block in (recommendations.get("boards") or {}).items():
        if bid not in allow:
            continue
        for it in block.get("items") or []:
            if it.get("action") in ("buy", "add") or (
                it.get("score") is not None
                and float(it["score"])
                >= float(recommendations.get("buy_threshold") or 0.55)
            ):
                picks.append(it)
    by_sym: dict[str, dict[str, Any]] = {}
    for p in picks:
        sym = p.get("symbol")
        if not sym:
            continue
        if sym not in by_sym or float(p.get("score") or 0) > float(
            by_sym[sym].get("score") or 0
        ):
            by_sym[sym] = p
    ranked = sorted(
        by_sym.values(), key=lambda x: float(x.get("score") or 0), reverse=True
    )
    if max_count is not None and max_count > 0:
        return ranked[:max_count]
    return ranked



def _pick_price(p: dict[str, Any]) -> tuple[str, float]:
    """Use archived close when present; only fall back to live kline."""
    name = str(p.get("name") or p.get("symbol") or "")
    close = p.get("close")
    if close is not None and float(close) > 0:
        return name, float(close)
    live_name, px = _latest_price(str(p["symbol"]))
    return name or live_name, px


def _allocate_lots_full(
    cash: float,
    priced: list[tuple[dict[str, Any], str, float, float]],
) -> list[tuple[dict[str, Any], str, float, float]]:
    """按评分比例分配手数，再用剩余资金补满（尽量满仓）。

    priced: (pick, name, price, weight)
    returns: (pick, name, price, qty) 仅 qty>=LOT
    """
    if cash <= 0 or not priced:
        return []
    wsum = sum(w for *_, w in priced) or 1.0
    targets = [cash * (w / wsum) for *_, w in priced]
    qtys = [0.0] * len(priced)
    remaining = cash

    for i, (_p, _name, px, _w) in enumerate(priced):
        if px <= 0:
            continue
        qty = (targets[i] / px // LOT) * LOT
        cost = qty * px
        if cost > remaining:
            qty = (remaining / px // LOT) * LOT
            cost = qty * px
        if qty >= LOT:
            qtys[i] = qty
            remaining -= cost

    # 剩余资金：优先补给相对目标缺口最大的标的，直至买不起 1 手
    while True:
        best_i: int | None = None
        best_deficit = float("-inf")
        for i, (_p, _name, px, _w) in enumerate(priced):
            if px <= 0 or remaining + 1e-9 < px * LOT:
                continue
            deficit = targets[i] - qtys[i] * px
            if best_i is None or deficit > best_deficit:
                best_i = i
                best_deficit = deficit
        if best_i is None:
            break
        px = priced[best_i][2]
        qtys[best_i] += LOT
        remaining -= px * LOT

    out: list[tuple[dict[str, Any], str, float, float]] = []
    for i, (p, name, px, _w) in enumerate(priced):
        if qtys[i] >= LOT:
            out.append((p, name, px, qtys[i]))
    return out


def one_click_buy_from_recs(
    user_id: str,
    recommendations: dict[str, Any],
    trade_date: str | None = None,
    boards: tuple[str, ...] | list[str] | None = None,
    *,
    mode: str = "balanced",
    max_count: int | None = None,
) -> dict[str, Any]:
    """Allocate cash by score across buy/add picks (fast: prefer snapshot close)."""
    trades: list[dict[str, Any]] = []
    account: dict[str, Any] | None = None
    for ev in iter_one_click_buy_events(
        user_id,
        recommendations,
        trade_date,
        boards=boards,
        mode=mode,
        max_count=max_count,
    ):
        if ev["event"] == "trade":
            trades.append(ev["data"]["trade"])
        elif ev["event"] == "done":
            account = ev["data"]["account"]
            return {
                "trades": trades,
                "account": account,
                "rec_date": ev["data"].get("rec_date"),
                "source": "rec_one_click",
                "boards": list(boards or ONE_CLICK_BOARDS),
                "mode": mode,
                "max_count": max_count,
            }
        elif ev["event"] == "error":
            raise ValueError(ev["data"].get("detail") or "一键买入失败")
    raise ValueError("一键买入未完成")


def iter_one_click_buy_events(
    user_id: str,
    recommendations: dict[str, Any],
    trade_date: str | None = None,
    boards: tuple[str, ...] | list[str] | None = None,
    *,
    mode: str = "balanced",
    max_count: int | None = None,
):
    """Yield SSE events: meta → trade* → done | error.

    mode=balanced：按评分比例分配（可能留现金）
    mode=full：尽量用尽现金满仓
    max_count：最多买入标的数（按评分取 Top N）；None 不限制
    """
    board_ids = tuple(boards or ONE_CLICK_BOARDS)
    full = mode == "full"
    limit = max_count if max_count is not None and max_count > 0 else None
    picks = _collect_one_click_picks(
        recommendations, boards=board_ids, max_count=limit
    )
    if not picks:
        yield {
            "event": "error",
            "data": {"detail": "今日无达标可买推荐（已排除科创，仅 ETF/沪深）"},
        }
        return

    # cash only, no mark-to-market (was the main slowdown)
    account = get_account(user_id, mark_to_market=False)
    cash = float(account["cash"])
    if cash < LOT * 1:
        yield {"event": "error", "data": {"detail": "模拟盘余额不足"}}
        return

    rec_date = trade_date or recommendations.get("as_of")
    yield {
        "event": "meta",
        "data": {
            "total": len(picks),
            "cash": cash,
            "rec_date": rec_date,
            "boards": list(board_ids),
            "mode": "full" if full else "balanced",
            "max_count": limit,
        },
    }

    if full:
        yield from _iter_one_click_full(
            user_id, picks, cash=cash, rec_date=rec_date
        )
    else:
        yield from _iter_one_click_balanced(
            user_id, picks, cash=cash, rec_date=rec_date
        )


def _iter_one_click_balanced(
    user_id: str,
    picks: list[dict[str, Any]],
    *,
    cash: float,
    rec_date: str | None,
):
    weights = [max(float(p.get("score") or 0), 0.01) ** 2 for p in picks]
    wsum = sum(weights) or 1.0
    budget = cash * 0.99
    trades: list[dict[str, Any]] = []
    skipped = 0

    for i, (p, w) in enumerate(zip(picks, weights)):
        alloc = budget * (w / wsum)
        try:
            name, px = _pick_price(p)
        except Exception as exc:
            skipped += 1
            yield {
                "event": "skip",
                "data": {
                    "index": i,
                    "symbol": p.get("symbol"),
                    "reason": f"无价格: {exc}",
                    "done": i + 1,
                    "total": len(picks),
                },
            }
            continue
        qty = (alloc / px // LOT) * LOT
        if qty < LOT:
            skipped += 1
            yield {
                "event": "skip",
                "data": {
                    "index": i,
                    "symbol": p.get("symbol"),
                    "reason": "余额不足以买入手数",
                    "done": i + 1,
                    "total": len(picks),
                },
            }
            continue
        try:
            result = place_order(
                user_id,
                PaperOrderBody(
                    symbol=str(p["symbol"]),
                    side="buy",
                    qty=qty,
                    price=px,
                    name=p.get("name") or name,
                ),
                source="rec_one_click",
                rec_date=rec_date,
                mark_to_market=False,
            )
            trades.append(result["trade"])
            budget = float(result["account"]["cash"]) * 0.99
            yield {
                "event": "trade",
                "data": {
                    "index": i,
                    "done": i + 1,
                    "total": len(picks),
                    "trade": result["trade"],
                    "cash": result["account"]["cash"],
                },
            }
        except Exception as exc:
            skipped += 1
            yield {
                "event": "skip",
                "data": {
                    "index": i,
                    "symbol": p.get("symbol"),
                    "reason": str(exc),
                    "done": i + 1,
                    "total": len(picks),
                },
            }

    if not trades:
        yield {
            "event": "error",
            "data": {"detail": "无法下单：单价过高或余额不足以买入手数"},
        }
        return

    yield {
        "event": "done",
        "data": {
            "trades_count": len(trades),
            "skipped": skipped,
            "rec_date": rec_date,
            "source": "rec_one_click",
            "mode": "balanced",
            "account": get_account(user_id, mark_to_market=False),
        },
    }


def _iter_one_click_full(
    user_id: str,
    picks: list[dict[str, Any]],
    *,
    cash: float,
    rec_date: str | None,
):
    """先解析价格 → 按评分+剩余资金分配手数 → 下单。"""
    priced: list[tuple[dict[str, Any], str, float, float]] = []
    skipped = 0
    total = len(picks)

    for i, p in enumerate(picks):
        try:
            name, px = _pick_price(p)
        except Exception as exc:
            skipped += 1
            yield {
                "event": "skip",
                "data": {
                    "index": i,
                    "symbol": p.get("symbol"),
                    "reason": f"无价格: {exc}",
                    "done": i + 1,
                    "total": total,
                    "phase": "price",
                },
            }
            continue
        if px <= 0:
            skipped += 1
            yield {
                "event": "skip",
                "data": {
                    "index": i,
                    "symbol": p.get("symbol"),
                    "reason": "价格无效",
                    "done": i + 1,
                    "total": total,
                    "phase": "price",
                },
            }
            continue
        w = max(float(p.get("score") or 0), 0.01) ** 2
        priced.append((p, p.get("name") or name, px, w))
        yield {
            "event": "progress",
            "data": {
                "index": i,
                "symbol": p.get("symbol"),
                "done": i + 1,
                "total": total,
                "phase": "price",
                "message": f"报价 {p.get('symbol')}",
            },
        }

    # 尽量用尽现金（保留极少浮点余量）
    plan = _allocate_lots_full(cash * 0.999, priced)
    if not plan:
        yield {
            "event": "error",
            "data": {"detail": "无法满仓：单价过高或余额不足以买入手数"},
        }
        return

    trades: list[dict[str, Any]] = []
    trade_total = len(plan)
    yield {
        "event": "meta",
        "data": {
            "total": trade_total,
            "cash": cash,
            "rec_date": rec_date,
            "mode": "full",
            "phase": "order",
        },
    }
    for i, (p, name, px, qty) in enumerate(plan):
        try:
            result = place_order(
                user_id,
                PaperOrderBody(
                    symbol=str(p["symbol"]),
                    side="buy",
                    qty=qty,
                    price=px,
                    name=p.get("name") or name,
                ),
                source="rec_one_click",
                rec_date=rec_date,
                mark_to_market=False,
            )
            trades.append(result["trade"])
            yield {
                "event": "trade",
                "data": {
                    "index": i,
                    "done": i + 1,
                    "total": trade_total,
                    "trade": result["trade"],
                    "cash": result["account"]["cash"],
                    "phase": "order",
                },
            }
        except Exception as exc:
            skipped += 1
            yield {
                "event": "skip",
                "data": {
                    "index": i,
                    "symbol": p.get("symbol"),
                    "reason": str(exc),
                    "done": i + 1,
                    "total": trade_total,
                    "phase": "order",
                },
            }

    if not trades:
        yield {
            "event": "error",
            "data": {"detail": "满仓下单失败"},
        }
        return

    account = get_account(user_id, mark_to_market=False)
    cash_left = float(account["cash"])
    spent = cash - cash_left
    yield {
        "event": "done",
        "data": {
            "trades_count": len(trades),
            "skipped": skipped,
            "rec_date": rec_date,
            "source": "rec_one_click",
            "mode": "full",
            "cash_before": round(cash, 2),
            "cash_left": round(cash_left, 2),
            "spent": round(spent, 2),
            "account": account,
        },
    }


def _last_reset_at(user_id: str):
    """最近一次重置时间；无则 None。"""
    db = get_db()
    doc = db.paper_trades.find_one(
        {"user_id": user_id, "side": "reset"},
        {"_id": 0, "created_at": 1},
        sort=[("created_at", -1)],
    )
    return None if not doc else doc.get("created_at")


def _iter_trades_since_reset(user_id: str, *, source: str | None = None):
    """重置后的买卖成交（升序）。排除已作废；重置后幽灵仓不计入收益。"""
    db = get_db()
    q: dict[str, Any] = {
        "user_id": user_id,
        "side": {"$in": ["buy", "sell"]},
        "voided": {"$ne": True},
    }
    if source:
        q["source"] = source
    reset_at = _last_reset_at(user_id)
    if reset_at is not None:
        q["created_at"] = {"$gt": reset_at}
    return list(db.paper_trades.find(q, {"_id": 0}).sort("created_at", 1))


def delete_position(user_id: str, symbol: str) -> dict[str, Any]:
    """删除持仓：当作从未买过该标的。

    - 作废重置后该标的全部买卖记录（不计入收益）
    - 按买卖净流出回补现金
    - 删除持仓
    """
    symbol = normalize_symbol(symbol)
    db = get_db()
    pos = db.paper_positions.find_one({"user_id": user_id, "symbol": symbol})
    if not pos:
        raise ValueError(f"无持仓: {symbol}")

    reset_at = _last_reset_at(user_id)
    q: dict[str, Any] = {
        "user_id": user_id,
        "symbol": symbol,
        "side": {"$in": ["buy", "sell"]},
        "voided": {"$ne": True},
    }
    if reset_at is not None:
        q["created_at"] = {"$gt": reset_at}
    trades = list(db.paper_trades.find(q))

    buy_amt = sum(
        float(t.get("amount") or 0) for t in trades if t.get("side") == "buy"
    )
    sell_amt = sum(
        float(t.get("amount") or 0) for t in trades if t.get("side") == "sell"
    )
    if trades:
        net_spent = buy_amt - sell_amt
    else:
        # 无成交记录时按成本回补
        net_spent = float(pos.get("cost") or 0) * float(pos.get("qty") or 0)

    acc = db.paper_accounts.find_one({"user_id": user_id})
    if not acc:
        raise ValueError("模拟盘账户不存在")
    mutation_id = _begin_account_mutation(
        db,
        user_id,
        kind="delete_position",
        expected_version=acc.get("account_version"),
        expected_updated_at=acc.get("updated_at"),
    )
    new_cash = float(acc["cash"]) + net_spent
    now = _now()

    db.paper_accounts.update_one(
        {"user_id": user_id},
        {"$set": {"cash": new_cash, "updated_at": now}},
    )
    db.paper_positions.delete_one({"user_id": user_id, "symbol": symbol})
    if trades:
        db.paper_trades.update_many(
            {"_id": {"$in": [t["_id"] for t in trades]}},
            {
                "$set": {
                    "voided": True,
                    "voided_at": now,
                    "void_reason": "position_delete",
                }
            },
        )
    db.paper_trades.insert_one(
        {
            "user_id": user_id,
            "symbol": symbol,
            "name": pos.get("name") or symbol,
            "side": "delete",
            "qty": float(pos.get("qty") or 0),
            "price": float(pos.get("cost") or 0),
            "amount": round(net_spent, 2),
            "source": "position_delete",
            "mutation_id": mutation_id,
            "voided": False,
            "created_at": now,
        }
    )
    owner, token = _mutation_identity(db, user_id, mutation_id)
    _finish_account_mutation(
        db,
        user_id,
        mutation_id,
        lease_owner=owner,
        fencing_token=token,
    )
    return get_account(user_id, mark_to_market=False)


def sell_all_positions(user_id: str) -> dict[str, Any]:
    """一键卖出：按库内现价（无则拉行情）全部平仓，计入已实现收益。"""
    account = get_account(user_id, mark_to_market=False)
    positions = list(account.get("positions") or [])
    if not positions:
        raise ValueError("当前无持仓可卖")

    trades: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for pos in positions:
        sym = str(pos.get("symbol") or "")
        qty = float(pos.get("qty") or 0)
        if not sym or qty <= 0:
            continue
        last = pos.get("last")
        try:
            result = place_order(
                user_id,
                PaperOrderBody(
                    symbol=sym,
                    side="sell",
                    qty=qty,
                    price=float(last) if last is not None and float(last) > 0 else None,
                    name=pos.get("name"),
                ),
                source="manual",
                mark_to_market=False,
            )
            trades.append(result["trade"])
        except Exception as exc:
            errors.append({"symbol": sym, "detail": str(exc)})

    if not trades and errors:
        raise ValueError(errors[0]["detail"] if len(errors) == 1 else f"全部卖出失败: {errors}")

    return {
        "sold": len(trades),
        "failed": len(errors),
        "trades": trades,
        "errors": errors,
        "account": get_account(user_id, mark_to_market=False),
    }


def _fifo_open_lots(
    trades: list[dict[str, Any]],
    held_qty: dict[str, float] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """按 FIFO 还原仍持有的 lots；可选与真实持仓手数对齐，丢掉幽灵仓。"""
    from collections import defaultdict, deque

    lots: dict[str, deque] = defaultdict(deque)

    def _src(s: Any) -> str:
        if s == "rec_one_click":
            return "rec_one_click"
        if s == "manual":
            return "manual"
        return "other"

    for t in trades:
        side = t.get("side")
        sym = str(t.get("symbol") or "")
        qty = float(t.get("qty") or 0)
        px = float(t.get("price") or 0)
        if not sym or qty <= 0:
            continue
        if side == "buy":
            lots[sym].append(
                {
                    "qty": qty,
                    "price": px,
                    "source": _src(t.get("source")),
                    "name": t.get("name"),
                    "rec_date": t.get("rec_date"),
                    "created_at": t.get("created_at"),
                }
            )
        elif side == "sell":
            remain = qty
            while remain > 1e-9 and lots[sym]:
                lot = lots[sym][0]
                take = min(remain, float(lot["qty"]))
                lot["qty"] = float(lot["qty"]) - take
                remain -= take
                if float(lot["qty"]) <= 1e-9:
                    lots[sym].popleft()

    if held_qty is not None:
        for sym, q in list(lots.items()):
            want = float(held_qty.get(sym) or 0)
            have = sum(float(lot["qty"]) for lot in q)
            excess = have - want
            while excess > 1e-9 and q:
                lot = q[0]
                take = min(excess, float(lot["qty"]))
                lot["qty"] = float(lot["qty"]) - take
                excess -= take
                if float(lot["qty"]) <= 1e-9:
                    q.popleft()
            if not q:
                lots.pop(sym, None)

    return {sym: list(q) for sym, q in lots.items() if q}


def get_account_snapshot_atomic(
    user_id: str,
    *,
    as_of: datetime,
    max_retries: int = 2,
    _db: Any | None = None,
    _allow_pending_mutation_id: str | None = None,
) -> dict[str, Any]:
    """Read one version-consistent current snapshot or an archived historical one."""
    db = _db if _db is not None else get_db()
    as_of = as_of.astimezone(timezone.utc)
    if as_of.date() < _now().date():
        archived = db.paper_account_snapshots.find_one(
            {"user_id": user_id, "data_as_of": {"$lte": as_of}},
            {"_id": 0},
            sort=[("data_as_of", -1)],
        )
        if not archived:
            raise ValueError("historical account snapshot is unavailable")
        return archived

    for _attempt in range(max_retries):
        before = db.paper_accounts.find_one({"user_id": user_id})
        if not before:
            raise ValueError("paper account does not exist")
        if before.get("mutation_pending") and (
            before.get("mutation_id") != _allow_pending_mutation_id
        ):
            raise RuntimeError("account mutation is pending")
        fingerprint = (
            before.get("account_version", 0),
            before.get("updated_at"),
            before.get("latest_mutation_id"),
        )
        positions = list(
            db.paper_positions.find({"user_id": user_id}, {"_id": 0})
        )
        position_fingerprint = sorted(
            (
                str(item.get("symbol") or ""),
                item.get("updated_at"),
                item.get("marked_at"),
                float(item.get("qty") or 0),
                float(item.get("last") or 0),
            )
            for item in positions
        )
        reset = db.paper_trades.find_one(
            {"user_id": user_id, "side": "reset"},
            {"_id": 0, "created_at": 1},
            sort=[("created_at", -1)],
        )
        trade_query: dict[str, Any] = {
            "user_id": user_id,
            "side": {"$in": ["buy", "sell"]},
            "voided": {"$ne": True},
        }
        if reset and reset.get("created_at") is not None:
            trade_query["created_at"] = {"$gt": reset["created_at"]}
        trades = list(
            db.paper_trades.find(trade_query, {"_id": 0}).sort(
                "created_at", 1
            )
        )
        latest_trade = db.paper_trades.find_one(
            {"user_id": user_id},
            {"_id": 0, "mutation_id": 1},
            sort=[("created_at", -1)],
        )
        after = db.paper_accounts.find_one({"user_id": user_id})
        positions_after = list(
            db.paper_positions.find({"user_id": user_id}, {"_id": 0})
        )
        position_fingerprint_after = sorted(
            (
                str(item.get("symbol") or ""),
                item.get("updated_at"),
                item.get("marked_at"),
                float(item.get("qty") or 0),
                float(item.get("last") or 0),
            )
            for item in positions_after
        )
        if not after or (
            after.get("account_version", 0),
            after.get("updated_at"),
            after.get("latest_mutation_id"),
        ) != fingerprint or position_fingerprint_after != position_fingerprint:
            continue
        if after.get("mutation_pending") and (
            after.get("mutation_id") != _allow_pending_mutation_id
        ):
            raise RuntimeError("account mutation is pending")
        latest_mutation_id = after.get("latest_mutation_id")
        if latest_mutation_id and _allow_pending_mutation_id is None:
            journal = db.paper_mutations.find_one(
                {
                    "user_id": user_id,
                    "mutation_id": latest_mutation_id,
                },
                {"_id": 0},
            )
            if (
                not journal
                or journal.get("status") != "completed"
                or int(journal.get("account_version", -1))
                != int(after.get("account_version", 0))
            ):
                raise RuntimeError("account mutation journal/version mismatch")
            archived = db.paper_account_snapshots.find_one(
                {
                    "user_id": user_id,
                    "account_version": int(after.get("account_version", 0)),
                },
                {"_id": 0},
                sort=[("data_as_of", -1)],
            )
            if archived is None:
                raise RuntimeError("completed mutation archive is missing")
            expected_hash = journal.get("archive_hash")
            if expected_hash:
                actual_hash = hashlib.sha256(
                    json.dumps(
                        archived, default=str, sort_keys=True
                    ).encode("utf-8")
                ).hexdigest()
                if actual_hash != expected_hash:
                    raise RuntimeError("completed mutation archive hash mismatch")
            journal_type = str(journal.get("type") or "")
            if journal_type.startswith(("order:", "trade")) and (
                not latest_trade
                or latest_trade.get("mutation_id") != latest_mutation_id
            ):
                raise RuntimeError("account trade/version mismatch")
        held_qty = {
            str(item["symbol"]): float(item.get("qty") or 0)
            for item in positions
        }
        lots = _fifo_open_lots(trades, held_qty=held_qty)
        local_day = as_of.astimezone(ZoneInfo("Asia/Shanghai")).date()
        same_day_buys: dict[str, float] = {}
        same_day_sells: dict[str, float] = {}
        for trade in trades:
            created = trade.get("created_at")
            if (
                created is None
                or created.astimezone(ZoneInfo("Asia/Shanghai")).date()
                != local_day
            ):
                continue
            target = (
                same_day_buys
                if trade.get("side") == "buy"
                else same_day_sells
            )
            symbol = str(trade.get("symbol") or "")
            target[symbol] = target.get(symbol, 0) + float(
                trade.get("qty") or 0
            )
        frozen_positions = []
        latest_times = [before.get("updated_at")]
        for item in positions:
            symbol = str(item["symbol"])
            acquired = [
                lot.get("created_at")
                for lot in lots.get(symbol, [])
                if lot.get("created_at") is not None
            ]
            if not acquired:
                raise ValueError(
                    f"position acquisition date unavailable for {symbol}"
                )
            quantity = float(item.get("qty") or 0)
            last_price = float(item.get("last") or 0)
            price_as_of = item.get("marked_at") or item.get("updated_at")
            if last_price <= 0 or price_as_of is None:
                raise ValueError(f"position valuation unavailable for {symbol}")
            latest_times.append(price_as_of)
            available = max(
                0.0,
                quantity
                - max(
                    0.0,
                    same_day_buys.get(symbol, 0)
                    - same_day_sells.get(symbol, 0),
                ),
            )
            frozen_positions.append(
                {
                    "symbol": symbol,
                    "quantity": quantity,
                    "available_quantity": available,
                    "acquired_at": min(acquired),
                    "cost": float(item.get("cost") or 0),
                    "last_price": last_price,
                    "market_value": quantity * last_price,
                    "price_as_of": price_as_of,
                }
            )
        cash = float(before["cash"])
        market_value = sum(
            float(item["market_value"]) for item in frozen_positions
        )
        data_as_of = max(value for value in latest_times if value is not None)
        version = str(
            before.get("account_version", 0)
            or data_as_of.isoformat()
        )
        return {
            "user_id": user_id,
            "cash": cash,
            "equity": cash + market_value,
            "positions": frozen_positions,
            "version": version,
            "account_version": int(before.get("account_version", 0)),
            "latest_mutation_id": before.get("latest_mutation_id"),
            "data_as_of": data_as_of,
        }
    raise RuntimeError("paper account changed during snapshot read")


def rec_one_click_performance(
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """PnL of positions/trades tagged rec_one_click（open_rows 分页）。"""
    trades = _iter_trades_since_reset(user_id, source="rec_one_click")
    buys = [t for t in trades if t.get("side") == "buy"]
    account = get_account(user_id, mark_to_market=False)
    held = {p["symbol"]: p for p in account["positions"]}
    held_qty = {s: float(p.get("qty") or 0) for s, p in held.items()}

    # 用 FIFO 剩余手数算浮盈，避免把整仓盈亏挂到每一笔一键买入上
    open_lots = _fifo_open_lots(trades, held_qty=held_qty)
    open_rows: list[dict[str, Any]] = []
    closed_rows: list[dict[str, Any]] = []
    total_cost = 0.0
    total_mv = 0.0
    total_unreal = 0.0

    for sym, lots in open_lots.items():
        p = held.get(sym) or {}
        last = float(p.get("last") or 0)
        for lot in lots:
            if lot.get("source") != "rec_one_click":
                continue
            qty = float(lot["qty"])
            cost_px = float(lot["price"])
            px = last if last > 0 else cost_px
            cost = cost_px * qty
            mv = px * qty
            unreal = (px - cost_px) * qty
            total_cost += cost
            total_mv += mv
            total_unreal += unreal
            open_rows.append(
                {
                    "symbol": sym,
                    "name": lot.get("name") or p.get("name") or sym,
                    "buy_price": cost_px,
                    "buy_qty": qty,
                    "rec_date": lot.get("rec_date"),
                    "last": px,
                    "held_qty": held_qty.get(sym),
                    "unrealized_pnl": round(unreal, 2),
                    "unrealized_pnl_pct": (
                        None if cost_px <= 0 else round(px / cost_px - 1, 6)
                    ),
                    "status": "open",
                }
            )

    # 已平仓：一键买入过、当前无剩余 lot
    open_syms = {r["symbol"] for r in open_rows}
    for t in buys:
        sym = t["symbol"]
        if sym in open_syms:
            continue
        if any(r.get("symbol") == sym for r in closed_rows):
            continue
        closed_rows.append(
            {
                **{
                    k: (v.isoformat() if hasattr(v, "isoformat") else v)
                    for k, v in t.items()
                },
                "status": "closed",
                "unrealized_pnl": None,
            }
        )

    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    total = len(open_rows)
    pages = max(1, (total + page_size - 1) // page_size) if total else 1
    if page > pages:
        page = pages
    start = (page - 1) * page_size
    page_rows = open_rows[start : start + page_size]

    return {
        "trades_count": len(buys),
        "open_total": total,
        "open_rows": page_rows,
        "closed_rows": closed_rows[:50],
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "approx_one_click_mv": round(total_mv, 2),
        "approx_one_click_cost": round(total_cost, 2),
        "approx_one_click_pnl": round(total_unreal, 2),
        "account_equity": account["equity"],
    }


def paper_pnl_summary(user_id: str) -> dict[str, Any]:
    """按成交 FIFO 拆分一键买入 / 手动买入 / 总收益（库内缓存价）。

    仅统计「最近一次重置」之后的成交，避免重置前幽灵仓把一键收益撑爆。
    """
    account = get_account(user_id, mark_to_market=False)
    initial = float(account["initial_cash"] or 0)
    equity = float(account["equity"] or 0)
    cash = float(account["cash"] or 0)
    last_map = {
        p["symbol"]: float(p.get("last") or p.get("cost") or 0)
        for p in account["positions"]
    }
    held_qty = {p["symbol"]: float(p.get("qty") or 0) for p in account["positions"]}

    trades = _iter_trades_since_reset(user_id)

    from collections import defaultdict, deque

    lots: dict[str, deque] = defaultdict(deque)
    realized = {"rec_one_click": 0.0, "manual": 0.0, "other": 0.0}

    def _src(s: Any) -> str:
        if s == "rec_one_click":
            return "rec_one_click"
        if s == "manual":
            return "manual"
        return "other"

    for t in trades:
        side = t.get("side")
        sym = str(t.get("symbol") or "")
        qty = float(t.get("qty") or 0)
        px = float(t.get("price") or 0)
        src = _src(t.get("source"))
        if not sym or qty <= 0:
            continue
        if side == "buy":
            lots[sym].append({"qty": qty, "price": px, "source": src})
        elif side == "sell":
            remain = qty
            while remain > 1e-9 and lots[sym]:
                lot = lots[sym][0]
                take = min(remain, float(lot["qty"]))
                realized[lot["source"]] += (px - float(lot["price"])) * take
                lot["qty"] = float(lot["qty"]) - take
                remain -= take
                if lot["qty"] <= 1e-9:
                    lots[sym].popleft()

    # 与真实持仓对齐，丢弃重置/对账残留的幽灵手数
    for sym, q in list(lots.items()):
        want = float(held_qty.get(sym) or 0)
        have = sum(float(lot["qty"]) for lot in q)
        excess = have - want
        while excess > 1e-9 and q:
            lot = q[0]
            take = min(excess, float(lot["qty"]))
            lot["qty"] = float(lot["qty"]) - take
            excess -= take
            if float(lot["qty"]) <= 1e-9:
                q.popleft()

    unrealized = {"rec_one_click": 0.0, "manual": 0.0, "other": 0.0}
    open_cost = {"rec_one_click": 0.0, "manual": 0.0, "other": 0.0}
    open_mv = {"rec_one_click": 0.0, "manual": 0.0, "other": 0.0}
    for sym, q in lots.items():
        last = float(last_map.get(sym) or 0)
        for lot in q:
            src = lot["source"]
            qty = float(lot["qty"])
            if qty <= 1e-9:
                continue
            cost_px = float(lot["price"])
            px = last if last > 0 else cost_px
            open_cost[src] += cost_px * qty
            open_mv[src] += px * qty
            unrealized[src] += (px - cost_px) * qty

    def _bucket(key: str) -> dict[str, Any]:
        r = realized[key]
        u = unrealized[key]
        pnl = r + u
        cost = open_cost[key]
        return {
            "realized": round(r, 2),
            "unrealized": round(u, 2),
            "pnl": round(pnl, 2),
            "open_cost": round(cost, 2),
            "open_market_value": round(open_mv[key], 2),
            "return_pct": None if cost <= 0 else round(u / cost, 6),
        }

    total_pnl = equity - initial
    one_click = _bucket("rec_one_click")
    manual = _bucket("manual")
    other = _bucket("other")
    manual_merged = {
        "realized": round(manual["realized"] + other["realized"], 2),
        "unrealized": round(manual["unrealized"] + other["unrealized"], 2),
        "pnl": round(manual["pnl"] + other["pnl"], 2),
        "open_cost": round(manual["open_cost"] + other["open_cost"], 2),
        "open_market_value": round(
            manual["open_market_value"] + other["open_market_value"], 2
        ),
        "return_pct": (
            None
            if (manual["open_cost"] + other["open_cost"]) <= 0
            else round(
                (manual["unrealized"] + other["unrealized"])
                / (manual["open_cost"] + other["open_cost"]),
                6,
            )
        ),
    }

    total_realized = (
        float(one_click["realized"])
        + float(manual_merged["realized"])
    )
    total_unrealized = (
        float(one_click["unrealized"])
        + float(manual_merged["unrealized"])
    )
    total_open_cost = (
        float(one_click["open_cost"]) + float(manual_merged["open_cost"])
    )
    total_open_mv = (
        float(one_click["open_market_value"])
        + float(manual_merged["open_market_value"])
    )
    hist_total_pnl = float(one_click["pnl"]) + float(manual_merged["pnl"])

    return {
        "initial_cash": round(initial, 2),
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "market_value": round(float(account["market_value"]), 2),
        # 兼容：total = 历史累计收益
        "total": {
            "pnl": round(hist_total_pnl, 2),
            "return_pct": None if initial <= 0 else round(hist_total_pnl / initial, 6),
            "equity_pnl": round(total_pnl, 2),
        },
        # 历史收益 = 持仓浮盈 + 卖出已实现
        "historical": {
            "total": {
                "pnl": round(hist_total_pnl, 2),
                "realized": round(total_realized, 2),
                "unrealized": round(total_unrealized, 2),
                "return_pct": (
                    None if initial <= 0 else round(hist_total_pnl / initial, 6)
                ),
            },
            "one_click": {
                "pnl": one_click["pnl"],
                "realized": one_click["realized"],
                "unrealized": one_click["unrealized"],
                "return_pct": (
                    None
                    if initial <= 0
                    else round(float(one_click["pnl"]) / initial, 6)
                ),
            },
            "manual": {
                "pnl": manual_merged["pnl"],
                "realized": manual_merged["realized"],
                "unrealized": manual_merged["unrealized"],
                "return_pct": (
                    None
                    if initial <= 0
                    else round(float(manual_merged["pnl"]) / initial, 6)
                ),
            },
        },
        # 持仓收益 = 仅当前浮盈亏
        "holding": {
            "total": {
                "pnl": round(total_unrealized, 2),
                "open_cost": round(total_open_cost, 2),
                "open_market_value": round(total_open_mv, 2),
                "return_pct": (
                    None
                    if total_open_cost <= 0
                    else round(total_unrealized / total_open_cost, 6)
                ),
            },
            "one_click": {
                "pnl": one_click["unrealized"],
                "open_cost": one_click["open_cost"],
                "open_market_value": one_click["open_market_value"],
                "return_pct": one_click["return_pct"],
            },
            "manual": {
                "pnl": manual_merged["unrealized"],
                "open_cost": manual_merged["open_cost"],
                "open_market_value": manual_merged["open_market_value"],
                "return_pct": manual_merged["return_pct"],
            },
        },
        "one_click": one_click,
        "manual": manual_merged,
        "note": (
            "历史收益=持仓浮盈+卖出已实现；持仓收益=仅浮盈亏。"
            "按最近一次重置后的成交 FIFO；现价用库内缓存。"
        ),
    }
