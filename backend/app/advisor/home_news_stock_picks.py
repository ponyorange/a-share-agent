"""News-driven stock picks for home Agent brief (no 今日关注 tools)."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

from .agent import tools as agent_tools
from .agent.llm import build_chat_model

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


STOCK_PICK_SYSTEM = (
    "你是投研助手，任务：根据「今日资讯」与相关板块，用工具核实后挑选未来约 3–5 个交易日"
    "可能有势头的 A 股观察标的。禁止使用或提及「今日关注」推荐列表。"
    "目标最多 5 只；证据不足可更少，禁止无依据硬凑。"
    "可用工具查成分股、涨幅榜、报价、个股新闻、联网（若已挂载）。"
    "最终只输出 JSON（不要 Markdown 围栏）："
    '{"symbols":[{"symbol":"600000","name":"...","reason":"须点明资讯/题材关联"}],'
    '"symbols_note":"可选说明"}。'
    "reason≤80字；勿保证收益；表述为研究观察。"
)


def _message_text(msg: Any) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content or msg or "")


def _truncate_news(news: dict[str, Any]) -> dict[str, Any]:
    groups = {}
    for k, g in (news.get("groups") or {}).items():
        items = []
        for it in (g.get("items") or [])[:6]:
            if not isinstance(it, dict):
                continue
            items.append(
                {
                    "title": str(it.get("title") or "")[:100],
                    "summary": (str(it.get("summary") or "")[:120] or None),
                }
            )
        groups[k] = {"ok": bool(g.get("ok")), "items": items}
    return {"trade_date": news.get("trade_date"), "groups": groups}


def run_home_news_stock_picks(
    user_id: str,
    *,
    news: dict[str, Any],
    sectors: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        tools = build_home_news_stock_pick_tools(user_id)
        model = build_chat_model(
            user_id,
            slot="home",
            temperature=0.2,
            streaming=False,
            request_timeout=90,
        )
        agent = create_react_agent(model, tools, prompt=STOCK_PICK_SYSTEM)
        payload = {
            "news": _truncate_news(news),
            "sectors": sectors[:8],
            "instruction": "请调用工具核实后输出观察股 JSON。",
        }
        result = agent.invoke(
            {
                "messages": [
                    HumanMessage(
                        content=json.dumps(payload, ensure_ascii=False, default=str)
                    )
                ]
            },
            config={"recursion_limit": 16},
        )
        messages = result.get("messages") if isinstance(result, dict) else None
        text = ""
        if messages:
            text = _message_text(messages[-1])
        return parse_stock_pick_payload(text)
    except Exception as exc:  # noqa: BLE001
        return {
            "symbols": [],
            "symbols_note": f"观察股生成失败：{type(exc).__name__}",
        }
