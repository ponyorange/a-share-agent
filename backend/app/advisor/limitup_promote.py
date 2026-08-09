"""LLM: next-day limit-up promotion candidates from today's sealed pool."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..limitup import get_limit_up
from .agent.llm import build_chat_model
from .home_market import list_hot_sectors
from .home_news import get_or_build_home_news
from .home_news_brief import get_home_news_brief
from .llm_settings import resolve_llm_credentials

logger = logging.getLogger(__name__)

CONTEXT_MAX = 60
PICKS_MAX = 8
CACHE_TTL_SEC = 12 * 60
NEWS_TITLES_PER_GROUP = 5
HOT_SECTORS_TOP = 8

_cache: dict[str, Any] = {}  # key -> {ts, payload}

SYSTEM_PROMPT = (
    "你是A股短线打板研究助手。根据当日「仍封板」涨停池摘要，并结合提供的"
    "今日资讯（联播/宏观政策/题材）、热点板块与（若有）Agent 市场解读，"
    "推断次一交易日更可能继续涨停（晋级）的标的。"
    "优先关注：封板质量（时间/资金/炸板）与题材/政策/热点共振；"
    "热点缺失时仍可仅依据封板池研判。"
    "只用中文输出 JSON（不要 Markdown 围栏），格式："
    '{"summary":"一句话总览","picks":[{"symbol":"六位代码","name":"名称",'
    '"board_count":连板数整数,"score":1到5整数,"reason":"简短理由"}]}。'
    "要求：picks≤8；score 越高表示晋级相对更值得关注；"
    "只能从提供的候选里选，禁止编造代码；reason 可点明是否蹭到热点/政策；"
    "表述为研究观察，不保证次日涨停，非投资建议与下单指令。"
)

_GROUP_LABELS = {
    "cctv": "联播",
    "macro": "宏观政策",
    "index_sentiment": "指数情绪",
    "sectors": "题材热点",
    "web": "联网舆情",
}


def _cache_key(user_id: str, trade_date: str) -> str:
    return f"{user_id}:{trade_date}"


def build_promote_context(*, force_pool: bool = False) -> dict[str, Any]:
    """Build sealed-only context for LLM (prefer higher board counts)."""
    payload = get_limit_up(force=force_pool)
    today = list(payload.get("today") or [])
    sealed = [r for r in today if isinstance(r, dict) and r.get("status") == "sealed"]
    sealed.sort(
        key=lambda r: (
            -(int(r.get("board_count") or 1)),
            -(float(r.get("main_net_inflow") or 0.0) if r.get("main_net_inflow") is not None else 0.0),
            str(r.get("first_seal_time") or "99:99:99"),
            str(r.get("symbol") or ""),
        )
    )
    rows: list[dict[str, Any]] = []
    for r in sealed[:CONTEXT_MAX]:
        rows.append(
            {
                "symbol": str(r.get("symbol") or ""),
                "name": str(r.get("name") or ""),
                "board_count": int(r.get("board_count") or 1),
                "day_chg_pct": r.get("day_chg_pct"),
                "first_seal_time": r.get("first_seal_time"),
                "last_seal_time": r.get("last_seal_time"),
                "seal_funds": r.get("seal_funds"),
                "break_count": r.get("break_count"),
                "turnover_pct": r.get("turnover_pct"),
                "industry": r.get("industry"),
                "main_net_inflow": r.get("main_net_inflow"),
                "main_inflow": r.get("main_inflow"),
                "main_outflow": r.get("main_outflow"),
            }
        )
    return {
        "date": payload.get("date"),
        "as_of": payload.get("as_of"),
        "session": payload.get("session") or {},
        "candidates": rows,
        "candidate_count": len(sealed),
        "context_count": len(rows),
    }


def _clip_title(raw: Any, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    return text[:limit]


def build_theme_context(user_id: str) -> dict[str, Any]:
    """Compact news / hot-sector / brief context for promote LLM (best-effort)."""
    news_groups: dict[str, list[str]] = {}
    news_ok = False
    try:
        pack = get_or_build_home_news()
        groups = pack.get("groups") or {}
        for key, label in _GROUP_LABELS.items():
            g = groups.get(key) or {}
            if not g.get("ok"):
                continue
            titles: list[str] = []
            for item in list(g.get("items") or [])[:NEWS_TITLES_PER_GROUP]:
                if not isinstance(item, dict):
                    continue
                title = _clip_title(item.get("title"))
                if title:
                    titles.append(title)
            if titles:
                news_groups[label] = titles
                news_ok = True
    except Exception as exc:
        logger.warning("promote theme news skipped: %s", exc)

    hot_sectors: list[dict[str, Any]] = []
    sectors_ok = False
    try:
        hot = list_hot_sectors(top=HOT_SECTORS_TOP)
        for row in list(hot.get("items") or [])[:HOT_SECTORS_TOP]:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            item: dict[str, Any] = {"name": str(row.get("name"))[:40]}
            if row.get("change_pct") is not None:
                item["change_pct"] = row.get("change_pct")
            elif row.get("strength") is not None:
                item["strength"] = row.get("strength")
            hot_sectors.append(item)
        sectors_ok = bool(hot_sectors)
    except Exception as exc:
        logger.warning("promote theme sectors skipped: %s", exc)

    brief: dict[str, Any] | None = None
    brief_ok = False
    try:
        raw = get_home_news_brief(user_id)
        if str(raw.get("status") or "") == "ready" and (
            raw.get("summary") or raw.get("bullets") or raw.get("sectors")
        ):
            brief = {
                "summary": _clip_title(raw.get("summary"), 160),
                "bullets": [
                    _clip_title(x, 100)
                    for x in list(raw.get("bullets") or [])[:5]
                    if str(x or "").strip()
                ],
                "sectors": [
                    {
                        "name": str(s.get("name") or "")[:40],
                        "reason": _clip_title(s.get("reason"), 80),
                    }
                    for s in list(raw.get("sectors") or [])[:6]
                    if isinstance(s, dict) and s.get("name")
                ],
            }
            brief_ok = True
    except Exception as exc:
        logger.warning("promote theme brief skipped: %s", exc)

    return {
        "news_groups": news_groups,
        "hot_sectors": hot_sectors,
        "brief": brief,
        "used": {
            "news": news_ok,
            "hot_sectors": sectors_ok,
            "brief": brief_ok,
        },
    }


def _parse_promote_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    data: Any
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            data = {"summary": text[:200], "picks": []}
    if not isinstance(data, dict):
        data = {}
    return data


def _normalize_symbol(raw: Any) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6) if digits else ""


def filter_picks_against_context(
    raw_picks: list[Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_sym = {
        str(c.get("symbol") or ""): c
        for c in candidates
        if c.get("symbol")
    }
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_picks or []:
        if not isinstance(item, dict):
            continue
        sym = _normalize_symbol(item.get("symbol"))
        if not sym or sym not in by_sym or sym in seen:
            continue
        seen.add(sym)
        base = by_sym[sym]
        try:
            score = int(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        score = max(1, min(5, score))
        try:
            board = int(item.get("board_count") or base.get("board_count") or 1)
        except (TypeError, ValueError):
            board = int(base.get("board_count") or 1)
        reason = str(item.get("reason") or "").strip()
        reason = re.sub(r"\s+", " ", reason)[:120]
        out.append(
            {
                "symbol": sym,
                "name": str(item.get("name") or base.get("name") or sym)[:40],
                "board_count": board,
                "score": score,
                "reason": reason or "模型未给出具体理由",
            }
        )
        if len(out) >= PICKS_MAX:
            break
    out.sort(key=lambda x: (-x["score"], -x["board_count"], x["symbol"]))
    return out


def _chunk_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or ""))
        return "".join(parts)
    return str(value)


def _reasoning_delta(chunk: Any) -> str:
    for bag_name in ("additional_kwargs", "response_metadata"):
        bag = getattr(chunk, bag_name, None) or {}
        if not isinstance(bag, dict):
            continue
        for key in ("reasoning_content", "reasoning"):
            text = _chunk_text(bag.get(key))
            if text:
                return text
    return ""


def iter_promote_events(
    user_id: str,
    *,
    force: bool = False,
    force_pool: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield SSE events: progress* → thinking* → token* → done | error."""
    resolve_llm_credentials(user_id)
    yield {
        "event": "progress",
        "data": {"phase": "pool", "message": "正在获取当日封板池…"},
    }
    ctx = build_promote_context(force_pool=force_pool)
    trade_date = str(ctx.get("date") or "")[:10]
    key = _cache_key(user_id, trade_date or "unknown")
    now = time.monotonic()
    hit = _cache.get(key)
    if (
        not force
        and isinstance(hit, dict)
        and (now - float(hit.get("ts") or 0.0)) < CACHE_TTL_SEC
        and isinstance(hit.get("payload"), dict)
    ):
        payload = dict(hit["payload"])
        payload["from_cache"] = True
        yield {
            "event": "progress",
            "data": {"phase": "cache", "message": "命中研判缓存…"},
        }
        yield {"event": "done", "data": payload}
        return

    candidates = list(ctx.get("candidates") or [])
    if not candidates:
        empty = {
            "date": trade_date,
            "as_of": ctx.get("as_of"),
            "session": ctx.get("session"),
            "summary": "当前无封板标的，暂无晋级候选。",
            "picks": [],
            "candidate_count": 0,
            "from_cache": False,
            "theme_used": {"news": False, "hot_sectors": False, "brief": False},
        }
        _cache[key] = {"ts": now, "payload": empty}
        yield {"event": "done", "data": empty}
        return

    yield {
        "event": "progress",
        "data": {
            "phase": "theme",
            "message": "正在汇总资讯、政策与热点板块…",
        },
    }
    theme = build_theme_context(user_id)
    theme_used = dict(theme.get("used") or {})

    yield {
        "event": "progress",
        "data": {
            "phase": "model",
            "message": f"正在结合热点研判 {len(candidates)} 只封板摘要…",
        },
    }
    model = build_chat_model(user_id, temperature=0.2, streaming=True)
    human = (
        f"池日期 {trade_date}；封板总数 {ctx.get('candidate_count')}；"
        f"以下为摘要候选（已按连板优先截断至 {len(candidates)} 只）：\n"
        + json.dumps(candidates, ensure_ascii=False)
        + "\n\n今日资讯/政策/题材（标题摘要，可能不完整）：\n"
        + json.dumps(theme.get("news_groups") or {}, ensure_ascii=False)
        + "\n\n热点板块：\n"
        + json.dumps(theme.get("hot_sectors") or [], ensure_ascii=False)
        + "\n\n用户 Agent 市场解读（可能为空，status=ready 才有）：\n"
        + json.dumps(theme.get("brief"), ensure_ascii=False)
    )
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)]
    text_parts: list[str] = []
    stream_fn = getattr(model, "stream", None)
    if callable(stream_fn):
        for chunk in stream_fn(messages):
            reasoning = _reasoning_delta(chunk)
            if reasoning:
                yield {"event": "thinking", "data": {"delta": reasoning}}
            delta = _chunk_text(getattr(chunk, "content", None))
            if delta:
                text_parts.append(delta)
                yield {"event": "token", "data": {"delta": delta}}
        text = "".join(text_parts)
    else:
        resp = model.invoke(messages)
        reasoning = _reasoning_delta(resp)
        if reasoning:
            yield {"event": "thinking", "data": {"delta": reasoning}}
        text = _chunk_text(getattr(resp, "content", None) or resp or "")
        if text:
            yield {"event": "token", "data": {"delta": text}}

    yield {
        "event": "progress",
        "data": {"phase": "parse", "message": "正在解析研判结果…"},
    }
    parsed = _parse_promote_json(text)
    picks = filter_picks_against_context(list(parsed.get("picks") or []), candidates)
    summary = str(parsed.get("summary") or "").strip()[:200]
    if not summary:
        summary = f"从 {len(candidates)} 只封板摘要中选出 {len(picks)} 只晋级关注候选。"
    result = {
        "date": trade_date,
        "as_of": ctx.get("as_of"),
        "session": ctx.get("session"),
        "summary": summary,
        "picks": picks,
        "candidate_count": int(ctx.get("candidate_count") or 0),
        "from_cache": False,
        "theme_used": {
            "news": bool(theme_used.get("news")),
            "hot_sectors": bool(theme_used.get("hot_sectors")),
            "brief": bool(theme_used.get("brief")),
        },
    }
    _cache[key] = {"ts": time.monotonic(), "payload": result}
    yield {"event": "done", "data": result}


def generate_promote_picks(
    user_id: str,
    *,
    force: bool = False,
    force_pool: bool = False,
) -> dict[str, Any]:
    """Return promotion picks; uses per-user/day cache unless force."""
    last_error: str | None = None
    for ev in iter_promote_events(user_id, force=force, force_pool=force_pool):
        if ev.get("event") == "done":
            data = ev.get("data")
            if isinstance(data, dict):
                return data
        if ev.get("event") == "error":
            last_error = str((ev.get("data") or {}).get("detail") or "晋级研判失败")
    raise RuntimeError(last_error or "晋级研判失败")


def clear_promote_cache() -> None:
    _cache.clear()
