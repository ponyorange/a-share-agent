"""Shared articles, per-user inbox, seen fingerprints, source scan state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId

from ...db import get_db
from .urls import article_open_url, normalize_title, normalize_url_key


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value) if value else None


def _as_aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return None


def _article_id(doc: dict[str, Any]) -> str:
    return str(doc.get("id") or doc.get("_id") or "")


def public_article(doc: dict[str, Any]) -> dict[str, Any]:
    interp = doc.get("interpretation")
    return {
        "id": _article_id(doc),
        "url_key": doc.get("url_key"),
        "url": doc.get("url"),
        "title": doc.get("title"),
        "source_key": doc.get("source_key"),
        "source_label": doc.get("source_label"),
        "body_excerpt": doc.get("body_excerpt"),
        "body_ok": bool(doc.get("body_ok")),
        "interpretation": interp if isinstance(interp, dict) else None,
        "interpret_status": doc.get("interpret_status") or "pending",
        "interpret_attempts": int(doc.get("interpret_attempts") or 0),
        "fetched_at": _iso(doc.get("fetched_at")),
        "interpreted_at": _iso(doc.get("interpreted_at")),
    }


def public_item(doc: dict[str, Any], article: dict[str, Any] | None = None) -> dict[str, Any]:
    art = article or {}
    interp = art.get("interpretation") if isinstance(art.get("interpretation"), dict) else {}
    return {
        "id": _article_id(doc),
        "article_id": str(doc.get("article_id") or ""),
        "user_id": doc.get("user_id"),
        "created_at": _iso(doc.get("created_at")),
        "read_at": _iso(doc.get("read_at")),
        "notified_at": _iso(doc.get("notified_at")),
        "notify_status": doc.get("notify_status") or "skipped",
        "title": art.get("title"),
        "source_label": art.get("source_label"),
        "source_key": art.get("source_key"),
        "url": article_open_url(str(art.get("url") or "")),
        "summary": (interp or {}).get("summary"),
        "direction": (interp or {}).get("direction"),
        "impact_score": (interp or {}).get("impact_score"),
        "sectors": (interp or {}).get("sectors") or [],
        "symbols": (interp or {}).get("symbols") or [],
        "body_ok": art.get("body_ok"),
    }


def mark_seen(
    source_key: str, url_key: str, title: str, *, now: datetime | None = None
) -> bool:
    current = now or _now()
    coll = get_db().policy_watch_seen
    existing = coll.find_one({"source_key": source_key, "url_key": url_key})
    if existing:
        return False
    coll.insert_one(
        {
            "source_key": source_key,
            "url_key": url_key,
            "title_norm": normalize_title(title),
            "first_seen_at": current,
        }
    )
    return True


def seed_seen(
    source_key: str, links: list[dict[str, Any]], *, now: datetime | None = None
) -> int:
    added = 0
    for link in links:
        url = str((link or {}).get("url") or "").strip()
        if not url:
            continue
        if mark_seen(
            source_key,
            normalize_url_key(url),
            str((link or {}).get("title") or ""),
            now=now,
        ):
            added += 1
    return added


def upsert_article(
    *,
    url: str,
    title: str,
    source_key: str,
    source_label: str,
    body_excerpt: str | None,
    body_ok: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    url_key = normalize_url_key(url)
    coll = get_db().policy_watch_articles
    existing = coll.find_one({"url_key": url_key})
    if existing:
        return public_article(existing)
    doc = {
        "url_key": url_key,
        "url": url,
        "title": title,
        "source_key": source_key,
        "source_label": source_label,
        "body_excerpt": body_excerpt,
        "body_ok": bool(body_ok),
        "interpretation": None,
        "interpret_status": "pending",
        "interpret_attempts": 0,
        "fetched_at": current,
        "interpreted_at": None,
    }
    result = coll.insert_one(doc)
    doc["_id"] = result.inserted_id
    return public_article(doc)


def _find_article_raw(article_id: str) -> dict[str, Any] | None:
    coll = get_db().policy_watch_articles
    doc = coll.find_one({"_id": article_id})
    if doc:
        return doc
    try:
        return coll.find_one({"_id": ObjectId(article_id)})
    except Exception:
        return None


def get_article(article_id: str) -> dict[str, Any] | None:
    doc = _find_article_raw(article_id)
    return public_article(doc) if doc else None


def get_article_by_url_key(url_key: str) -> dict[str, Any] | None:
    doc = get_db().policy_watch_articles.find_one({"url_key": url_key})
    return public_article(doc) if doc else None


def list_pending_interpret(*, limit: int = 5) -> list[dict[str, Any]]:
    cap = max(1, min(int(limit), 20))
    rows = list(
        get_db().policy_watch_articles.find(
            {"interpret_status": {"$in": ["pending", "failed"]}}
        )
    )
    out = []
    for row in rows:
        if int(row.get("interpret_attempts") or 0) >= 2:
            continue
        out.append(public_article(row))
        if len(out) >= cap:
            break
    return out


def save_interpretation(
    url_key: str, interpretation: dict[str, Any] | None, status: str
) -> None:
    payload: dict[str, Any] = {
        "interpret_status": status,
        "interpreted_at": _now(),
    }
    if interpretation is not None:
        payload["interpretation"] = interpretation
    update: dict[str, Any] = {"$set": payload}
    if status == "failed":
        update["$inc"] = {"interpret_attempts": 1}
    get_db().policy_watch_articles.update_one({"url_key": url_key}, update)


def insert_item(
    user_id: str,
    article_id: str,
    notify_status: str,
    *,
    notified_at: datetime | None = None,
) -> dict[str, Any] | None:
    coll = get_db().policy_watch_items
    existing = coll.find_one({"user_id": user_id, "article_id": article_id})
    if existing:
        return None
    doc = {
        "user_id": user_id,
        "article_id": article_id,
        "created_at": _now(),
        "read_at": None,
        "notified_at": notified_at,
        "notify_status": notify_status,
    }
    result = coll.insert_one(doc)
    doc["_id"] = result.inserted_id
    article = get_article(article_id)
    return public_item(doc, article)


def user_has_item(user_id: str, article_id: str) -> bool:
    return (
        get_db().policy_watch_items.find_one(
            {"user_id": user_id, "article_id": article_id}
        )
        is not None
    )


def list_unfanned_articles(user_id: str, source_keys: list[str]) -> list[dict[str, Any]]:
    keys = [str(k) for k in source_keys if str(k).strip()]
    if not keys:
        return []
    articles = list(
        get_db().policy_watch_articles.find({"source_key": {"$in": keys}})
    )
    have = {
        str(row.get("article_id"))
        for row in get_db().policy_watch_items.find({"user_id": user_id})
    }
    return [
        public_article(row)
        for row in articles
        if _article_id(row) not in have
    ]


def list_items(
    user_id: str,
    *,
    filter: str = "all",
    cursor: str | None = None,
    limit: int = 30,
    page: int | None = None,
) -> dict[str, Any]:
    cap = max(1, min(int(limit or 30), 50))
    rows = list(get_db().policy_watch_items.find({"user_id": user_id}))
    if filter == "emailed":
        rows = [r for r in rows if r.get("notify_status") == "sent"]
    elif filter == "inbox":
        rows = [r for r in rows if r.get("notify_status") != "sent"]

    def _created(row: dict[str, Any]) -> datetime:
        return _as_aware(row.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)

    rows.sort(key=_created, reverse=True)
    total = len(rows)
    if page is not None:
        page_n = max(1, int(page))
        last_page = max(1, (total + cap - 1) // cap) if total else 1
        if page_n > last_page:
            page_n = last_page
        offset = (page_n - 1) * cap
        sliced = rows[offset : offset + cap]
    else:
        page_n = 1
        if cursor:
            cur_dt = _as_aware(cursor)
            if cur_dt is not None:
                rows = [r for r in rows if _created(r) < cur_dt]
        sliced = rows[:cap]
    items = []
    for row in sliced:
        art = get_article(str(row.get("article_id") or ""))
        items.append(public_item(row, art))
    next_cursor = None
    if page is not None:
        if page_n * cap < total and sliced:
            next_cursor = _iso(sliced[-1].get("created_at"))
    elif len(rows) > cap and sliced:
        next_cursor = _iso(sliced[-1].get("created_at"))
    return {
        "items": items,
        "next_cursor": next_cursor,
        "page": page_n,
        "page_size": cap,
        "total": total,
    }


def mark_item_read(user_id: str, item_id: str) -> dict[str, Any]:
    coll = get_db().policy_watch_items
    doc = coll.find_one({"_id": item_id, "user_id": user_id})
    if doc is None:
        try:
            doc = coll.find_one({"_id": ObjectId(item_id), "user_id": user_id})
        except Exception:
            doc = None
    if doc is None:
        raise ValueError("收件箱条目不存在")
    now = _now()
    coll.update_one({"_id": doc["_id"]}, {"$set": {"read_at": now}})
    doc["read_at"] = now
    return public_item(doc, get_article(str(doc.get("article_id") or "")))


def recent_notified_titles(
    user_id: str, source_key: str, *, hours: int = 24
) -> list[str]:
    cutoff = _now() - timedelta(hours=max(1, int(hours)))
    titles: list[str] = []
    for row in get_db().policy_watch_items.find({"user_id": user_id}):
        if row.get("notify_status") != "sent":
            continue
        notified = _as_aware(row.get("notified_at"))
        if notified is None or notified < cutoff:
            continue
        art = get_article(str(row.get("article_id") or ""))
        if not art or art.get("source_key") != source_key:
            continue
        titles.append(str(art.get("title") or ""))
    return titles


def enrich_source_status(settings: dict[str, Any]) -> dict[str, Any]:
    """Overlay shared scan last_error onto the user's per-source status."""
    status = {
        str(k): dict(v)
        for k, v in (settings.get("source_status") or {}).items()
        if isinstance(v, dict)
    }
    keys = set(status)
    keys.update(str(x) for x in (settings.get("preset_ids") or []) if str(x).strip())
    for item in settings.get("custom_sources") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url:
            keys.add(normalize_url_key(url))
    for key in keys:
        scan = get_source_scan(key)
        if not scan:
            continue
        cur = dict(status.get(key) or {})
        cur["last_error"] = scan.get("last_error")
        if scan.get("last_fetch_at"):
            cur["last_fetch_at"] = scan["last_fetch_at"]
        status[key] = cur
    out = dict(settings)
    out["source_status"] = status
    return out


def get_source_scan(source_key: str) -> dict[str, Any] | None:
    doc = get_db().policy_watch_source_scans.find_one({"source_key": source_key})
    if not doc:
        return None
    return {
        "source_key": doc.get("source_key"),
        "last_fetch_at": _iso(doc.get("last_fetch_at")),
        "last_error": doc.get("last_error"),
        "raw_last_fetch_at": doc.get("last_fetch_at"),
    }


def touch_source_scan(source_key: str, **fields: Any) -> None:
    payload = dict(fields)
    get_db().policy_watch_source_scans.update_one(
        {"source_key": source_key},
        {"$set": payload},
        upsert=True,
    )
