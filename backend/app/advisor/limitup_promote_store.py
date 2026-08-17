"""Per-user daily archive for limit-up promote picks + background refresh."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from typing import Any

from ..db import get_db
from ..limitup import get_limit_up_status_map_for_date
from .calendar_util import last_trading_day, next_trading_day, parse_date
from .llm_settings import resolve_llm_credentials

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}


def _col():
    return get_db().limitup_promote_daily


def ensure_indexes() -> None:
    create = getattr(_col(), "create_index", None)
    if not callable(create):
        return
    create(
        [("user_id", 1), ("trade_date", 1)],
        unique=True,
        name="user_trade_date_1",
    )
    create(
        [("user_id", 1), ("trade_date", -1)],
        name="user_trade_date_desc",
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_key(user_id: str, day: str) -> str:
    return f"{user_id}:{day}"


def _empty_public(day: str, *, status: str = "idle") -> dict[str, Any]:
    return {
        "trade_date": day,
        "date": day,
        "status": status,
        "summary": "",
        "picks": [],
        "candidate_count": 0,
        "as_of": None,
        "session": {},
        "theme_used": {"news": False, "hot_sectors": False, "brief": False},
        "progress": None,
        "updated_at": None,
        "error": None,
        "outcome": None,
        "from_cache": False,
    }


def _public(doc: dict[str, Any] | None, day: str) -> dict[str, Any]:
    if not doc:
        return _empty_public(day)
    status = str(doc.get("status") or "idle")
    progress = None
    if status == "running" and isinstance(doc.get("progress"), dict):
        phase = str(doc["progress"].get("phase") or "").strip()
        message = str(doc["progress"].get("message") or "").strip()
        if phase and message:
            progress = {"phase": phase, "message": message}
    picks = []
    for row in doc.get("picks") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        picks.append(row)
    theme = doc.get("theme_used") if isinstance(doc.get("theme_used"), dict) else {}
    return {
        "trade_date": str(doc.get("trade_date") or day)[:10],
        "date": str(doc.get("date") or doc.get("trade_date") or day)[:10],
        "status": status,
        "summary": str(doc.get("summary") or ""),
        "picks": picks,
        "candidate_count": int(doc.get("candidate_count") or 0),
        "as_of": doc.get("as_of"),
        "session": doc.get("session") if isinstance(doc.get("session"), dict) else {},
        "theme_used": {
            "news": bool(theme.get("news")),
            "hot_sectors": bool(theme.get("hot_sectors")),
            "brief": bool(theme.get("brief")),
        },
        "progress": progress,
        "updated_at": doc.get("updated_at"),
        "error": doc.get("error"),
        "outcome": doc.get("outcome") if isinstance(doc.get("outcome"), dict) else None,
        "from_cache": False,
    }


def get_daily(user_id: str, trade_date: str | None = None) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    try:
        doc = _col().find_one({"user_id": user_id, "trade_date": day}, {"_id": 0})
    except Exception:
        doc = None
    return _public(doc, day)


def upsert_daily(user_id: str, day: str, fields: dict[str, Any]) -> dict[str, Any]:
    ensure_indexes()
    day = day[:10]
    payload = {
        **fields,
        "user_id": user_id,
        "trade_date": day,
        "date": str(fields.get("date") or day)[:10],
        "updated_at": _iso_now(),
    }
    if "created_at" not in fields:
        existing = _col().find_one(
            {"user_id": user_id, "trade_date": day}, {"created_at": 1}
        )
        if not existing or not existing.get("created_at"):
            payload["created_at"] = _iso_now()
    _col().update_one(
        {"user_id": user_id, "trade_date": day},
        {"$set": payload},
        upsert=True,
    )
    return get_daily(user_id, day)


def list_dates(user_id: str, *, limit: int = 60) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or 60), 366))
    try:
        rows = list(
            _col()
            .find(
                {"user_id": user_id, "status": "ready"},
                {
                    "_id": 0,
                    "trade_date": 1,
                    "status": 1,
                    "summary": 1,
                    "candidate_count": 1,
                    "updated_at": 1,
                    "picks": 1,
                },
            )
            .sort("trade_date", -1)
            .limit(lim)
        )
    except Exception:
        return []
    out = []
    for row in rows:
        picks = row.get("picks") or []
        out.append(
            {
                "trade_date": str(row.get("trade_date") or "")[:10],
                "status": str(row.get("status") or ""),
                "summary": str(row.get("summary") or "")[:120],
                "candidate_count": int(row.get("candidate_count") or 0),
                "pick_count": len(picks) if isinstance(picks, list) else 0,
                "updated_at": row.get("updated_at"),
            }
        )
    return out


def _thread_alive(user_id: str, day: str) -> bool:
    with _lock:
        th = _threads.get(_thread_key(user_id, day))
    return th is not None and th.is_alive()


def _set_progress(user_id: str, day: str, phase: str, message: str) -> None:
    existing = None
    try:
        existing = _col().find_one({"user_id": user_id, "trade_date": day}, {"_id": 0})
    except Exception:
        pass
    upsert_daily(
        user_id,
        day,
        {
            "status": "running",
            "summary": (existing or {}).get("summary") or "",
            "picks": (existing or {}).get("picks") or [],
            "candidate_count": int((existing or {}).get("candidate_count") or 0),
            "as_of": (existing or {}).get("as_of"),
            "session": (existing or {}).get("session") or {},
            "theme_used": (existing or {}).get("theme_used")
            or {"news": False, "hot_sectors": False, "brief": False},
            "progress": {"phase": phase, "message": message},
            "error": None,
            "outcome": None,
        },
    )


def _spawn_refresh_thread(user_id: str, day: str, *, force_pool: bool) -> None:
    key = _thread_key(user_id, day)

    def _run() -> None:
        from .limitup_promote import iter_promote_events

        try:
            final: dict[str, Any] | None = None
            for ev in iter_promote_events(
                user_id, force=True, force_pool=force_pool, use_memory_cache=False
            ):
                event = ev.get("event")
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                if event == "progress":
                    phase = str(data.get("phase") or "model")
                    message = str(data.get("message") or "研判中…")
                    try:
                        _set_progress(user_id, day, phase, message)
                    except Exception:
                        pass
                elif event == "done":
                    final = data
                elif event == "error":
                    raise RuntimeError(str(data.get("detail") or "晋级研判失败"))
            if final is None:
                raise RuntimeError("晋级研判未返回结果")
            trade_date = str(final.get("date") or day)[:10]
            upsert_daily(
                user_id,
                trade_date,
                {
                    "status": "ready",
                    "summary": str(final.get("summary") or ""),
                    "picks": list(final.get("picks") or []),
                    "candidate_count": int(final.get("candidate_count") or 0),
                    "as_of": final.get("as_of"),
                    "session": final.get("session") or {},
                    "theme_used": final.get("theme_used")
                    or {"news": False, "hot_sectors": False, "brief": False},
                    "progress": None,
                    "error": None,
                    "outcome": None,
                    "date": trade_date,
                },
            )
        except Exception as exc:
            logger.exception("limitup promote refresh failed user=%s day=%s", user_id, day)
            try:
                existing = _col().find_one(
                    {"user_id": user_id, "trade_date": day}, {"_id": 0}
                )
                upsert_daily(
                    user_id,
                    day,
                    {
                        "status": "error",
                        "summary": (existing or {}).get("summary") or "",
                        "picks": (existing or {}).get("picks") or [],
                        "candidate_count": int(
                            (existing or {}).get("candidate_count") or 0
                        ),
                        "as_of": (existing or {}).get("as_of"),
                        "session": (existing or {}).get("session") or {},
                        "theme_used": (existing or {}).get("theme_used")
                        or {"news": False, "hot_sectors": False, "brief": False},
                        "progress": None,
                        "error": f"{type(exc).__name__}: {exc}"[:400],
                        "outcome": None,
                    },
                )
            except Exception:
                pass
        finally:
            with _lock:
                _threads.pop(key, None)

    th = threading.Thread(target=_run, name=f"limitup-promote-{key}", daemon=True)
    with _lock:
        _threads[key] = th
    th.start()


def start_refresh(
    user_id: str,
    *,
    trade_date: str | None = None,
    force: bool = True,
    force_pool: bool = False,
) -> dict[str, Any]:
    """Start background LLM refresh; same-day overwrite when completed."""
    resolve_llm_credentials(user_id, "limitup")
    day = (trade_date or last_trading_day())[:10]
    # Prefer pool date when refreshing "today"
    if not trade_date:
        try:
            from ..limitup import get_limit_up

            pool = get_limit_up(force=False)
            pool_day = str(pool.get("date") or "")[:10]
            if pool_day:
                day = pool_day
        except Exception:
            pass

    existing = None
    try:
        existing = _col().find_one({"user_id": user_id, "trade_date": day}, {"_id": 0})
    except Exception:
        existing = None

    if (
        not force
        and existing
        and existing.get("status") == "running"
        and _thread_alive(user_id, day)
    ):
        return _public(existing, day)

    if (
        not force
        and existing
        and existing.get("status") == "ready"
        and isinstance(existing.get("picks"), list)
    ):
        return _public(existing, day)

    if (
        force
        and existing
        and existing.get("status") == "running"
        and _thread_alive(user_id, day)
    ):
        # Already refreshing; do not spawn duplicate
        return _public(existing, day)

    out = upsert_daily(
        user_id,
        day,
        {
            "status": "running",
            "summary": (existing or {}).get("summary") or "",
            "picks": (existing or {}).get("picks") or [],
            "candidate_count": int((existing or {}).get("candidate_count") or 0),
            "as_of": (existing or {}).get("as_of"),
            "session": (existing or {}).get("session") or {},
            "theme_used": (existing or {}).get("theme_used")
            or {"news": False, "hot_sectors": False, "brief": False},
            "progress": {"phase": "pool", "message": "正在获取当日封板池…"},
            "error": None,
            "outcome": None,
            "date": day,
        },
    )
    _spawn_refresh_thread(user_id, day, force_pool=force_pool)
    return out


def ensure_today(user_id: str) -> dict[str, Any]:
    """Return ready snapshot or start background refresh if missing."""
    resolve_llm_credentials(user_id, "limitup")
    day = last_trading_day()
    try:
        from ..limitup import get_limit_up

        pool = get_limit_up(force=False)
        pool_day = str(pool.get("date") or "")[:10]
        if pool_day:
            day = pool_day
    except Exception:
        pass

    doc = get_daily(user_id, day)
    if doc.get("status") == "ready":
        return doc
    if doc.get("status") == "running" and _thread_alive(user_id, day):
        return doc
    return start_refresh(user_id, trade_date=day, force=True, force_pool=False)


def compute_accuracy(
    user_id: str,
    trade_date: str,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Score T-day picks against T+1 limit-up pools (sealed or broken)."""
    day = trade_date[:10]
    doc = get_daily(user_id, day)
    if doc.get("status") != "ready":
        return {
            "trade_date": day,
            "ok": False,
            "error": "当日无就绪的晋级归档",
            "status": doc.get("status"),
        }

    parsed = parse_date(day)
    if parsed is None:
        return {"trade_date": day, "ok": False, "error": "无效交易日"}

    t1 = next_trading_day(parsed)
    today = date.today()
    if parse_date(t1) and parse_date(t1) > today:  # type: ignore[operator]
        return {
            "trade_date": day,
            "t1_date": t1,
            "ok": False,
            "pending": True,
            "error": "次一交易日尚未到来，暂无法统计",
            "pick_count": len(doc.get("picks") or []),
        }

    try:
        status_map = get_limit_up_status_map_for_date(t1)
    except Exception as exc:
        return {
            "trade_date": day,
            "t1_date": t1,
            "ok": False,
            "error": f"拉取次日涨停池失败: {type(exc).__name__}: {exc}",
            "pick_count": len(doc.get("picks") or []),
        }

    hits: list[dict[str, Any]] = []
    broken_hits: list[dict[str, Any]] = []
    misses: list[dict[str, Any]] = []
    pick_outcomes: list[dict[str, Any]] = []

    for pick in doc.get("picks") or []:
        if not isinstance(pick, dict):
            continue
        symbol = str(pick.get("symbol") or "").strip()
        if not symbol:
            continue
        t1_status = status_map.get(symbol)
        hit = t1_status in {"sealed", "broken"}
        row = {
            "symbol": symbol,
            "name": str(pick.get("name") or ""),
            "board_count": int(pick.get("board_count") or 1),
            "score": int(pick.get("score") or 0),
            "reason": str(pick.get("reason") or ""),
            "hit": hit,
            "t1_status": t1_status or "miss",
            "broken": t1_status == "broken",
        }
        pick_outcomes.append(row)
        if hit:
            hits.append(row)
            if t1_status == "broken":
                broken_hits.append(row)
        else:
            misses.append(row)

    pick_count = len(pick_outcomes)
    hit_count = len(hits)
    sealed_hit_count = sum(1 for r in hits if r.get("t1_status") == "sealed")
    broken_hit_count = len(broken_hits)
    hit_rate = (hit_count / pick_count) if pick_count else None

    outcome = {
        "t1_date": t1,
        "pick_count": pick_count,
        "hit_count": hit_count,
        "sealed_hit_count": sealed_hit_count,
        "broken_hit_count": broken_hit_count,
        "miss_count": len(misses),
        "hit_rate": hit_rate,
        "picks": pick_outcomes,
        "computed_at": _iso_now(),
    }

    if persist and doc.get("status") == "ready":
        try:
            raw = _col().find_one({"user_id": user_id, "trade_date": day}, {"_id": 0})
            if raw:
                upsert_daily(
                    user_id,
                    day,
                    {
                        "status": "ready",
                        "summary": raw.get("summary") or "",
                        "picks": raw.get("picks") or [],
                        "candidate_count": int(raw.get("candidate_count") or 0),
                        "as_of": raw.get("as_of"),
                        "session": raw.get("session") or {},
                        "theme_used": raw.get("theme_used")
                        or {"news": False, "hot_sectors": False, "brief": False},
                        "progress": None,
                        "error": None,
                        "outcome": outcome,
                        "date": day,
                    },
                )
        except Exception as exc:
            logger.warning("persist promote outcome failed: %s", exc)

    return {
        "trade_date": day,
        "t1_date": t1,
        "ok": True,
        "pending": False,
        "pick_count": pick_count,
        "hit_count": hit_count,
        "sealed_hit_count": sealed_hit_count,
        "broken_hit_count": broken_hit_count,
        "miss_count": len(misses),
        "hit_rate": hit_rate,
        "hits": hits,
        "broken_hits": broken_hits,
        "misses": misses,
        "outcome": outcome,
    }


