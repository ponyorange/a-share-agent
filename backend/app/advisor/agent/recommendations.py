"""生成 Agent「今日关注」：规则评分 + 新闻/宏观摘要，写入 agent_rec_snapshots。"""

from __future__ import annotations

import json
from typing import Any, Iterator

from langchain_core.messages import HumanMessage, SystemMessage

from ..llm_settings import resolve_llm_credentials
from . import unstructured as ustr
from .llm import build_chat_model
from ..agent_recs import save_agent_snapshot
from ..service import get_recommendations
from ..snapshots import effective_rec_date


def _pick_top_items(recs: dict[str, Any], per_board: int = 5) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    for bid, block in (recs.get("boards") or {}).items():
        items = list(block.get("items") or [])
        for it in items[:per_board]:
            if it.get("symbol"):
                picks.append({**it, "board": it.get("board") or bid})
    return picks


def _collect_news_for_items(
    items: list[dict[str, Any]], *, news_limit: int = 3
) -> dict[str, list[str]]:
    """symbol -> headlines。失败则空列表。"""
    out: dict[str, list[str]] = {}
    for it in items:
        sym = str(it.get("symbol") or "")
        if not sym:
            continue
        try:
            raw = ustr.fetch_stock_news(sym, limit=news_limit)
            headlines: list[str] = []
            for row in raw.get("items") or []:
                title = (
                    row.get("新闻标题")
                    or row.get("title")
                    or row.get("标题")
                    or ""
                )
                title = str(title).strip()
                if title:
                    headlines.append(title[:120])
            out[sym] = headlines
        except Exception:
            out[sym] = []
    return out


def _llm_enrich(
    user_id: str,
    *,
    trade_date: str,
    picks: list[dict[str, Any]],
    news_map: dict[str, list[str]],
    cctv: dict[str, Any],
    macro: dict[str, Any],
) -> dict[str, Any]:
    """调用用户配置的模型，返回 market_brief / macro_brief / notes{symbol: text}。"""
    resolve_llm_credentials(user_id, "home")  # raise if missing
    model = build_chat_model(user_id, slot="home", temperature=0.2, streaming=False)

    slim_picks = []
    for p in picks:
        layers = p.get("layer_scores") or {}
        slim_picks.append(
            {
                "symbol": p.get("symbol"),
                "name": p.get("name"),
                "board": p.get("board"),
                "industry": p.get("industry"),
                "score": p.get("score"),
                "action": p.get("action"),
                "day_chg_pct": p.get("day_chg_pct"),
                "tech_score": layers.get("tech_score"),
                "flow_score": layers.get("flow_score"),
                "sector_score": layers.get("sector_score"),
                "value_score": layers.get("value_score"),
                "market_score": layers.get("market_score"),
                "rationale": (p.get("rationale") or "")[:180],
                "news": (news_map.get(str(p.get("symbol"))) or [])[:3],
            }
        )
        graph = p.get("graph_signal")
        if isinstance(graph, dict) and (graph.get("action") or graph.get("error")):
            slim = {
                "action": graph.get("action"),
                "product_action": graph.get("product_action"),
            }
            if graph.get("error"):
                slim["error"] = str(graph.get("error"))[:120]
            slim_picks[-1]["graph_signal"] = slim
    cctv_items = [
        str(x.get("title") or x.get("内容") or x.get("新闻") or x)[:100]
        for x in (cctv.get("items") or [])[:8]
    ]
    prompt = {
        "trade_date": trade_date,
        "candidates": slim_picks,
        "cctv_headlines": cctv_items,
        "macro_blocks": {
            k: (v.get("items") or [])[-2:]
            for k, v in (macro.get("blocks") or {}).items()
        },
    }
    system = (
        "你是「投研助手」。根据多因子规则评分（tech量价/flow资金/sector板块/value估值，"
        "以及 market 市场状态缩放）候选 + 可选 graph_signal（图学习 BUY/HOLD/SELL）"
        " + 新闻标题 + 宏观/联播要点，用中文给出研究摘要。只输出 JSON，不要 Markdown 围栏。"
        '格式: {"market_brief":"...","macro_brief":"...","notes":{"600519":"一句话"}}。'
        "market_brief≤120字，可点出市场状态；macro_brief≤100字；"
        "notes 只覆盖候选代码，每条≤40字，勿只讲动量，可结合资金/板块/估值子分；"
        "有 graph_signal 时可点一句图方向，勿改写图分，勿让图替代多因子结论；"
        "勿编造未提供的数据；勿给出保证收益的表述。"
    )
    resp = model.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=json.dumps(prompt, ensure_ascii=False, default=str)),
        ]
    )
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试截取第一个 { ... }
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            data = {
                "market_brief": text[:200],
                "macro_brief": "",
                "notes": {},
            }
    notes = data.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}
    return {
        "market_brief": str(data.get("market_brief") or "")[:400],
        "macro_brief": str(data.get("macro_brief") or "")[:400],
        "notes": {str(k): str(v)[:120] for k, v in notes.items()},
    }


