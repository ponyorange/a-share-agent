"""Structured LLM interpretation for newly discovered articles."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ...kline import normalize_symbol
from ..agent.llm import build_chat_model
from ..llm_settings import public_llm_settings
from .config import policy_watch_config
from .settings import list_enabled_settings
from .store import list_pending_interpret, save_interpretation

_DIRECTIONS = frozenset({"up", "down", "mixed", "unclear"})
_CATEGORIES = frozenset({"policy", "regulation", "macro", "news", "other"})

_SYSTEM = (
    "你是投研助手。根据一篇已抓取的政策/新闻，用中文输出 JSON（不要 Markdown 围栏）。"
    '格式: {"impact_score":0.0,"direction":"up|down|mixed|unclear",'
    '"summary":"一句话","sectors":[{"name":"...","reason":"..."}],'
    '"symbols":[{"symbol":"600000","name":"...","reason":"...","direction":"up"}],'
    '"category":"policy|regulation|macro|news|other"}。'
    "impact_score 为 0 到 1；sectors≤5；symbols≤8；"
    "股票代码必须是 A 股 6 位数字，禁止编造；"
    "表述为研究观察，非投资建议。"
)


def parse_interpretation(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("解读不是 JSON")
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("解读不是对象")
    try:
        score = float(data.get("impact_score"))
    except (TypeError, ValueError) as exc:
        raise ValueError("缺少 impact_score") from exc
    score = max(0.0, min(1.0, score))
    direction = str(data.get("direction") or "unclear").strip()
    if direction not in _DIRECTIONS:
        direction = "unclear"
    category = str(data.get("category") or "other").strip()
    if category not in _CATEGORIES:
        category = "other"
    sectors: list[dict[str, str]] = []
    for item in data.get("sectors") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        sectors.append({"name": name[:40], "reason": str(item.get("reason") or "")[:80]})
        if len(sectors) >= 5:
            break
    symbols = verify_symbols(list(data.get("symbols") or []))[:8]
    return {
        "impact_score": score,
        "direction": direction,
        "summary": str(data.get("summary") or "").strip()[:200],
        "sectors": sectors,
        "symbols": symbols,
        "category": category,
    }


def verify_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in symbols:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        raw_code = str(item.get("symbol") or "").strip()
        code = ""
        verified = False
        if raw_code:
            try:
                code = normalize_symbol(raw_code)
                verified = True
            except ValueError:
                code = ""
        if not code and not name:
            continue
        if not code and name:
            verified = False
        direction = str(item.get("direction") or "").strip()
        if direction not in _DIRECTIONS:
            direction = "unclear"
        out.append(
            {
                "symbol": code or None,
                "name": name or code,
                "reason": str(item.get("reason") or "")[:80],
                "direction": direction,
                "verified": verified,
            }
        )
        if len(out) >= 8:
            break
    return out


def pick_interpret_user_id() -> str | None:
    for row in list_enabled_settings():
        uid = str(row.get("user_id") or "")
        if not uid:
            continue
        try:
            if public_llm_settings(uid).get("configured"):
                return uid
        except Exception:
            continue
    return None


def interpret_pending(*, limit: int | None = None) -> dict[str, int]:
    cfg = policy_watch_config()
    cap = int(limit or cfg.get("max_fetch_per_tick") or 5)
    user_id = pick_interpret_user_id()
    if not user_id:
        return {"ok": 0, "failed": 0, "skipped": 1}
    stats = {"ok": 0, "failed": 0, "skipped": 0}
    model = None
    for article in list_pending_interpret(limit=cap):
        try:
            if model is None:
                model = build_chat_model(user_id, temperature=0.1, streaming=False)
            excerpt = str(article.get("body_excerpt") or "")
            max_chars = int(cfg.get("max_article_chars") or 8000)
            prompt = {
                "source": article.get("source_label"),
                "title": article.get("title"),
                "body": excerpt[:max_chars],
                "body_ok": article.get("body_ok"),
            }
            resp = model.invoke(
                [
                    SystemMessage(content=_SYSTEM),
                    HumanMessage(content=json.dumps(prompt, ensure_ascii=False)),
                ]
            )
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            parsed = parse_interpretation(text)
            save_interpretation(str(article.get("url_key") or ""), parsed, "ready")
            stats["ok"] += 1
        except Exception:
            save_interpretation(str(article.get("url_key") or ""), None, "failed")
            stats["failed"] += 1
    return stats
