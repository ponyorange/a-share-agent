"""Shared daily news pack for advisor home."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from ..db import get_db
from .agent import unstructured as ustr
from .calendar_util import last_trading_day
from .home_market import list_hot_sectors

_build_lock = threading.Lock()
GROUP_KEYS = ("cctv", "macro", "index_sentiment", "sectors", "web")


def _col():
    return get_db().home_news_daily


def _ensure_index() -> None:
    create = getattr(_col(), "create_index", None)
    if callable(create):
        create("trade_date", unique=True, name="trade_date_1")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item(
    title: str,
    *,
    summary: str | None = None,
    published_at: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    t = (title or "").strip()
    if not t:
        return {}
    return {
        "title": t[:200],
        "summary": (summary[:400] if summary else None),
        "published_at": published_at,
        "url": url,
        "tags": tags,
    }


def _empty_group(
    source: str | None = None, error: str | None = None
) -> dict[str, Any]:
    return {"ok": False, "source": source, "error": error, "items": []}


def _fetch_cctv_group(day: str) -> dict[str, Any]:
    ymd = day.replace("-", "")[:8]
    try:
        raw = ustr.fetch_market_cctv_news(date=ymd, limit=10)
    except Exception as exc:
        return _empty_group("akshare.news_cctv", str(exc)[:300])
    items = []
    for row in raw.get("items") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("内容") or row.get("新闻") or "")
        summary_raw = str(row.get("summary") or row.get("内容") or "")
        it = _item(title, summary=summary_raw[:200] or None)
        if it:
            items.append(it)
    err = raw.get("error")
    return {
        "ok": bool(items) and not err,
        "source": raw.get("source") or "akshare.news_cctv",
        "error": None if items else (str(err)[:300] if err else "empty"),
        "items": items,
    }


def _fetch_macro_group(day: str) -> dict[str, Any]:
    _ = day
    try:
        raw = ustr.fetch_macro_china_snapshot(limit=3)
    except Exception as exc:
        return _empty_group("akshare.macro_china_*", str(exc)[:300])
    items = []
    for block_name, block in (raw.get("blocks") or {}).items():
        for row in (block.get("items") or [])[-2:]:
            if not isinstance(row, dict):
                continue
            vals = [str(v) for v in row.values() if v is not None and str(v).strip()]
            title = f"{block_name}: {' / '.join(vals[:3])}" if vals else ""
            it = _item(title, tags=["macro", str(block_name)])
            if it:
                items.append(it)
    return {
        "ok": bool(items),
        "source": raw.get("source") or "akshare.macro_china_*",
        "error": None if items else "empty",
        "items": items[:12],
    }


def _fetch_index_sentiment_group(day: str) -> dict[str, Any]:
    _ = day
    try:
        raw = ustr.fetch_index_news_sentiment(limit=12)
    except Exception as exc:
        return _empty_group("akshare.index_news_sentiment_scope", str(exc)[:300])
    items = []
    for row in raw.get("items") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("新闻标题") or row.get("name") or "")
        if not title:
            vals = [str(v) for v in row.values() if v is not None and str(v).strip()]
            title = " / ".join(vals[:3])
        it = _item(title, tags=["index_sentiment"])
        if it:
            items.append(it)
    err = raw.get("error")
    return {
        "ok": bool(items) and not err,
        "source": raw.get("source"),
        "error": None if items else (str(err)[:300] if err else "empty"),
        "items": items,
    }


def _fetch_sectors_group(day: str) -> dict[str, Any]:
    try:
        raw = list_hot_sectors(top=8, trade_date=day)
    except Exception as exc:
        return _empty_group("home_market.list_hot_sectors", str(exc)[:300])
    items = []
    for row in raw.get("items") or []:
        name = str(row.get("name") or "")
        pct = row.get("change_pct")
        summary = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else None
        it = _item(name, summary=summary, tags=["sector"])
        if it:
            items.append(it)
    return {
        "ok": bool(items) and bool(raw.get("ok")),
        "source": raw.get("source") or "sectors",
        "error": None if items else (raw.get("error") or "empty"),
        "items": items,
    }


def _build_news_groups(day: str) -> dict[str, Any]:
    return {
        "cctv": _fetch_cctv_group(day),
        "macro": _fetch_macro_group(day),
        "index_sentiment": _fetch_index_sentiment_group(day),
        "sectors": _fetch_sectors_group(day),
        "web": _empty_group(None, None),
    }


def _public(doc: dict[str, Any]) -> dict[str, Any]:
    groups = {}
    raw_groups = doc.get("groups") or {}
    for k in GROUP_KEYS:
        g = raw_groups.get(k) or _empty_group()
        groups[k] = {
            "ok": bool(g.get("ok")),
            "source": g.get("source"),
            "error": g.get("error"),
            "items": list(g.get("items") or []),
        }
    return {
        "trade_date": str(doc.get("trade_date") or "")[:10],
        "as_of": str(doc.get("as_of") or ""),
        "groups": groups,
    }


def _load_news_doc(day: str) -> dict[str, Any] | None:
    return _col().find_one({"trade_date": day}, {"_id": 0})


def _save_news_doc(doc: dict[str, Any]) -> None:
    _ensure_index()
    day = str(doc["trade_date"])[:10]
    _col().update_one(
        {"trade_date": day},
        {"$set": {**doc, "trade_date": day}},
        upsert=True,
    )


def merge_web_group(trade_date: str, web_group: dict[str, Any]) -> dict[str, Any]:
    """Update only the web group on an existing pack (create shell if missing)."""
    day = (trade_date or last_trading_day())[:10]
    with _build_lock:
        doc = _load_news_doc(day)
        if not doc:
            doc = {
                "trade_date": day,
                "as_of": _iso_now(),
                "groups": _build_news_groups(day),
            }
        groups = dict(doc.get("groups") or {})
        groups["web"] = {
            "ok": bool(web_group.get("ok")),
            "source": web_group.get("source") or "web_research",
            "error": web_group.get("error"),
            "items": list(web_group.get("items") or [])[:12],
        }
        doc = {**doc, "groups": groups, "as_of": _iso_now(), "trade_date": day}
        _save_news_doc(doc)
        return _public(doc)


def get_or_build_home_news(
    trade_date: str | None = None, *, force: bool = False
) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    if not force:
        cached = _load_news_doc(day)
        if cached:
            return _public(cached)
    with _build_lock:
        if not force:
            cached = _load_news_doc(day)
            if cached:
                return _public(cached)
        groups = _build_news_groups(day)
        doc = {"trade_date": day, "as_of": _iso_now(), "groups": groups}
        _save_news_doc(doc)
        return _public(doc)
