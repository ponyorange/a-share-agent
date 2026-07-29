"""Persist daily recommendation snapshots and evaluate returns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import get_db
from . import context
from .calendar_util import is_trading_day, last_trading_day, parse_date
from .features import fetch_daily_df


def _resolve_user_id(user_id: str | None = None) -> str:
    uid = user_id or context.get_user_id()
    if not uid:
        raise ValueError("缺少 user_id（推荐归档已按用户隔离）")
    return uid


def _close_on_or_before(symbol: str, on_date: str) -> float | None:
    try:
        _, df = fetch_daily_df(symbol)
        if df is None or df.empty:
            return None
        sub = df[df["time"] <= on_date]
        if sub.empty:
            return None
        return float(sub.iloc[-1]["close"])
    except Exception:
        return None


def effective_rec_date(as_of: str | None = None) -> str:
    """Trading day for '今日关注': today if open, else last trading day."""
    if as_of:
        d = str(as_of)[:10]
        if is_trading_day(d):
            return d
        parsed = parse_date(d)
        return last_trading_day(parsed) if parsed else last_trading_day()
    return last_trading_day()


def save_snapshot(
    payload: dict[str, Any],
    trade_date: str | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Upsert recommendation snapshot for the effective trading day (overwrite same day)."""
    uid = _resolve_user_id(user_id)
    td = trade_date or effective_rec_date(
        str(payload.get("as_of") or "")[:10] or None
    )
    td = str(td)[:10]
    # Always key by a trading day; if caller passed a weekend, map back
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
                    "coarse_score": it.get("coarse_score"),
                    "hit_rate": it.get("hit_rate"),
                    "has_position": it.get("has_position"),
                    "rationale": it.get("rationale"),
                    "layer_scores": it.get("layer_scores"),
                    "industry": it.get("industry"),
                }
            )
        slim_boards[bid] = {
            "id": bid,
            "label": block.get("label"),
            "count": len(items),
            "scanned": block.get("scanned"),
            "pool_size": block.get("pool_size"),
            "precise_size": block.get("precise_size"),
            "items": items,
        }

    now = datetime.now(timezone.utc)
    doc = {
        "user_id": uid,
        "trade_date": td,
        "as_of": payload.get("as_of") or td,
        "buy_threshold": payload.get("buy_threshold"),
        "mode": payload.get("mode"),
        "universe_source": payload.get("universe_source"),
        "strategy_hit_rate": payload.get("strategy_hit_rate"),
        "strategy_version": payload.get("strategy_version"),
        "strategy_source": payload.get("strategy_source"),
        "scanned": payload.get("scanned"),
        "pool_total": payload.get("pool_total"),
        "boards": slim_boards,
        "market_context": payload.get("market_context"),
        "updated_at": now,
    }
    db = get_db()
    db.rec_snapshots.update_one(
        {"user_id": uid, "trade_date": td},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {
        "saved": True,
        "trade_date": td,
        "user_id": uid,
        "from_cache": False,
        "boards": {k: v["count"] for k, v in slim_boards.items()},
    }


def iter_rec_quote_events(
    trade_date: str | None = None,
    board: str | None = None,
    *,
    user_id: str | None = None,
):
    """SSE: live last price + day change for archived recommendation symbols."""
    from ..quote import get_last_quote, trading_session

    td = effective_rec_date(trade_date)
    snap = get_snapshot(td, user_id=user_id)
    if not snap:
        yield {
            "event": "error",
            "data": {"detail": f"无归档可加载行情: {td}"},
        }
        return

    items: list[dict[str, Any]] = []
    for bid, block in (snap.get("boards") or {}).items():
        if board and board not in ("", "all") and bid != board:
            continue
        for it in block.get("items") or []:
            if it.get("symbol"):
                items.append({**it, "board": it.get("board") or bid})

    # unique symbols preserve order
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for it in items:
        sym = str(it["symbol"])
        if sym in seen:
            continue
        seen.add(sym)
        uniq.append(it)

    session = trading_session()
    yield {
        "event": "meta",
        "data": {
            "trade_date": td,
            "total": len(uniq),
            "is_trading": bool(session.get("is_trading")),
            "live": True,
        },
    }

    for i, it in enumerate(uniq):
        sym = str(it["symbol"])
        try:
            quote = get_last_quote(sym)
            price = quote.get("price")
            pre = quote.get("pre_close")
            day_chg = quote.get("day_chg_pct")
            if price is None and quote.get("error"):
                raise RuntimeError(str(quote.get("error")))
            yield {
                "event": "quote",
                "data": {
                    "index": i,
                    "done": i + 1,
                    "total": len(uniq),
                    "symbol": sym,
                    "name": quote.get("name") or it.get("name"),
                    "close": price,
                    "prev_close": pre,
                    "day_chg_pct": (
                        None if day_chg is None else round(float(day_chg), 6)
                    ),
                    "as_of": session.get("now"),
                    "board": it.get("board"),
                    "live": True,
                },
            }
        except Exception as exc:
            yield {
                "event": "quote",
                "data": {
                    "index": i,
                    "done": i + 1,
                    "total": len(uniq),
                    "symbol": sym,
                    "name": it.get("name"),
                    "close": None,
                    "prev_close": None,
                    "day_chg_pct": None,
                    "error": str(exc),
                    "board": it.get("board"),
                    "live": True,
                },
            }

    yield {
        "event": "done",
        "data": {
            "trade_date": td,
            "total": len(uniq),
            "is_trading": bool(session.get("is_trading")),
            "live": True,
        },
    }


def list_snapshot_dates(
    limit: int = 60, *, user_id: str | None = None
) -> list[str]:
    uid = _resolve_user_id(user_id)
    db = get_db()
    cur = (
        db.rec_snapshots.find({"user_id": uid}, {"trade_date": 1})
        .sort("trade_date", -1)
        .limit(limit)
    )
    return [d["trade_date"] for d in cur]


def get_snapshot(
    trade_date: str, *, user_id: str | None = None
) -> dict[str, Any] | None:
    uid = _resolve_user_id(user_id)
    db = get_db()
    doc = db.rec_snapshots.find_one(
        {"user_id": uid, "trade_date": trade_date[:10]}, {"_id": 0}
    )
    return doc


def has_snapshot(trade_date: str, *, user_id: str | None = None) -> bool:
    uid = _resolve_user_id(user_id)
    db = get_db()
    return (
        db.rec_snapshots.count_documents(
            {"user_id": uid, "trade_date": trade_date[:10]}, limit=1
        )
        > 0
    )


def snapshot_as_recommendations(
    trade_date: str,
    *,
    board: str | None = None,
    top: int | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Rebuild recommendations API payload from a stored snapshot."""
    snap = get_snapshot(trade_date, user_id=user_id)
    if not snap:
        return None

    boards_raw = snap.get("boards") or {}
    boards_out: dict[str, Any] = {}
    flat: list[dict[str, Any]] = []

    for bid, block in boards_raw.items():
        items = list(block.get("items") or [])
        if top is not None:
            items = items[:top]
        # ensure display fields
        for it in items:
            it.setdefault("action_label", it.get("action") or "")
            it.setdefault("factors", [])
            it.setdefault("as_of", snap.get("as_of") or trade_date)
        boards_out[bid] = {
            "id": bid,
            "label": block.get("label") or bid,
            "scanned": block.get("scanned") or len(items),
            "count": len(items),
            "pool_size": block.get("pool_size"),
            "precise_size": block.get("precise_size"),
            "items": items,
        }
        flat.extend(items)

    if board and board not in ("", "all"):
        flat = list((boards_out.get(board) or {}).get("items") or [])

    return {
        "as_of": snap.get("as_of") or trade_date,
        "trade_date": trade_date,
        "count": len(flat),
        "buy_threshold": snap.get("buy_threshold"),
        "strategy_hit_rate": snap.get("strategy_hit_rate"),
        "items": flat,
        "scanned": snap.get("scanned") or sum(
            int(b.get("scanned") or 0) for b in boards_out.values()
        ),
        "pool_total": snap.get("pool_total"),
        "mode": snap.get("mode") or "cached_snapshot",
        "board": board or "all",
        "boards": boards_out,
        "universe_source": snap.get("universe_source"),
        "disclaimer": "来自归档快照（有效交易日已存在记录时直接展示，手动刷新候选池可覆盖）",
        "snapshot": {
            "saved": True,
            "trade_date": trade_date,
            "from_cache": True,
            "user_id": snap.get("user_id"),
        },
    }


def load_history_plain(
    trade_date: str, *, user_id: str | None = None
) -> dict[str, Any]:
    """Fast history payload: archived picks only, no return calculation."""
    snap = get_snapshot(trade_date, user_id=user_id)
    if not snap:
        raise ValueError(f"无该日推荐归档: {trade_date}")
    flat: list[dict[str, Any]] = []
    boards_out: dict[str, Any] = {}
    for bid, block in (snap.get("boards") or {}).items():
        items = []
        for it in block.get("items") or []:
            row = dict(it)
            row.setdefault("action_label", row.get("action") or "")
            items.append(row)
            flat.append(row)
        boards_out[bid] = {**block, "items": items, "count": len(items)}
    return {
        "trade_date": trade_date[:10],
        "as_of": snap.get("as_of"),
        "count": len(flat),
        "items": flat,
        "boards": boards_out,
        "returns_computed": False,
    }


def _item_return_row(
    it: dict[str, Any],
    trade_date: str,
    vs: str,
) -> dict[str, Any]:
    sym = it.get("symbol")
    base = it.get("close")
    if base is None and sym:
        base = _close_on_or_before(str(sym), trade_date[:10])
    end = _close_on_or_before(str(sym), vs) if sym else None
    ret = None
    if base and end and float(base) > 0:
        ret = float(end) / float(base) - 1.0
    row = dict(it)
    row["base_close"] = base
    row["vs_close"] = end
    row["vs_date"] = vs
    row["return_pct"] = None if ret is None else round(ret, 6)
    return row


def iter_snapshot_return_events(
    trade_date: str,
    vs_date: str | None = None,
    *,
    user_id: str | None = None,
):
    """Yield SSE-ready dict events: meta → item* → done."""
    snap = get_snapshot(trade_date, user_id=user_id)
    if not snap:
        raise ValueError(f"无该日推荐归档: {trade_date}")

    vs = (vs_date or last_trading_day())[:10]
    flat: list[dict[str, Any]] = []
    for block in (snap.get("boards") or {}).values():
        flat.extend(block.get("items") or [])

    yield {
        "event": "meta",
        "data": {
            "trade_date": trade_date[:10],
            "vs_date": vs,
            "total": len(flat),
        },
    }

    hits = 0
    total = 0
    buy_hits = 0
    buy_total = 0

    for i, it in enumerate(flat):
        try:
            row = _item_return_row(it, trade_date, vs)
            err = None
        except Exception as exc:
            row = dict(it)
            row["vs_date"] = vs
            row["return_pct"] = None
            row["base_close"] = it.get("close")
            row["vs_close"] = None
            err = str(exc)

        ret = row.get("return_pct")
        if ret is not None:
            total += 1
            if ret > 0:
                hits += 1
            if it.get("action") in ("buy", "add"):
                buy_total += 1
                if ret > 0:
                    buy_hits += 1

        yield {
            "event": "item",
            "data": {
                "index": i,
                "symbol": row.get("symbol"),
                "error": err,
                **{k: row.get(k) for k in (
                    "name", "score", "action", "action_label", "close",
                    "base_close", "vs_close", "vs_date", "return_pct",
                )},
            },
        }

    yield {
        "event": "done",
        "data": {
            "trade_date": trade_date[:10],
            "vs_date": vs,
            "accuracy": {
                "all_hit_rate": None if total == 0 else round(hits / total, 4),
                "all_n": total,
                "buy_hit_rate": None if buy_total == 0 else round(buy_hits / buy_total, 4),
                "buy_n": buy_total,
                "note": "涨跌幅=相对日收盘/推荐日收盘-1；buy_hit_rate 仅统计 buy/add",
            },
        },
    }


def enrich_snapshot_returns(
    trade_date: str,
    vs_date: str | None = None,
    *,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Attach return from snapshot close → vs_date close for each recommended symbol."""
    plain = load_history_plain(trade_date, user_id=user_id)
    vs = (vs_date or last_trading_day())[:10]
    items_out = []
    hits = 0
    total = 0
    buy_hits = 0
    buy_total = 0
    for it in plain["items"]:
        row = _item_return_row(it, trade_date, vs)
        ret = row.get("return_pct")
        if ret is not None:
            total += 1
            if ret > 0:
                hits += 1
            if it.get("action") in ("buy", "add"):
                buy_total += 1
                if ret > 0:
                    buy_hits += 1
        items_out.append(row)
    return {
        "trade_date": trade_date[:10],
        "vs_date": vs,
        "as_of": plain.get("as_of"),
        "count": len(items_out),
        "items": items_out,
        "returns_computed": True,
        "accuracy": {
            "all_hit_rate": None if total == 0 else round(hits / total, 4),
            "all_n": total,
            "buy_hit_rate": None if buy_total == 0 else round(buy_hits / buy_total, 4),
            "buy_n": buy_total,
            "note": "涨跌幅=vs_date收盘/推荐日收盘-1；buy_hit_rate 仅统计 action=buy/add",
        },
    }


def accuracy_summary(
    limit_days: int = 30, *, user_id: str | None = None
) -> dict[str, Any]:
    """Roll next-day (or vs latest) accuracy across recent snapshots."""
    dates = list_snapshot_dates(limit_days, user_id=user_id)
    rows = []
    for td in dates:
        # next trading day after td as vs when possible; else last trading day
        from datetime import date, timedelta

        d0 = date.fromisoformat(td)
        vs = None
        for i in range(1, 10):
            cand = (d0 + timedelta(days=i)).isoformat()
            if is_trading_day(cand):
                vs = cand
                break
        if vs is None:
            vs = last_trading_day()
        # if vs is in the future relative to today, use today
        today = last_trading_day()
        if vs > today:
            vs = today
        try:
            enriched = enrich_snapshot_returns(td, vs, user_id=user_id)
            acc = enriched["accuracy"]
            rows.append(
                {
                    "trade_date": td,
                    "vs_date": vs,
                    "buy_hit_rate": acc.get("buy_hit_rate"),
                    "buy_n": acc.get("buy_n"),
                    "all_hit_rate": acc.get("all_hit_rate"),
                    "all_n": acc.get("all_n"),
                }
            )
        except Exception as exc:
            rows.append({"trade_date": td, "error": str(exc)})

    valid = [r for r in rows if r.get("buy_n")]
    if valid:
        w_hits = sum((r["buy_hit_rate"] or 0) * r["buy_n"] for r in valid)
        w_n = sum(r["buy_n"] for r in valid)
        overall = round(w_hits / w_n, 4) if w_n else None
    else:
        overall = None

    return {
        "overall_buy_hit_rate": overall,
        "days": len(rows),
        "rows": rows,
    }
