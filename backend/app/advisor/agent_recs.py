"""Agent「今日关注」独立归档（与基础面板 rec_snapshots 分离）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import get_db
from . import context
from .calendar_util import is_trading_day, last_trading_day
from .snapshots import effective_rec_date


def _resolve_user_id(user_id: str | None = None) -> str:
    uid = user_id or context.get_user_id()
    if not uid:
        raise ValueError("缺少 user_id")
    return uid


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def save_agent_snapshot(
    payload: dict[str, Any],
    trade_date: str | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    uid = _resolve_user_id(user_id)
    td = str(trade_date or effective_rec_date(str(payload.get("as_of") or "")[:10] or None))[
        :10
    ]
    if not is_trading_day(td):
        td = effective_rec_date(td)

    boards = payload.get("boards") or {}
    slim_boards: dict[str, Any] = {}
    for bid, block in boards.items():
        items = []
        for it in block.get("items") or []:
            if "score" not in it:
                continue
            items.append(
                {
                    "symbol": it.get("symbol"),
                    "name": it.get("name"),
                    "score": it.get("score"),
                    "action": it.get("action"),
                    "action_label": it.get("action_label"),
                    "close": it.get("close"),
                    "prev_close": it.get("prev_close"),
                    "day_chg_pct": it.get("day_chg_pct"),
                    "board": it.get("board") or bid,
                    "score_source": it.get("score_source"),
                    "rationale": it.get("rationale"),
                    "layer_scores": it.get("layer_scores"),
                    "industry": it.get("industry"),
                    "agent_note": it.get("agent_note"),
                    "news_headlines": it.get("news_headlines") or [],
                }
            )
        slim_boards[bid] = {
            "count": len(items),
            "items": items,
            "label": block.get("label"),
        }

    doc = {
        "user_id": uid,
        "trade_date": td,
        "kind": "agent",
        "as_of": payload.get("as_of") or _now_iso(),
        "updated_at": _now_iso(),
        "buy_threshold": payload.get("buy_threshold"),
        "boards": slim_boards,
        "market_brief": payload.get("market_brief") or "",
        "macro_brief": payload.get("macro_brief") or "",
        "market_context": payload.get("market_context"),
        "disclaimer": payload.get("disclaimer")
        or "规则评分 + Agent 资讯摘要，仅供研究参考，不构成投资建议。",
        "meta": payload.get("meta") or {},
    }
    get_db().agent_rec_snapshots.update_one(
        {"user_id": uid, "trade_date": td},
        {"$set": doc, "$setOnInsert": {"created_at": _now_iso()}},
        upsert=True,
    )
    return {**doc, "_id": None}


def has_agent_snapshot(trade_date: str, *, user_id: str | None = None) -> bool:
    uid = _resolve_user_id(user_id)
    return (
        get_db().agent_rec_snapshots.count_documents(
            {"user_id": uid, "trade_date": trade_date[:10]}, limit=1
        )
        > 0
    )


def get_agent_snapshot(
    trade_date: str, *, user_id: str | None = None
) -> dict[str, Any] | None:
    uid = _resolve_user_id(user_id)
    return get_db().agent_rec_snapshots.find_one(
        {"user_id": uid, "trade_date": trade_date[:10]}, {"_id": 0}
    )


def list_agent_snapshot_dates(
    limit: int = 60, *, user_id: str | None = None
) -> list[str]:
    uid = _resolve_user_id(user_id)
    cur = (
        get_db()
        .agent_rec_snapshots.find({"user_id": uid}, {"trade_date": 1})
        .sort("trade_date", -1)
        .limit(limit)
    )
    return [d["trade_date"] for d in cur]


def agent_snapshot_as_recommendations(
    trade_date: str,
    *,
    board: str | None = None,
    top: int | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    snap = get_agent_snapshot(trade_date, user_id=user_id)
    if not snap:
        return None
    boards = snap.get("boards") or {}
    if board:
        boards = {board: boards[board]} if board in boards else {}
    if top is not None:
        out_boards = {}
        for bid, block in boards.items():
            items = list(block.get("items") or [])[: int(top)]
            out_boards[bid] = {**block, "items": items, "count": len(items)}
        boards = out_boards
    return {
        "trade_date": snap.get("trade_date"),
        "as_of": snap.get("as_of"),
        "buy_threshold": snap.get("buy_threshold"),
        "boards": boards,
        "market_brief": snap.get("market_brief"),
        "macro_brief": snap.get("macro_brief"),
        "disclaimer": snap.get("disclaimer"),
        "kind": "agent",
        "from_cache": True,
        "snapshot": {
            "trade_date": snap.get("trade_date"),
            "updated_at": snap.get("updated_at"),
        },
    }


def default_agent_trade_date() -> str:
    return last_trading_day()