def iter_agent_recommendation_events(
    user_id: str,
    *,
    top: int = 10,
    force: bool = False,
    as_of: str | None = None,
) -> Iterator[dict[str, Any]]:
    """SSE：meta → progress* → done | error。写入 agent_rec_snapshots。"""
    trade_date = effective_rec_date(as_of)
    yield {
        "event": "meta",
        "data": {
            "trade_date": trade_date,
            "force": force,
            "phase": "start",
            "kind": "agent",
        },
    }

    try:
        resolve_llm_credentials(user_id, "home")
    except ValueError as exc:
        yield {"event": "error", "data": {"detail": str(exc)}}
        return

    yield {
        "event": "progress",
        "data": {"phase": "score", "detail": "正在按策略计算候选…", "done": 1, "total": 4},
    }
    try:
        recs = get_recommendations(
            top=top,
            as_of=as_of or trade_date,
            board=None,
            force_universe=force,
            user_id=user_id,
        )
    except Exception as exc:
        yield {
            "event": "error",
            "data": {"detail": f"评分失败: {type(exc).__name__}: {exc}"},
        }
        return

    picks = _pick_top_items(recs, per_board=min(5, top))
    yield {
        "event": "progress",
        "data": {
            "phase": "news",
            "detail": f"拉取资讯（{len(picks)} 只）…",
            "done": 2,
            "total": 4,
        },
    }
    news_map = _collect_news_for_items(picks, news_limit=3)
    for it in picks:
        sym = str(it.get("symbol") or "")
        it["news_headlines"] = news_map.get(sym) or []

    yield {
        "event": "progress",
        "data": {"phase": "macro", "detail": "拉取联播/宏观…", "done": 3, "total": 4},
    }
    cctv = ustr.fetch_market_cctv_news(limit=10)
    macro = ustr.fetch_macro_china_snapshot(limit=3)

    yield {
        "event": "progress",
        "data": {"phase": "llm", "detail": "投研助手总结中…", "done": 3, "total": 4},
    }
    try:
        enrich = _llm_enrich(
            user_id,
            trade_date=trade_date,
            picks=picks,
            news_map=news_map,
            cctv=cctv,
            macro=macro,
        )
    except Exception as exc:
        yield {
            "event": "error",
            "data": {"detail": f"Agent 总结失败: {type(exc).__name__}: {exc}"},
        }
        return

    notes = enrich.get("notes") or {}
    # 把 agent_note / news 写回 boards
    boards = recs.get("boards") or {}
    for bid, block in boards.items():
        new_items = []
        for it in block.get("items") or []:
            sym = str(it.get("symbol") or "")
            row = dict(it)
            row["agent_note"] = notes.get(sym) or notes.get(sym.lstrip("0")) or ""
            row["news_headlines"] = news_map.get(sym) or []
            new_items.append(row)
        boards[bid] = {**block, "items": new_items, "count": len(new_items)}

    payload = {
        **recs,
        "boards": boards,
        "trade_date": trade_date,
        "kind": "agent",
        "market_brief": enrich.get("market_brief") or "",
        "macro_brief": enrich.get("macro_brief") or "",
        "disclaimer": "规则评分 + Agent 资讯摘要，仅供研究参考，不构成投资建议。",
        "meta": {
            "picks": len(picks),
            "cctv_date": cctv.get("date"),
            "generated_by": "agent_recs",
        },
    }
    snap = save_agent_snapshot(payload, trade_date=trade_date, user_id=user_id)
    payload["snapshot"] = {
        "trade_date": snap.get("trade_date"),
        "updated_at": snap.get("updated_at"),
    }
    payload["from_cache"] = False

    yield {"event": "progress", "data": {"phase": "done", "done": 4, "total": 4}}
    yield {"event": "done", "data": payload}
