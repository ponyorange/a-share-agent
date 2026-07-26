"""LangGraph tool-calling agent for research / strategy copilots."""

from __future__ import annotations

import contextvars
import queue
import threading
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.prebuilt import create_react_agent

from .chat_store import (
    append_message,
    build_context_history,
    ensure_session,
    session_exists,
)
from .llm import build_chat_model
from .progress import ProgressValidationError, bind_progress_sink, progress_to_tool_trace
from .tools import build_tools

_STREAM_END = object()
_EVENT_QUEUE_SIZE = 128
_PROGRESS_TRACE_LIMIT = 20
_DELEGATE_DATA_TASK_NAME = "delegate_data_task"
_DELEGATE_DATA_TASK_SAFE_CONTENT = "数据子 Agent 已返回结构化结果"
_AGENT_ERROR_DETAIL = "Agent 执行失败"

SYSTEM_PROMPT = """你是次日顾问产品中的 AI 投研副驾（DeepSeek）。
默认对外自称「投研助手」；若下文「用户系统提示词」另有称呼、角色、对用户称呼或性格要求，必须以用户设定为准，覆盖上述默认自称。
语气默认专业、简洁、务实，不卖弄术语，结论先行再补依据；若用户系统提示词另有语气要求，以用户设定为准。
你可按需读取用户全部业务数据（真实持仓、模拟盘、策略、推荐归档、龙虎榜、LLM 配置状态），
并用自然语言协助配置真实持仓与操作模拟盘；也可拉取 AKShare 新闻/公告/研报/宏观/经济日历与主要指数行情。
规则：
1. 用中文 Markdown 回答；买卖建议仅供研究参考。
2. 需要事实时优先调用工具，不要编造名单、新闻、持仓、收益、指数点位或历史最高点。
2b. 涨跌幅单位：工具里 day_chg_pct / pnl_pct 等是小数比例（0.19=涨19%），对用户展示必须写成 19% 或优先用已格式化字段 day_chg / pnl_chg；严禁把 0.19 直接写成 0.19%。
3. 写操作（改持仓、模拟盘下单/清仓/重置、改策略、发送邮件摘要）必须：先读现状 → 向用户复述拟执行内容 → 用户明确确认后再调用对应工具并传 confirm=true。未确认只展示预览。
4. 分析真实持仓用 analyze_portfolio_positions；可再拉新闻/公告补叙事。
5. 用户问「今日关注 / 今日推荐」：先调用 get_today_recommendations，再按需拉联播/宏观；
   按板块列出标的，说明综合分并点到 tech/flow/sector/value/market 子分，勿只讲动量。
6. 宏观/政策：fetch_macro_china_snapshot、fetch_economic_calendar、fetch_market_cctv_news；无独立政治源，政治相关仅能间接参考联播等公开报道。
7. 指数点位/涨跌/大盘概况：必须先调用 fetch_market_indices，不得编造点位；该工具覆盖上证、深成、创业板、科创50、沪深300 等主要指数。
8. 指数历史最高/最低/距高点回撤：必须先调用 fetch_index_extremes（可传「科创50」或 000688），不得凭记忆或训练数据编造历史高点。
9. 个股日 K / MA5·MA10·MA20：必须先调用 fetch_symbol_daily_ma，不得编造均线数值。
10. 策略修改：propose 后展示 patch，用户确认再 apply_strategy_patch(confirm=true)。
11. 若无今日归档，引导去基础面板「今日关注」刷新候选池。
12. 用户知识：消息上下文中可能含「用户必选知识」，须遵守；若系统提示含「用户可选知识目录」，需要细则时调用 load_knowledge(id)；勿编造目录外内容。
13. 知识库写入/更新/删除：先整理内容或用 list_knowledge 定位 → 调用 save_knowledge / delete_knowledge 且 confirm=false 展示预览 → 用户明确同意后再 confirm=true。未指定可选/必选时先询问。匹配多条时列出候选，勿猜测。未确认不得声称已保存。
14. 回复末尾加一句免责声明。
15. 涉及通用行情、财务、宏观、资讯等 Provider 外部数据，或跨表/跨源计算时，自动调用 delegate_data_task；
   持仓、模拟盘、策略和推荐归档仍使用现有专用工具，规则 4-12 中明确指定的专用工具仍优先。
16. 数据子 Agent 返回 failures、warnings 或 truncated 时必须如实展示；
   数据不足时明确无法完成，严禁自行补齐或编造。
17. 知识规则回测/调优：先询问优化目标（A 收益 / B 夏普 / C 约束下收益，默认 C）与标的（可跳过用默认池）→
   起草规则前必须先调用 list_rule_factors（勿凭记忆声称「不支持量比」）→
   再 compile_knowledge_rules → run_rule_backtest 或 optimize_knowledge_rules →
   必须同时向用户展示样本内与样本外指标；objective=C 且 feasible=false 时说明无可行解，不得写库 →
   写回知识库：默认新建（标题可加「（回测优化）」），正文含自然语言结论 + 样本内外指标 + RuleSpec 附录，
   经 save_knowledge(confirm=false) 预览，用户确认后再 confirm=true。未确认不得声称已写入。
   量能/阴阳已支持：vol_ratio（lookback 2..60，默认5；别名 volume_ratio/vol/volume）、is_yin、is_yang；勿用 turn。
   术语映射（必须遵守）：
   - 缩量 = 当日量明显小于近 N 日均量 → vol_ratio < 阈值（常用 1.0，更严 0.8）
   - 放量 = 当日量明显大于近 N 日均量 → vol_ratio > 阈值（常用 1.5 或 2.0）
   - vol_ratio 定义 = 当日 volume ÷ 前 N 日均 volume（不含当日）；1.0 表示持平均量
   - 缩量阴：is_yin>=1 且 vol_ratio lookback=N op=< value=1.0；放量阳：is_yang>=1 且 vol_ratio > 1.5
   用户说缩量/放量时必须用 vol_ratio 表达，禁止声称不支持或改用绝对 volume/turn。
18. 小计算、试跑、对本轮小结果二次加工：使用 run_python_script；
   需要喂入本轮工具 JSON 时先 register_tool_dataset。
   Provider 外部数据/跨源/大表仍用 delegate_data_task（规则 15）。
   沙箱已预置 pd/np（可直接用或 import pandas/numpy）；
   仅允许 pandas/numpy/math/statistics/datetime/time/zoneinfo。
   解读优先 result，其次 stdout/stderr；禁止编造未工具返回的数据进沙箱。
19. 将聊天摘要发到用户邮箱：使用 send_chat_summary_email；
   先 confirm=false 预览收件人/主题/摘要，用户明确同意后再 confirm=true。
   无已验证邮箱时引导去个人资料页绑定；禁止编造收件人。
20. 联网：若已挂载 web_research，综合调研优先用之；若已挂载 web_search/fetch_url，
   需自行筛选来源时先 web_search 再 fetch_url。引用须带来源 URL，禁止编造链接。
   A 股结构化新闻/联播/指数点位仍优先专用工具（规则 6–9）。
21. 回答「现在几点 / 今天几号 / 当前日期」等：以系统提示「当前时间」一节为准（北京时间），
   不要编造，也不必为此调用 Python；需要脚本内取时可 import datetime/time/zoneinfo。
22. 回答「当前用什么模型」：以系统提示「运行配置」一节的模型名为准；也可调用 get_user_data_overview 核对。
"""

