"""MongoDB client for sharedata."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    uri = (os.getenv("MONGODB_URI") or "").strip()
    if not uri:
        raise RuntimeError("MONGODB_URI is required")
    return MongoClient(uri, serverSelectionTimeoutMS=8000)


def get_db() -> Database:
    client = get_client()
    # URI path selects DB; fallback name
    db = client.get_default_database()
    if db is None:
        return client["sharedata"]
    return db


def _ensure_partial_unique_index(
    collection: Any,
    keys: list[tuple[str, int]],
    *,
    name: str,
    partial_filter: dict[str, Any],
) -> None:
    """Create/replace a unique index that ignores missing/null key values."""
    existing = {idx["name"]: idx for idx in collection.list_indexes()}
    current = existing.get(name)
    desired_partial = partial_filter
    if current is not None:
        same_keys = list(current.get("key", {}).items()) == list(keys)
        same_unique = bool(current.get("unique"))
        same_partial = current.get("partialFilterExpression") == desired_partial
        if same_keys and same_unique and same_partial:
            return
        collection.drop_index(name)
    collection.create_index(
        keys,
        unique=True,
        name=name,
        partialFilterExpression=desired_partial,
    )


def ensure_indexes() -> None:
    db = get_db()
    db.users.create_index("username", unique=True)
    _ensure_partial_unique_index(
        db.users,
        [("email", ASCENDING)],
        name="email_1",
        partial_filter={"email": {"$type": "string"}},
    )
    db.email_verification_codes.create_index(
        [("user_id", ASCENDING), ("purpose", ASCENDING), ("created_at", DESCENDING)]
    )
    db.portfolios.create_index("user_id", unique=True)
    db.watchlists.create_index("user_id", unique=True)
    db.agent_monitor_jobs.create_index(
        [("user_id", ASCENDING), ("status", ASCENDING)]
    )
    db.agent_monitor_jobs.create_index(
        [("user_id", ASCENDING), ("updated_at", DESCENDING)]
    )
    db.paper_accounts.create_index("user_id", unique=True)
    db.paper_positions.create_index(
        [("user_id", ASCENDING), ("symbol", ASCENDING)], unique=True
    )
    db.paper_trades.create_index(
        [("user_id", ASCENDING), ("created_at", DESCENDING)]
    )
    # Mongo sparse unique still indexes explicit nulls; historical trades often
    # store external_idempotency_key/order_id as null, so require string values.
    _ensure_partial_unique_index(
        db.paper_trades,
        [
            ("user_id", ASCENDING),
            ("external_idempotency_key", ASCENDING),
        ],
        name="user_id_1_external_idempotency_key_1",
        partial_filter={"external_idempotency_key": {"$type": "string"}},
    )
    _ensure_partial_unique_index(
        db.paper_trades,
        [("user_id", ASCENDING), ("order_id", ASCENDING)],
        name="user_id_1_order_id_1",
        partial_filter={"order_id": {"$type": "string"}},
    )
    db.paper_account_snapshots.create_index(
        [
            ("user_id", ASCENDING),
            ("data_as_of", ASCENDING),
            ("account_version", ASCENDING),
        ],
        unique=True,
    )
    db.paper_mutations.create_index(
        [("user_id", ASCENDING), ("mutation_id", ASCENDING)],
        unique=True,
    )
    db.paper_mutations.create_index(
        [("status", ASCENDING), ("started_at", ASCENDING)]
    )
    _ensure_partial_unique_index(
        db.paper_mutations,
        [
            ("user_id", ASCENDING),
            ("external_idempotency_key", ASCENDING),
        ],
        name="user_id_1_external_idempotency_key_1",
        partial_filter={"external_idempotency_key": {"$type": "string"}},
    )
    db.paper_mutation_counters.create_index("user_id", unique=True)
    db.committee_approvals.create_index(
        [
            ("user_id", ASCENDING),
            ("idempotency_key", ASCENDING),
        ],
        unique=True,
        name="committee_approval_user_idempotency_unique",
    )
    db.committee_approvals.create_index(
        [("status", ASCENDING), ("updated_at", ASCENDING)]
    )
    db.leaderboard_snapshots.create_index(
        [("trade_date", ASCENDING)], unique=True
    )
    # Committee audit indexes are Mongo-only and do not initialize Redis.
    # Local import avoids a module cycle during app startup.
    from .advisor.committee.repository import CommitteeRepository

    CommitteeRepository(db).ensure_indexes()
    # per-user strategies + snapshots（含旧索引迁移）
    try:
        from .advisor.migrate_user_data import (
            ensure_rec_snapshot_indexes,
            migrate_rec_snapshots_to_users,
        )

        ensure_rec_snapshot_indexes()
        migrate_rec_snapshots_to_users()
    except Exception as exc:
        print(
            "[migrate] user strategy/snapshots skipped: "
            f"{type(exc).__name__}"
        )
        # 兜底索引，避免完全不可用
        try:
            db.rec_snapshots.create_index(
                [("user_id", ASCENDING), ("trade_date", ASCENDING)],
                unique=True,
            )
            db.user_strategies.create_index(
                [("user_id", ASCENDING)], unique=True
            )
        except Exception:
            pass
    try:
        db.user_llm_settings.create_index(
            [("user_id", ASCENDING)], unique=True
        )
    except Exception:
        pass
    try:
        db.user_ui_settings.create_index(
            [("user_id", ASCENDING)], unique=True
        )
    except Exception:
        pass
    try:
        db.agent_chat_sessions.create_index(
            [("user_id", ASCENDING), ("updated_at", DESCENDING)]
        )
        db.agent_chat_messages.create_index(
            [
                ("user_id", ASCENDING),
                ("session_id", ASCENDING),
                ("created_at", ASCENDING),
            ]
        )
    except Exception:
        pass
    try:
        db.user_knowledge_items.create_index(
            [("user_id", ASCENDING), ("updated_at", DESCENDING)]
        )
        db.user_knowledge_items.create_index(
            [("user_id", ASCENDING), ("id", ASCENDING)], unique=True
        )
    except Exception:
        pass
    try:
        db.user_agent_config.create_index(
            [("user_id", ASCENDING)], unique=True
        )
    except Exception:
        pass
    try:
        db.rec_refresh_jobs.create_index(
            [("user_id", ASCENDING), ("created_at", DESCENDING)]
        )
        db.rec_refresh_jobs.create_index(
            [("user_id", ASCENDING), ("job_id", ASCENDING)], unique=True
        )
        db.rec_refresh_jobs.create_index(
            [("user_id", ASCENDING), ("trade_date", ASCENDING), ("status", ASCENDING)]
        )
    except Exception:
        pass
    try:
        db.limitup_promote_daily.create_index(
            [("user_id", ASCENDING), ("trade_date", ASCENDING)],
            unique=True,
            name="user_trade_date_1",
        )
        db.limitup_promote_daily.create_index(
            [("user_id", ASCENDING), ("trade_date", DESCENDING)],
            name="user_trade_date_desc",
        )
    except Exception:
        pass


def ping() -> dict[str, Any]:
    client = get_client()
    info = client.server_info()
    return {"ok": True, "version": info.get("version"), "db": get_db().name}
