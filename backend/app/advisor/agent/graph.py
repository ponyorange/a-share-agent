"""LangGraph tool-calling agent for research / strategy copilots."""

from __future__ import annotations

from typing import Any, Iterator

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
)
from langgraph.prebuilt import create_react_agent

from .chat_store import (
    append_message,
    build_context_history,
    ensure_session,
)
from .llm import build_chat_model
from .tools import build_tools

SYSTEM_PROMPT = """你是「投研助手」，次日顾问产品中的 AI 投研副驾（DeepSeek）。
对外自称「投研助手」；语气专业、简洁、务实，不卖弄术语，结论先行再补依据。
你可按需读取用户全部业务数据（真实持仓、模拟盘、策略、推荐归档、龙虎榜、LLM 配置状态），
并用自然语言协助配置真实持仓与操作模拟盘；也可拉取 AKShare 新闻/公告/研报/宏观/经济日历与主要指数行情。
规则：
1. 用中文 Markdown 回答；买卖建议仅供研究参考。
2. 需要事实时优先调用工具，不要编造名单、新闻、持仓、收益、指数点位或历史最高点。
2b. 涨跌幅单位：工具里 day_chg_pct / pnl_pct 等是小数比例（0.19=涨19%），对用户展示必须写成 19% 或优先用已格式化字段 day_chg / pnl_chg；严禁把 0.19 直接写成 0.19%。
3. 写操作（改持仓、模拟盘下单/清仓/重置、改策略）必须：先读现状 → 向用户复述拟执行内容 → 用户明确确认后再调用对应工具并传 confirm=true。未确认只展示预览。
4. 分析真实持仓用 analyze_portfolio_positions；可再拉新闻/公告补叙事。
5. 用户问「今日关注 / 今日推荐」：先调用 get_today_recommendations，再按需拉联播/宏观；
   按板块列出标的，说明综合分并点到 tech/flow/sector/value/market 子分，勿只讲动量。
6. 宏观/政策：fetch_macro_china_snapshot、fetch_economic_calendar、fetch_market_cctv_news；无独立政治源，政治相关仅能间接参考联播等公开报道。
7. 指数点位/涨跌/大盘概况：必须先调用 fetch_market_indices，不得编造点位；该工具覆盖上证、深成、创业板、科创50、沪深300 等主要指数。
8. 指数历史最高/最低/距高点回撤：必须先调用 fetch_index_extremes（可传「科创50」或 000688），不得凭记忆或训练数据编造历史高点。
9. 个股日 K / MA5·MA10·MA20：必须先调用 fetch_symbol_daily_ma，不得编造均线数值。
10. 策略修改：propose 后展示 patch，用户确认再 apply_strategy_patch(confirm=true)。
11. 若无今日归档，引导去基础面板「今日关注」刷新候选池。
12. 用户可选知识：若系统提示含「用户可选知识目录」，需要细则时调用 load_knowledge(id)；勿编造目录外内容。必选知识已在系统提示中。
13. 回复末尾加一句免责声明。
"""


def build_system_prompt(user_id: str) -> str:
    from ..knowledge import build_knowledge_prompt_section

    extra = build_knowledge_prompt_section(user_id)
    if not extra:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT.rstrip()}\n\n{extra}\n"

DISCLAIMER = "以上内容仅供研究参考，不构成投资建议。"


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif hasattr(block, "get"):
                parts.append(str(block.get("text") or block))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _finalize_reply(text: str) -> str:
    text = (text or "").strip() or "（无回复）"
    if DISCLAIMER not in text:
        text = f"{text}\n\n{DISCLAIMER}"
    return text


