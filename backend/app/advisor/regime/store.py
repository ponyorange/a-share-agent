from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import get_db


def _collection() -> Any:
    return get_db().market_regime_daily


def _without_mongo_id(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


def _ensure_trade_date_index(col: Any) -> None:
    create_index = getattr(col, "create_index", None)
    if callable(create_index):
        create_index("trade_date", unique=True, name="trade_date_1")


def upsert_daily(trade_date: str, doc: dict) -> None:
    col = _collection()
    _ensure_trade_date_index(col)
    now = datetime.now(timezone.utc)
    col.update_one(
        {"trade_date": trade_date},
        {
            "$set": {
                **dict(doc),
                "trade_date": trade_date,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


def get_daily(trade_date: str) -> dict | None:
    doc = _collection().find_one({"trade_date": trade_date})
    return _without_mongo_id(doc)


def list_daily(limit: int) -> list[dict]:
    n = max(1, min(int(limit), 366))
    cursor = _collection().find({}).sort("trade_date", -1).limit(n)
    return [_without_mongo_id(doc) or {} for doc in cursor]
