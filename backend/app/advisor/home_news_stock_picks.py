"""News-driven stock picks for home Agent brief (no 今日关注 tools)."""

from __future__ import annotations

import json
import re
from typing import Any

from .agent import tools as agent_tools

STOCK_PICK_BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "get_today_recommendations",
        "get_recommendation_archive",
        "list_recommendation_dates",
    }
)

STOCK_PICK_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "get_stock_quotes",
        "get_leaderboard_brief",
        "fetch_stock_news",
        "fetch_symbol_daily_ma",
        "delegate_data_task",
        "register_tool_dataset",
        "run_python_script",
        "web_research",
        "web_search",
        "fetch_url",
    }
)


def build_home_news_stock_pick_tools(user_id: str) -> list[Any]:
    """Whitelist tools for stock-pick agent; always block recommendation tools."""
    raw = agent_tools.build_tools(user_id, exclude=STOCK_PICK_BLOCKED_TOOLS)
    return [
        t
        for t in raw
        if getattr(t, "name", None) in STOCK_PICK_ALLOWED_TOOLS
    ]


def parse_stock_pick_payload(text: str) -> dict[str, Any]:
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
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
    if not isinstance(data, dict):
        data = {}
    symbols: list[dict[str, str]] = []
    for x in data.get("symbols") or []:
        if not isinstance(x, dict):
            continue
        sym = re.sub(r"\D", "", str(x.get("symbol") or ""))[-6:]
        if not re.fullmatch(r"\d{6}", sym):
            continue
        reason = str(x.get("reason") or "").strip()
        if not reason:
            continue
        symbols.append(
            {
                "symbol": sym,
                "name": str(x.get("name") or "")[:40],
                "reason": reason[:120],
                "horizon": "3-5d",
            }
        )
        if len(symbols) >= 5:
            break
    note = str(data.get("symbols_note") or "").strip()[:200] or None
    if not symbols and not note:
        note = "暂无足够证据的观察股"
    return {"symbols": symbols, "symbols_note": note}