def iter_agent_chat_events(
    user_id: str,
    message: str,
    *,
    session_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield SSE-ready events: meta → tool* → token* → done | error.

    Persists user/assistant messages; loads sliding-window history as context.
    """
    sid = ensure_session(user_id, session_id)
    msg = (message or "").strip()
    if not msg:
        yield {"event": "error", "data": {"detail": "消息不能为空"}}
        return

    append_message(user_id, sid, role="user", content=msg)
    history = build_context_history(user_id, sid)
    # history already includes the user message we just appended
    # Rebuild: all but last as prior, last is current — actually build_context
    # includes current user msg; create_react_agent needs full list ending with it.

    yield {
        "event": "meta",
        "data": {"session_id": sid, "context_messages": len(history)},
    }

    try:
        model = build_chat_model(user_id)
        tools = build_tools(user_id)
        agent = create_react_agent(model, tools, prompt=build_system_prompt(user_id))

        lc_messages: list[Any] = []
        for h in history:
            role = (h.get("role") or "").lower()
            content = h.get("content") or ""
            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            elif role in ("assistant", "ai"):
                lc_messages.append(AIMessage(content=content))

        tool_trace: list[dict[str, Any]] = []
        final_text = ""
        streamed_buf = ""
        streamed_any = False

        for mode, chunk in agent.stream(
            {"messages": lc_messages},
            stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                # (message_chunk, metadata) — token 级增量
                if not isinstance(chunk, tuple) or len(chunk) < 1:
                    continue
                m = chunk[0]
                if isinstance(m, ToolMessage):
                    content = _extract_text(m.content)[:2000]
                    name = getattr(m, "name", None) or "tool"
                    tool_trace.append({"tool": name, "content": content})
                    # 工具调用后下一轮模型输出才是最终回答
                    streamed_buf = ""
                    yield {
                        "event": "tool",
                        "data": {"tool": name, "content": content[:1200]},
                    }
                    continue

                # AIMessageChunk.content 是本轮增量，不是累计全文
                if isinstance(m, AIMessageChunk):
                    # 纯 tool-call 分片不向 UI 打字
                    has_tool_bits = bool(
                        getattr(m, "tool_call_chunks", None)
                        or getattr(m, "tool_calls", None)
                    )
                    text = _extract_text(m.content)
                    if text and not has_tool_bits:
                        streamed_buf += text
                        streamed_any = True
                        yield {"event": "token", "data": {"delta": text}}
                    continue

                if isinstance(m, AIMessage):
                    text = _extract_text(m.content)
                    if text and not getattr(m, "tool_calls", None):
                        if text.startswith(streamed_buf) and len(text) > len(
                            streamed_buf
                        ):
                            delta = text[len(streamed_buf) :]
                            streamed_buf = text
                            if delta:
                                streamed_any = True
                                yield {"event": "token", "data": {"delta": delta}}
                        elif not streamed_buf:
                            streamed_buf = text
                            streamed_any = True
                            yield {"event": "token", "data": {"delta": text}}
            elif mode == "updates":
                if not isinstance(chunk, dict):
                    continue
                for _node, update in chunk.items():
                    if not isinstance(update, dict):
                        continue
                    msgs = update.get("messages") or []
                    for m in msgs:
                        if isinstance(m, ToolMessage):
                            content = _extract_text(m.content)[:2000]
                            name = getattr(m, "name", None) or "tool"
                            if not any(
                                t.get("tool") == name and t.get("content") == content
                                for t in tool_trace[-3:]
                            ):
                                tool_trace.append({"tool": name, "content": content})
                                yield {
                                    "event": "tool",
                                    "data": {
                                        "tool": name,
                                        "content": content[:1200],
                                    },
                                }
                        elif isinstance(m, AIMessage) and m.content and not m.tool_calls:
                            text = _extract_text(m.content)
                            if text:
                                final_text = text
                                # 新一轮最终回答开始前，流式缓冲可重置对齐
                                if not streamed_buf:
                                    streamed_buf = text

        # Fallback non-stream invoke if nothing
        if not final_text and not streamed_buf:
            result = agent.invoke({"messages": lc_messages})
            for m in reversed(result.get("messages") or []):
                if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
                    final_text = _extract_text(m.content)
                    break
                if isinstance(m, ToolMessage):
                    tool_trace.append(
                        {
                            "tool": getattr(m, "name", None) or "tool",
                            "content": _extract_text(m.content)[:2000],
                        }
                    )

        reply = _finalize_reply(final_text or streamed_buf)
        if not streamed_any and reply:
            yield {"event": "token", "data": {"delta": reply}}

        append_message(
            user_id,
            sid,
            role="assistant",
            content=reply,
            tool_trace=tool_trace[-20:],
        )
        yield {
            "event": "done",
            "data": {
                "session_id": sid,
                "reply": reply,
                "tool_trace": tool_trace[-12:],
                "disclaimer": DISCLAIMER,
            },
        }
    except Exception as exc:
        yield {
            "event": "error",
            "data": {"detail": f"{type(exc).__name__}: {exc}", "session_id": sid},
        }


def run_agent_chat(
    user_id: str,
    message: str,
    *,
    session_id: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Non-streaming wrapper (ignores client history; uses persisted session)."""
    del history  # persisted context is source of truth
    reply = ""
    tool_trace: list[dict[str, Any]] = []
    sid = None
    for ev in iter_agent_chat_events(user_id, message, session_id=session_id):
        if ev["event"] == "meta":
            sid = ev["data"].get("session_id")
        elif ev["event"] == "done":
            reply = ev["data"].get("reply") or ""
            tool_trace = ev["data"].get("tool_trace") or []
            sid = ev["data"].get("session_id") or sid
        elif ev["event"] == "error":
            raise ValueError(ev["data"].get("detail") or "Agent 失败")
    return {
        "session_id": sid,
        "reply": reply,
        "tool_trace": tool_trace,
        "disclaimer": DISCLAIMER,
    }
