"""List-page discovery, structured presets, and first-run seeding."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from ..agent.unstructured import fetch_macro_china_snapshot, fetch_market_cctv_news
from ..agent.web_fetch_escalation import fetch_url_with_escalation, strip_fetch_via_meta
from .config import policy_watch_config
from .schedule import _in_trading_session, current_interval_minutes, in_user_scan_window
from .settings import clear_seeding, list_enabled_settings
from .store import (
    get_source_scan,
    mark_seen,
    seed_seen,
    touch_source_scan,
    upsert_article,
)
from .urls import normalize_title, normalize_url_key

_ARTICLE_PATH = re.compile(r"(content|zhengce|xwfb|/n/|\d{4})", re.I)


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = ""
        for key, value in attrs:
            if key.lower() == "href" and value:
                href = value
                break
        self._href = href or None
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._parts).strip()))
            self._href = None


def extract_article_links(
    html: str, page_url: str, *, max_links: int = 20
) -> list[dict[str, str]]:
    parser = _AnchorParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []
    page = urlparse(page_url)
    host = (page.hostname or "").lower()
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for href, title in parser.links:
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if (parsed.hostname or "").lower() != host:
            continue
        path = parsed.path or ""
        title_clean = " ".join((title or "").split())
        if not (_ARTICLE_PATH.search(path) or len(title_clean) >= 8):
            continue
        key = normalize_url_key(absolute)
        if key in seen:
            continue
        seen.add(key)
        out.append({"url": absolute, "title": title_clean or key})
        if len(out) >= max_links:
            break
    return out


def fetch_list_html(url: str) -> str:
    text = fetch_url_with_escalation(url)
    body = strip_fetch_via_meta(text or "")
    if body.startswith("错误："):
        raise RuntimeError(body)
    return body


def structured_links(preset_id: str) -> list[dict[str, str]]:
    if preset_id == "cctv":
        data = fetch_market_cctv_news(limit=10)
        day = str(data.get("date") or "")
        links: list[dict[str, str]] = []
        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("内容") or "").strip()
            if not title:
                continue
            links.append(
                {
                    "url": f"policy://cctv/{day}/{normalize_title(title)}",
                    "title": title,
                }
            )
        return links
    if preset_id == "macro":
        data = fetch_macro_china_snapshot(limit=3)
        links = []
        for block_name, block in (data.get("blocks") or {}).items():
            if not isinstance(block, dict):
                continue
            items = block.get("items") or []
            if not items:
                continue
            first = items[-1] if isinstance(items, list) else None
            if not isinstance(first, dict):
                continue
            title = str(
                first.get("title") or first.get("日期") or first.get("月份") or block_name
            ).strip()
            summary = str(first.get("今值") or first.get("最新值") or "").strip()
            label = f"{block_name} {title} {summary}".strip()
            links.append(
                {
                    "url": f"policy://macro/{block_name}/{normalize_title(label)}",
                    "title": label,
                }
            )
        return links
    return []


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


def source_due(
    source_key: str, interval_min: int, *, now: datetime | None = None
) -> bool:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    scan = get_source_scan(source_key)
    if not scan:
        return True
    last = _as_aware(scan.get("raw_last_fetch_at") or scan.get("last_fetch_at"))
    if last is None:
        return True
    return (current - last).total_seconds() >= float(max(1, interval_min)) * 60.0


def collect_due_source_keys(*, now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    cfg = policy_watch_config()
    presets = cfg.get("presets") if isinstance(cfg.get("presets"), dict) else {}
    floor = int(
        cfg.get("interval_trading_min")
        if _in_trading_session(current)
        else cfg.get("interval_offhours_min")
        or 15
    )
    cap = int(cfg.get("max_sources_per_tick") or 4)
    grouped: dict[str, dict[str, Any]] = {}
    for settings in list_enabled_settings():
        if not in_user_scan_window(settings, now=current):
            continue
        interval = max(floor, current_interval_minutes(settings, now=current))
        status = settings.get("source_status") or {}
        for preset_id in settings.get("preset_ids") or []:
            pid = str(preset_id).strip()
            if not pid:
                continue
            meta = presets.get(pid) if isinstance(presets.get(pid), dict) else {}
            spec = grouped.setdefault(
                pid,
                {
                    "source_key": pid,
                    "kind": "preset",
                    "preset_id": pid,
                    "url": meta.get("list_url"),
                    "label": meta.get("name") or pid,
                    "interval_min": interval,
                    "seeding": False,
                },
            )
            spec["interval_min"] = min(int(spec["interval_min"]), interval)
            if (status.get(pid) or {}).get("state") == "seeding":
                spec["seeding"] = True
        for custom in settings.get("custom_sources") or []:
            if not isinstance(custom, dict):
                continue
            url = str(custom.get("url") or "").strip()
            if not url:
                continue
            key = normalize_url_key(url)
            spec = grouped.setdefault(
                key,
                {
                    "source_key": key,
                    "kind": "custom",
                    "url": url,
                    "label": custom.get("title") or url,
                    "interval_min": interval,
                    "seeding": False,
                },
            )
            spec["interval_min"] = min(int(spec["interval_min"]), interval)
            if (status.get(key) or {}).get("state") == "seeding":
                spec["seeding"] = True
    due: list[dict[str, Any]] = []
    for spec in grouped.values():
        if spec.get("seeding") or source_due(
            str(spec["source_key"]), int(spec["interval_min"]), now=current
        ):
            due.append(spec)
        if len(due) >= cap:
            break
    return due


def _load_links(spec: dict[str, Any], *, max_links: int) -> list[dict[str, str]]:
    preset_id = str(spec.get("preset_id") or "")
    if spec.get("kind") == "preset" and preset_id in {"cctv", "macro"}:
        return structured_links(preset_id)[:max_links]
    url = str(spec.get("url") or "").strip()
    if not url:
        cfg = policy_watch_config()
        presets = cfg.get("presets") if isinstance(cfg.get("presets"), dict) else {}
        meta = presets.get(preset_id) if isinstance(presets.get(preset_id), dict) else {}
        url = str(meta.get("list_url") or "").strip()
    if not url:
        return []
    html = fetch_list_html(url)
    return extract_article_links(html, url, max_links=max_links)


def _fetch_body(url: str) -> tuple[str | None, bool]:
    if url.startswith("policy://"):
        return None, True
    if url.lower().split("?", 1)[0].endswith(".pdf"):
        return None, False
    try:
        text = strip_fetch_via_meta(fetch_url_with_escalation(url))
    except Exception:
        return None, False
    if text.startswith("错误："):
        return None, False
    clean = text.strip()
    if len(clean) < 40:
        return clean or None, False
    return clean, True


def ingest_source(spec: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    cfg = policy_watch_config()
    max_links = int(cfg.get("max_list_links") or 20)
    max_fetch = int(cfg.get("max_fetch_per_tick") or 5)
    source_key = str(spec.get("source_key") or "")
    label = str(spec.get("label") or source_key)
    try:
        links = _load_links(spec, max_links=max_links)
    except Exception as exc:
        err = str(exc)[:300]
        touch_source_scan(source_key, last_fetch_at=current, last_error=err)
        return {"new_articles": 0, "seeded": 0, "error": err}
    if not links:
        err = "该页不像列表，请换栏目 URL"
        touch_source_scan(source_key, last_fetch_at=current, last_error=err)
        return {"new_articles": 0, "seeded": 0, "error": err}
    if spec.get("seeding"):
        seeded = seed_seen(source_key, links, now=current)
        clear_seeding(source_key)
        touch_source_scan(source_key, last_fetch_at=current, last_error=None)
        return {"new_articles": 0, "seeded": seeded, "error": None}

    created = 0
    fetched = 0
    for link in links:
        url = str(link.get("url") or "")
        title = str(link.get("title") or url)
        if not mark_seen(source_key, normalize_url_key(url), title, now=current):
            continue
        body = None
        body_ok = False
        if fetched < max_fetch:
            raw, body_ok = _fetch_body(url)
            fetched += 1
            if url.startswith("policy://"):
                body = title
                body_ok = True
            else:
                body = raw
        upsert_article(
            url=url,
            title=title,
            source_key=source_key,
            source_label=label,
            body_excerpt=body,
            body_ok=body_ok,
            now=current,
        )
        created += 1
    touch_source_scan(source_key, last_fetch_at=current, last_error=None)
    return {"new_articles": created, "seeded": 0, "error": None}
