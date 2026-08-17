"""Channel B: knowledge-aware LLM market watch (single structured call)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .alerts import send_watch_digest_email
from .flow import get_flow_snapshot
from .rules import is_cooled_down, llm_cooldown_key, mark_cooldown

logger = logging.getLogger(__name__)

LLM_SYMBOL_LIMIT = 10
KNOWLEDGE_BODY_BUDGET = 6000

_WATCH_SYSTEM = """你是 A 股盘中看盘助手。根据提供的用户知识、大盘、行情、资金与新闻摘要，
给出结构化操作建议。只输出一个 JSON 对象，不要 Markdown，不要多余说明。
action 只能是 buy、sell、hold 之一。没有把握时用 hold。
JSON 形状：
{"symbols":[{"symbol":"代码","action":"buy|sell|hold","confidence":0.0,"rationale":"…","catalysts":["…"]}],"market_note":"大盘一句话"}
"""


def _parse_ts(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw
    if isinstance(raw, str) and raw.strip():
        text = raw.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def should_run_llm_watch(
    job: dict[str, Any],
    quotes_by_symbol: dict[str, dict[str, Any]],
    now: datetime,
) -> tuple[bool, list[str]]:
    if not job.get("llm_enabled"):
        return False, []
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        interval = int(job.get("llm_interval_sec") or 900)
    except (TypeError, ValueError):
        interval = 900
    try:
        anomaly = float(job.get("llm_anomaly_abs_chg") or 0.03)
    except (TypeError, ValueError):
        anomaly = 0.03
    baselines = dict(job.get("llm_symbol_baselines") or {})

    last = _parse_ts(job.get("last_llm_at"))
    interval_ok = last is None or (now - last).total_seconds() >= float(interval)

    anomaly_syms: list[str] = []
    scored: list[tuple[float, str]] = []
    for sym, q in quotes_by_symbol.items():
        chg = q.get("day_chg_pct")
        if chg is None:
            continue
        try:
            chg_f = float(chg)
        except (TypeError, ValueError):
            continue
        base = baselines.get(sym)
        if base is None:
            delta = abs(chg_f)
        else:
            try:
                delta = abs(chg_f - float(base))
            except (TypeError, ValueError):
                delta = abs(chg_f)
        scored.append((delta, sym))
        if delta >= anomaly:
            anomaly_syms.append(sym)

    if not interval_ok and not anomaly_syms:
        return False, []

    scored.sort(key=lambda x: x[0], reverse=True)
    ordered = [s for _, s in scored]
    # Prefer anomaly symbols first
    pick: list[str] = []
    for s in anomaly_syms + ordered:
        if s not in pick:
            pick.append(s)
        if len(pick) >= LLM_SYMBOL_LIMIT:
            break
    if not pick and quotes_by_symbol:
        pick = list(quotes_by_symbol.keys())[:LLM_SYMBOL_LIMIT]
    return True, pick


def parse_watch_response(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty_llm_response")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            raise ValueError("json_not_found") from None
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("json_not_object")
    return data


def actions_to_notify(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in parsed.get("symbols") or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "").strip().lower()
        if action not in ("buy", "sell"):
            continue
        sym = str(row.get("symbol") or "").strip()
        if not sym:
            continue
        out.append(
            {
                "symbol": sym,
                "action": action,
                "confidence": row.get("confidence"),
                "rationale": row.get("rationale") or "",
                "catalysts": list(row.get("catalysts") or [])[:8],
            }
        )
    return out


def _knowledge_block(user_id: str, knowledge_ids: list[str]) -> str:
    from ..knowledge import (
        format_always_knowledge_section,
        get_item,
        list_raw,
    )

    items = list_raw(user_id)
    parts = [format_always_knowledge_section(items)]
    used = len(parts[0] or "")
    for kid in knowledge_ids[:8]:
        doc = get_item(user_id, kid)
        if not doc:
            continue
        body = str(doc.get("body") or "")
        chunk = f"### {doc.get('title')}\n{body}"
        if used + len(chunk) > KNOWLEDGE_BODY_BUDGET:
            remain = max(0, KNOWLEDGE_BODY_BUDGET - used)
            chunk = chunk[:remain]
        parts.append(chunk)
        used += len(chunk)
        if used >= KNOWLEDGE_BODY_BUDGET:
            break
    return "\n\n".join(p for p in parts if p).strip() or "（无用户知识）"


def build_watch_context(
    user_id: str,
    job: dict[str, Any],
    symbols: list[str],
    quotes_by_symbol: dict[str, dict[str, Any]],
) -> str:
    knowledge = _knowledge_block(user_id, list(job.get("knowledge_ids") or []))

    market_txt = "（大盘不可用）"
    try:
        from app.market import featured_indices_snapshot, get_market

        snap = featured_indices_snapshot(get_market())
        rows = []
        for ix in (snap.get("indices") or [])[:8]:
            if not isinstance(ix, dict):
                continue
            rows.append(
                f"{ix.get('name') or ix.get('symbol')}: "
                f"价={ix.get('price')} 涨跌%={ix.get('change_pct')}"
            )
        if rows:
            market_txt = "\n".join(rows)
    except Exception as exc:
        market_txt = f"（大盘拉取失败: {type(exc).__name__}）"

    news_bits: list[str] = []
    try:
        from ..agent import unstructured as ustr

        sent = ustr.fetch_index_news_sentiment(limit=5)
        for it in (sent.get("items") or [])[:5]:
            if isinstance(it, dict):
                news_bits.append(str(it.get("title") or it)[:120])
            else:
                news_bits.append(str(it)[:120])
    except Exception:
        pass

    symbol_blocks: list[str] = []
    for sym in symbols:
        q = quotes_by_symbol.get(sym) or {}
        flow = get_flow_snapshot(sym)
        try:
            from ..agent import unstructured as ustr

            news = ustr.fetch_stock_news(sym, limit=3)
            headlines = []
            for it in (news.get("items") or [])[:3]:
                if isinstance(it, dict):
                    headlines.append(str(it.get("title") or "")[:100])
        except Exception:
            headlines = []
        symbol_blocks.append(
            "\n".join(
                [
                    f"## {sym} {q.get('name') or ''}",
                    f"price={q.get('price')} day_chg_pct={q.get('day_chg_pct')}",
                    f"flow_ok={flow.get('ok')} net_inflow={flow.get('net_inflow')} "
                    f"ratio={flow.get('ratio')}",
                    "news: " + ("；".join(headlines) if headlines else "—"),
                ]
            )
        )

    return "\n\n".join(
        [
            f"# 任务\n标题: {job.get('title')}\n范围: {job.get('scope')}\n备注: {job.get('note') or '—'}",
            f"# 用户知识\n{knowledge}",
            f"# 大盘\n{market_txt}",
            f"# 宏观/情绪新闻\n" + ("\n".join(news_bits) if news_bits else "—"),
            "# 标的\n" + "\n\n".join(symbol_blocks),
            "请输出 JSON。",
        ]
    )


def run_llm_watch(
    user_id: str,
    job: dict[str, Any],
    symbols: list[str],
    quotes_by_symbol: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
    cooldowns: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one structured LLM watch; may send a digest email for buy/sell."""
    now = now or datetime.now(timezone.utc)
    cds = dict(cooldowns or {})
    try:
        cooldown_sec = int(job.get("cooldown_sec") or 1800)
    except (TypeError, ValueError):
        cooldown_sec = 1800
    to_addr = str(job.get("notify_email") or "").strip()
    title = str(job.get("title") or "盯盘任务")
    job_id = str(job.get("id") or "")

    try:
        from ..agent.llm import build_chat_model
        from ..llm_settings import resolve_llm_credentials

        resolve_llm_credentials(user_id, "monitor")
        model = build_chat_model(user_id, slot="monitor", temperature=0.2, streaming=False)
        prompt = build_watch_context(user_id, job, symbols, quotes_by_symbol)
        msg = model.invoke(
            [
                {"role": "system", "content": _WATCH_SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
        text = getattr(msg, "content", None)
        if isinstance(text, list):
            text = "".join(
                str(x.get("text") if isinstance(x, dict) else x) for x in text
            )
        parsed = parse_watch_response(str(text or ""))
    except Exception as exc:
        logger.warning("llm watch failed job=%s: %s", job_id, exc)
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "notified": 0,
            "alert_cooldowns": cds,
            "parsed": None,
        }

    items = actions_to_notify(parsed)
    notify_items: list[dict[str, Any]] = []
    for it in items:
        key = llm_cooldown_key(str(it["symbol"]))
        if not is_cooled_down(cds, key, now, cooldown_sec):
            continue
        notify_items.append(it)

    notified = 0
    if notify_items and to_addr:
        try:
            send_watch_digest_email(
                to=to_addr,
                title=title,
                job_id=job_id,
                market_note=str(parsed.get("market_note") or ""),
                items=notify_items,
            )
            for it in notify_items:
                cds = mark_cooldown(cds, llm_cooldown_key(str(it["symbol"])), now)
            notified = len(notify_items)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"mail: {type(exc).__name__}: {exc}",
                "notified": 0,
                "alert_cooldowns": cds,
                "parsed": parsed,
            }

    return {
        "ok": True,
        "error": None,
        "notified": notified,
        "alert_cooldowns": cds,
        "parsed": parsed,
    }
