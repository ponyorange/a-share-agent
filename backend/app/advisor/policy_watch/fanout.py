"""Fan out shared articles into per-user inboxes and optional email."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ...mail import send_email
from .config import policy_watch_config
from .mailer import build_policy_watch_email
from .schedule import in_user_scan_window, user_interval_elapsed
from .sensitivity import should_email
from .settings import list_enabled_settings, peek_verified_email, touch_settings
from .store import insert_item, list_unfanned_articles, recent_notified_titles
from .urls import normalize_url_key, titles_similar

logger = logging.getLogger(__name__)


def _source_keys(settings: dict[str, Any]) -> list[str]:
    keys = [str(x) for x in (settings.get("preset_ids") or []) if str(x).strip()]
    for item in settings.get("custom_sources") or []:
        if isinstance(item, dict) and item.get("url"):
            keys.append(normalize_url_key(str(item["url"])))
    return keys


def should_skip_similar(user_id: str, source_key: str, title: str) -> bool:
    hours = int(policy_watch_config().get("similar_title_hours") or 24)
    for prev in recent_notified_titles(user_id, source_key, hours=hours):
        if titles_similar(title, prev):
            return True
    return False


def _row_from_article(article: dict[str, Any]) -> dict[str, Any]:
    interp = article.get("interpretation") if isinstance(article.get("interpretation"), dict) else {}
    return {
        "title": article.get("title"),
        "source_label": article.get("source_label"),
        "url": article.get("url"),
        "summary": interp.get("summary"),
        "direction": interp.get("direction"),
        "sectors": interp.get("sectors") or [],
        "symbols": interp.get("symbols") or [],
        "body_ok": article.get("body_ok"),
    }


def fanout_user(settings: dict[str, Any], *, now: datetime | None = None) -> dict[str, int]:
    current = now or datetime.now(timezone.utc)
    if not in_user_scan_window(settings, now=current) or not user_interval_elapsed(
        settings, now=current
    ):
        return {"items": 0, "emailed": 0, "skipped": 1}
    user_id = str(settings.get("user_id") or "")
    keys = _source_keys(settings)
    pending_email: list[dict[str, Any]] = []
    pending_ids: list[str] = []
    inserted = 0
    skipped = 0
    for article in list_unfanned_articles(user_id, keys):
        article_id = str(article.get("id") or "")
        interp = article.get("interpretation")
        ready = article.get("interpret_status") == "ready" and isinstance(interp, dict)
        if not ready:
            insert_item(user_id, article_id, "skipped")
            inserted += 1
            skipped += 1
            continue
        if should_email(interp, str(settings.get("sensitivity") or "medium")) and not should_skip_similar(
            user_id, str(article.get("source_key") or ""), str(article.get("title") or "")
        ):
            pending_email.append(_row_from_article(article))
            pending_ids.append(article_id)
        else:
            insert_item(user_id, article_id, "skipped")
            inserted += 1
            skipped += 1

    emailed = 0
    to_addr = str(settings.get("notify_email") or "").strip() or peek_verified_email(user_id)
    if pending_ids:
        if to_addr:
            try:
                subject, body = build_policy_watch_email(pending_email)
                send_email(to_addr, subject, body)
                for article_id in pending_ids:
                    insert_item(user_id, article_id, "sent", notified_at=current)
                    inserted += 1
                emailed = 1
            except Exception as exc:
                logger.exception("policy watch email failed user=%s", user_id)
                for article_id in pending_ids:
                    insert_item(user_id, article_id, "failed")
                    inserted += 1
                touch_settings(user_id, last_error=f"mail: {type(exc).__name__}: {exc}")
        else:
            for article_id in pending_ids:
                insert_item(user_id, article_id, "skipped")
                inserted += 1
                skipped += 1
    touch_settings(user_id, last_fanout_at=current)
    return {"items": inserted, "emailed": emailed, "skipped": skipped}


def fanout_due_users(*, now: datetime | None = None) -> dict[str, int]:
    totals = {"items": 0, "emailed": 0, "skipped": 0, "errors": 0}
    for settings in list_enabled_settings():
        try:
            stats = fanout_user(settings, now=now)
            totals["items"] += int(stats.get("items") or 0)
            totals["emailed"] += int(stats.get("emailed") or 0)
            totals["skipped"] += int(stats.get("skipped") or 0)
        except Exception:
            logger.exception("policy watch fanout failed")
            totals["errors"] += 1
    return totals