_USER_SYSTEM_PROMPT_HEADER = """## 用户系统提示词
（优先级高于上文默认自称/语气；工具调用、确认流程与免责声明等产品规则仍须遵守。）
"""

_AGENT_TZ = ZoneInfo("Asia/Shanghai")


def _current_time_section(*, now: datetime | None = None) -> str:
    current = now or datetime.now(_AGENT_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_AGENT_TZ)
    else:
        current = current.astimezone(_AGENT_TZ)
    stamped = current.strftime("%Y-%m-%d %H:%M:%S")
    weekday = "一二三四五六日"[current.weekday()]
    return (
        "## 当前时间\n"
        f"- 时区：Asia/Shanghai（北京时间）\n"
        f"- 现在：{stamped}（星期{weekday}）\n"
        f"- ISO：{current.isoformat()}\n"
        "回答当前时刻/日期时以本节为准。"
    )


def _runtime_config_section(user_id: str) -> str:
    from ..llm_settings import public_llm_settings

    llm = public_llm_settings(user_id)
    model = str(llm.get("model") or "（未配置）")
    configured = "已配置" if llm.get("configured") else "未配置"
    hint = llm.get("key_hint")
    key_line = f"- API Key：{hint}\n" if hint else ""
    return (
        "## 运行配置\n"
        f"- DeepSeek：{configured}\n"
        f"- 主对话模型：{model}\n"
        f"{key_line}"
        "回答「当前模型」时以本节为准（此即本轮请求实际使用的模型名）。"
    )


def build_system_prompt(user_id: str) -> str:
    from ..agent_config import get_system_prompt
    from ..knowledge import build_knowledge_prompt_section

    parts = [
        SYSTEM_PROMPT.rstrip(),
        _current_time_section(),
        _runtime_config_section(user_id),
    ]
    user_prompt = (get_system_prompt(user_id) or "").strip()
    if user_prompt:
        parts.append(_USER_SYSTEM_PROMPT_HEADER.rstrip() + "\n" + user_prompt)
    catalog = (build_knowledge_prompt_section(user_id) or "").strip()
    if catalog:
        parts.append(catalog)
    return "\n\n".join(parts) + "\n"

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