def accuracy_summary(user_id: str, *, limit: int = 30) -> dict[str, Any]:
    """Aggregate hit rates across recent ready days (skips pending T+1)."""
    dates = list_dates(user_id, limit=limit)
    days: list[dict[str, Any]] = []
    total_picks = 0
    total_hits = 0
    total_broken = 0
    for row in dates:
        day = row["trade_date"]
        acc = compute_accuracy(user_id, day, persist=True)
        if not acc.get("ok"):
            days.append(
                {
                    "trade_date": day,
                    "ok": False,
                    "pending": bool(acc.get("pending")),
                    "error": acc.get("error"),
                    "pick_count": acc.get("pick_count") or row.get("pick_count") or 0,
                }
            )
            continue
        days.append(
            {
                "trade_date": day,
                "ok": True,
                "t1_date": acc.get("t1_date"),
                "pick_count": acc["pick_count"],
                "hit_count": acc["hit_count"],
                "sealed_hit_count": acc["sealed_hit_count"],
                "broken_hit_count": acc["broken_hit_count"],
                "miss_count": acc["miss_count"],
                "hit_rate": acc["hit_rate"],
            }
        )
        total_picks += int(acc["pick_count"] or 0)
        total_hits += int(acc["hit_count"] or 0)
        total_broken += int(acc["broken_hit_count"] or 0)

    return {
        "days": days,
        "total_picks": total_picks,
        "total_hits": total_hits,
        "total_broken_hits": total_broken,
        "hit_rate": (total_hits / total_picks) if total_picks else None,
    }
