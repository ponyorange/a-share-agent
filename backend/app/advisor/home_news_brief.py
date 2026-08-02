"""Per-user home news Agent brief + refresh job."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..db import get_db
from .agent.llm import build_chat_model
from .agent.web_research import run_web_research
from .calendar_util import last_trading_day
from .home_news import get_or_build_home_news, merge_web_group
from .home_news_stock_picks import run_home_news_stock_picks
from .llm_settings import resolve_llm_credentials, web_tool_flags

_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}


def _col():
    return get_db().home_news_briefs


def _ensure_index() -> None:
    create = getattr(_col(), "create_index", None)
    if callable(create):
        create(
            [("user_id", 1), ("trade_date", 1)],
            unique=True,
            name="user_trade_date_1",
        )


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_key(user_id: str, day: str) -> str:
    return f"{user_id}:{day}"


def _idle(day: str) -> dict[str, Any]:
    return {
        "trade_date": day,
        "status": "idle",
        "summary": "",
        "bullets": [],
        "sectors": [],
        "symbols": [],
        "symbols_note": None,
        "updated_at": None,
        "error": None,
        "news_as_of": None,
    }


def _public(doc: dict[str, Any] | None, day: str) -> dict[str, Any]:
    if not doc:
        return _idle(day)
    symbols_note = doc.get("symbols_note")
    return {
        "trade_date": str(doc.get("trade_date") or day)[:10],
        "status": str(doc.get("status") or "idle"),
        "summary": str(doc.get("summary") or ""),
        "bullets": [str(x) for x in (doc.get("bullets") or [])][:5],
        "sectors": [
            {"name": str(x.get("name") or ""), "reason": str(x.get("reason") or "")}
            for x in (doc.get("sectors") or [])
            if isinstance(x, dict) and x.get("name")
        ][:8],
        "symbols": [
            {
                "symbol": str(x.get("symbol") or ""),
                "name": str(x.get("name") or ""),
                "reason": str(x.get("reason") or ""),
                "horizon": str(x.get("horizon") or "3-5d"),
            }
            for x in (doc.get("symbols") or [])
            if isinstance(x, dict)
            and re.fullmatch(r"\d{6}", str(x.get("symbol") or ""))
        ][:5],
        "symbols_note": str(symbols_note) if symbols_note else None,
        "updated_at": doc.get("updated_at"),
        "error": doc.get("error"),
        "news_as_of": doc.get("news_as_of"),
    }


def _load_brief(user_id: str, day: str) -> dict[str, Any] | None:
    return _col().find_one({"user_id": user_id, "trade_date": day}, {"_id": 0})


def _save_brief(user_id: str, day: str, fields: dict[str, Any]) -> dict[str, Any]:
    _ensure_index()
    payload = {
        **fields,
        "user_id": user_id,
        "trade_date": day,
        "updated_at": _iso_now(),
    }
    _col().update_one(
        {"user_id": user_id, "trade_date": day},
        {"$set": payload},
        upsert=True,
    )
    return _public(payload, day)


def get_home_news_brief(user_id: str, trade_date: str | None = None) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    return _public(_load_brief(user_id, day), day)


def _optional_knowledge_titles(user_id: str) -> list[str]:
    try:
        from .knowledge import list_items

        items = list_items(user_id, summary=True) or []
        out = []
        for it in items[:8]:
            t = str(it.get("title") or it.get("name") or "").strip()
            if t:
                out.append(t[:80])
        return out
    except Exception:
        return []


def _truncate_news_for_prompt(news: dict[str, Any]) -> dict[str, Any]:
    groups = {}
    for k, g in (news.get("groups") or {}).items():
        items = []
        for it in (g.get("items") or [])[:8]:
            if not isinstance(it, dict):
                continue
            items.append(
                {
                    "title": str(it.get("title") or "")[:120],
                    "summary": (str(it.get("summary") or "")[:160] or None),
                }
            )
        groups[k] = {"ok": bool(g.get("ok")), "items": items}
    return {"trade_date": news.get("trade_date"), "groups": groups}


def _parse_llm_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            data = {
                "summary": text[:200],
                "bullets": [],
                "sectors": [],
                "symbols": [],
            }
    if not isinstance(data, dict):
        data = {}
    bullets = [str(x)[:120] for x in (data.get("bullets") or []) if str(x).strip()][
        :5
    ]
    sectors = []
    for x in data.get("sectors") or []:
        if not isinstance(x, dict):
            continue
        name = str(x.get("name") or "").strip()
        if name:
            sectors.append(
                {"name": name[:40], "reason": str(x.get("reason") or "")[:80]}
            )
    return {
        "summary": str(data.get("summary") or "")[:200],
        "bullets": bullets,
        "sectors": sectors[:8],
        "symbols": [],
    }


def _maybe_fetch_web_items(user_id: str) -> list[dict[str, Any]]:
    flags = web_tool_flags(user_id)
    if not flags.get("web_research"):
        return []
    try:
        creds = resolve_llm_credentials(user_id)
        raw = run_web_research(
            creds["api_key"],
            "今日A股市场政策与舆情热点摘要（简体中文，列要点）",
        )
        text = str(raw or "").strip()
        if not text:
            return []
        items = []
        for line in text.splitlines():
            line = line.strip(" -*\t")
            if len(line) < 8:
                continue
            items.append(
                {
                    "title": line[:160],
                    "summary": None,
                    "published_at": None,
                    "url": None,
                    "tags": ["web"],
                }
            )
            if len(items) >= 8:
                break
        if not items:
            items = [
                {
                    "title": text[:160],
                    "summary": text[160:400] or None,
                    "published_at": None,
                    "url": None,
                    "tags": ["web"],
                }
            ]
        return items
    except Exception:
        return []


def generate_home_news_brief(user_id: str, news: dict[str, Any]) -> dict[str, Any]:
    resolve_llm_credentials(user_id)
    web_items = _maybe_fetch_web_items(user_id)
    if web_items:
        merge_web_group(
            str(news.get("trade_date") or ""),
            {
                "ok": True,
                "source": "web_research",
                "error": None,
                "items": web_items,
            },
        )
        groups = dict(news.get("groups") or {})
        groups["web"] = {
            "ok": True,
            "source": "web_research",
            "error": None,
            "items": web_items,
        }
        news = {**news, "groups": groups}

    model = build_chat_model(user_id, temperature=0.2, streaming=False)
    prompt = {
        "news": _truncate_news_for_prompt(news),
        "knowledge_titles": _optional_knowledge_titles(user_id),
    }
    system = (
        "你是投研助手。根据今日资讯包，用中文输出市场解读 JSON（不要 Markdown 围栏）。"
        '格式: {"summary":"一句话","bullets":["..."],"sectors":[{"name":"...","reason":"..."}]}。'
        "summary≤80字；bullets≤5条每条≤40字；sectors≤5；"
        "勿编造未提供的数据；勿给出保证收益或下单指令；"
        "表述为研究观察，非投资建议。"
    )
    resp = model.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=json.dumps(prompt, ensure_ascii=False, default=str)),
        ]
    )
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    brief = _parse_llm_json(text)
    picks = run_home_news_stock_picks(
        user_id,
        news=news,
        sectors=brief.get("sectors") or [],
    )
    brief["symbols"] = picks.get("symbols") or []
    brief["symbols_note"] = picks.get("symbols_note")
    return brief


def _thread_alive_for(user_id: str, day: str) -> bool:
    with _lock:
        th = _threads.get(_thread_key(user_id, day))
        return th is not None and th.is_alive()


def _spawn_refresh_thread(user_id: str, day: str) -> None:
    key = _thread_key(user_id, day)

    def _run() -> None:
        try:
            news = get_or_build_home_news(day)
            parsed = generate_home_news_brief(user_id, news)
            _save_brief(
                user_id,
                day,
                {
                    "status": "ready",
                    "summary": parsed["summary"],
                    "bullets": parsed["bullets"],
                    "sectors": parsed["sectors"],
                    "symbols": parsed["symbols"],
                    "symbols_note": parsed.get("symbols_note"),
                    "error": None,
                    "news_as_of": news.get("as_of"),
                },
            )
        except Exception as exc:
            _save_brief(
                user_id,
                day,
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                },
            )
        finally:
            with _lock:
                _threads.pop(key, None)

    th = threading.Thread(target=_run, name=f"home-news-brief-{key}", daemon=True)
    with _lock:
        _threads[key] = th
    th.start()


def start_home_news_brief_refresh(
    user_id: str, trade_date: str | None = None
) -> dict[str, Any]:
    day = (trade_date or last_trading_day())[:10]
    resolve_llm_credentials(user_id)

    existing = _load_brief(user_id, day)
    if (
        existing
        and existing.get("status") == "running"
        and _thread_alive_for(user_id, day)
    ):
        return _public(existing, day)

    out = _save_brief(
        user_id,
        day,
        {
            "status": "running",
            "summary": (existing or {}).get("summary") or "",
            "bullets": (existing or {}).get("bullets") or [],
            "sectors": (existing or {}).get("sectors") or [],
            "symbols": (existing or {}).get("symbols") or [],
            "symbols_note": (existing or {}).get("symbols_note"),
            "error": None,
            "news_as_of": (existing or {}).get("news_as_of"),
        },
    )
    _spawn_refresh_thread(user_id, day)
    return out