def _tool_message_trace(m: ToolMessage) -> dict[str, str]:
    name = getattr(m, "name", None) or "tool"
    if name == _DELEGATE_DATA_TASK_NAME:
        return {"tool": name, "content": _DELEGATE_DATA_TASK_SAFE_CONTENT}
    return {"tool": name, "content": _extract_text(m.content)[:2000]}


def _iter_agent_chat_events_sync(
    user_id: str,
    message: str,
    *,
    session_id: str | None,
    progress_trace: list[dict[str, str]],
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
        from .web_limits import reset_web_turn_counters

        reset_web_turn_counters()
        model = build_chat_model(user_id)
        tools = build_tools(user_id)
        agent = create_react_agent(model, tools, prompt=build_system_prompt(user_id))

        from ..knowledge import build_always_knowledge_text

        lc_messages: list[Any] = []
        always_text = (build_always_knowledge_text(user_id) or "").strip()
        if always_text:
            lc_messages.append(SystemMessage(content=always_text))
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
                    trace = _tool_message_trace(m)
                    tool_trace.append(trace)
                    # 工具调用后下一轮模型输出才是最终回答
                    streamed_buf = ""
                    yield {
                        "event": "tool",
                        "data": {
                            "tool": trace["tool"],
                            "content": trace["content"][:1200],
                        },
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
                            trace = _tool_message_trace(m)
                            if not any(
                                t.get("tool") == trace["tool"]
                                and t.get("content") == trace["content"]
                                for t in tool_trace[-3:]
                            ):
                                tool_trace.append(trace)
                                yield {
                                    "event": "tool",
                                    "data": {
                                        "tool": trace["tool"],
                                        "content": trace["content"][:1200],
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
                    tool_trace.append(_tool_message_trace(m))

        reply = _finalize_reply(final_text or streamed_buf)
        if not streamed_any and reply:
            yield {"event": "token", "data": {"delta": reply}}

        persisted_trace = [*progress_trace, *tool_trace][-20:]
        done_trace = persisted_trace[-12:]
        if not session_exists(user_id, sid):
            yield {
                "event": "error",
                "data": {"detail": "session_not_found", "session_id": sid},
            }
            return
        append_message(
            user_id,
            sid,
            role="assistant",
            content=reply,
            tool_trace=persisted_trace,
        )
        yield {
            "event": "done",
            "data": {
                "session_id": sid,
                "reply": reply,
                "tool_trace": done_trace,
                "disclaimer": DISCLAIMER,
            },
        }
    except ProgressValidationError:
        yield {
            "event": "error",
            "data": {"detail": "progress_validation_error", "session_id": sid},
        }
    except Exception:
        yield {
            "event": "error",
            "data": {"detail": _AGENT_ERROR_DETAIL, "session_id": sid},
        }


def iter_agent_chat_events(
    user_id: str,
    message: str,
    *,
    session_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield SSE-ready events with async progress bridged from sub-agents."""
    output: queue.Queue[object] = queue.Queue(maxsize=_EVENT_QUEUE_SIZE)
    stopped = threading.Event()
    progress_lock = threading.Lock()
    progress_trace: list[dict[str, str]] = []

    def put_required(value: object) -> bool:
        while not stopped.is_set():
            try:
                output.put(value, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def progress_sink(event: dict[str, object]) -> None:
        if stopped.is_set():
            return
        try:
            trace = progress_to_tool_trace(event)
        except ProgressValidationError:
            return
        # Serialize stop with enqueue/append so disconnect cannot interleave mid-write.
        with progress_lock:
            if stopped.is_set():
                return
            if not progress_trace or progress_trace[-1] != trace:
                progress_trace.append(trace)
                del progress_trace[:-_PROGRESS_TRACE_LIMIT]
            try:
                output.put_nowait({"event": "subagent_progress", "data": event})
            except queue.Full:
                pass

    def produce() -> None:
        try:
            with bind_progress_sink(progress_sink):
                for event in _iter_agent_chat_events_sync(
                    user_id,
                    message,
                    session_id=session_id,
                    progress_trace=progress_trace,
                ):
                    if stopped.is_set() or not put_required(event):
                        break
        except ProgressValidationError:
            put_required(
                {
                    "event": "error",
                    "data": {
                        "detail": "progress_validation_error",
                        "session_id": session_id,
                    },
                }
            )
        except Exception:
            put_required(
                {
                    "event": "error",
                    "data": {
                        "detail": _AGENT_ERROR_DETAIL,
                        "session_id": session_id,
                    },
                }
            )
        finally:
            put_required(_STREAM_END)

    context = contextvars.copy_context()
    worker = threading.Thread(target=context.run, args=(produce,), daemon=True)
    worker.start()
    try:
        while True:
            event = output.get()
            if event is _STREAM_END:
                return
            yield event  # type: ignore[misc]
    finally:
        with progress_lock:
            stopped.set()


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
