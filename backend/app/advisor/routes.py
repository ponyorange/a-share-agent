"""Advisor HTTP routes."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..auth import get_current_user
from . import context
from .backtest import iter_backtest_summary_events, run_backtest_summary
from .calendar_util import last_trading_day
from .leaderboard import iter_leaderboard_events, load_leaderboard
from .paper import (
    PaperOrderBody,
    PaperResetBody,
    ONE_CLICK_BOARDS,
    _json_safe,
    delete_position,
    get_account,
    iter_mark_to_market_events,
    iter_one_click_buy_events,
    list_trades,
    one_click_buy_from_recs,
    paper_pnl_summary,
    place_order,
    rec_one_click_performance,
    reset_account,
    sell_all_positions,
)
from .portfolio import PortfolioPayload, load_portfolio, save_portfolio
from .service import get_advice, get_portfolio_advice, get_recommendations
from .snapshots import (
    accuracy_summary,
    effective_rec_date,
    enrich_snapshot_returns,
    has_snapshot,
    iter_rec_quote_events,
    iter_snapshot_return_events,
    list_snapshot_dates,
    load_history_plain,
    save_snapshot,
    snapshot_as_recommendations,
)
from .universe import describe_universe
from .user_strategy import (
    STRATEGY_EDITABLE_KEYS,
    get_user_strategy,
    reset_user_strategy,
    strategy_public_view,
    update_user_strategy,
)
from .llm_settings import (
    clear_llm_settings,
    clear_tavily_settings,
    public_llm_settings,
    update_llm_settings,
)
from .ui_settings import get_ui_settings, save_ui_settings
from .agent.graph import iter_agent_chat_events, run_agent_chat
from .agent.chat_store import (
    delete_session,
    ensure_session,
    get_messages,
    list_sessions,
)
from .agent.recommendations import iter_agent_recommendation_events
from .agent_recs import (
    agent_snapshot_as_recommendations,
    has_agent_snapshot,
    list_agent_snapshot_dates,
)

router = APIRouter(prefix="/api/advisor", tags=["advisor"])


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(_json_safe(data), ensure_ascii=False)}\n\n"


def _user(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return user


def _bind(user: dict[str, Any]) -> str:
    """在端点线程内绑定用户策略。Depends 跑在别的线程池，ContextVar 传不过来。"""
    uid = str(user["id"])
    context.bind_user(uid)
    return uid


@router.get("/universe")
def advisor_universe() -> dict[str, Any]:
    return describe_universe()


class StrategyUpdateBody(BaseModel):
    config: dict[str, Any] | None = None
    config_patch: dict[str, Any] | None = None
    notes: str | None = None
    source: str = Field(default="manual")


@router.get("/strategy")
def strategy_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    """当前用户策略（缺省则创建为系统默认）。"""
    uid = _bind(user)
    return strategy_public_view(get_user_strategy(uid))


@router.put("/strategy")
def strategy_put(
    body: StrategyUpdateBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    """更新用户策略。改完后需「刷新候选池」才会按新策略重算今日关注。"""
    uid = _bind(user)
    try:
        doc = update_user_strategy(
            uid,
            config=body.config,
            config_patch=body.config_patch,
            source=body.source,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    context.bind_user(uid)
    return strategy_public_view(doc)


@router.post("/strategy/reset")
def strategy_reset(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    uid = _bind(user)
    doc = reset_user_strategy(uid)
    context.bind_user(uid)
    return strategy_public_view(doc)


class LlmSettingsBody(BaseModel):
    api_key: str | None = Field(default=None)
    model: str | None = Field(default=None)
    base_url: str | None = Field(default=None)
    web_research_enabled: bool | None = Field(default=None)
    tavily_enabled: bool | None = Field(default=None)
    tavily_api_key: str | None = Field(default=None)


class UiColorsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_bg: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    surface: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_primary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_muted: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    border: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    brand: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    market_up: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    market_down: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    success: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    error: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class UiSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_template: Literal["modern_data", "classic_market", "deep_navy"]
    colors: UiColorsBody


@router.get("/ui/settings")
def ui_settings_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    return get_ui_settings(_bind(user))


@router.put("/ui/settings")
def ui_settings_put(
    body: UiSettingsBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    uid = _bind(user)
    try:
        return save_ui_settings(
            uid,
            active_template=body.active_template,
            colors=body.colors.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/llm/settings")
def llm_settings_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    _bind(user)
    return public_llm_settings(user["id"])


@router.put("/llm/settings")
def llm_settings_put(
    body: LlmSettingsBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    uid = _bind(user)
    try:
        return update_llm_settings(
            uid,
            api_key=body.api_key,
            model=body.model,
            base_url=body.base_url,
            web_research_enabled=body.web_research_enabled,
            tavily_enabled=body.tavily_enabled,
            tavily_api_key=body.tavily_api_key,
            validate_deepseek=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek 校验失败: {type(exc).__name__}",
        ) from exc


@router.delete("/llm/settings")
def llm_settings_delete(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    uid = _bind(user)
    return clear_llm_settings(uid)


@router.delete("/llm/settings/tavily")
def llm_settings_tavily_delete(
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    uid = _bind(user)
    return clear_tavily_settings(uid)


class KnowledgeBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)
    mode: str = Field(..., pattern="^(always|on_demand)$")
    enabled: bool = True
    description: str = ""
    body: str = Field(..., min_length=1, max_length=8000)


class AgentSystemPromptBody(BaseModel):
    system_prompt: str = ""


@router.get("/agent-config/system-prompt")
def agent_system_prompt_get(
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .agent_config import public_system_prompt

    uid = _bind(user)
    return public_system_prompt(uid)


@router.put("/agent-config/system-prompt")
def agent_system_prompt_put(
    body: AgentSystemPromptBody,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .agent_config import save_system_prompt

    uid = _bind(user)
    try:
        return save_system_prompt(uid, body.system_prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge")
def knowledge_list(
    summary: bool = Query(default=False),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .knowledge import list_items

    uid = _bind(user)
    return {"items": list_items(uid, summary=summary)}


@router.post("/knowledge")
def knowledge_create(
    body: KnowledgeBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    from .knowledge import create_item

    uid = _bind(user)
    try:
        return create_item(uid, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge/{item_id}")
def knowledge_get(
    item_id: str, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    from .knowledge import get_item

    uid = _bind(user)
    item = get_item(uid, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return item


@router.put("/knowledge/{item_id}")
def knowledge_put(
    item_id: str,
    body: KnowledgeBody,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .knowledge import update_item

    uid = _bind(user)
    try:
        return update_item(uid, item_id, body.model_dump())
    except KeyError:
        raise HTTPException(status_code=404, detail="知识条目不存在") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/knowledge/{item_id}")
def knowledge_delete(
    item_id: str, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    from .knowledge import delete_item

    uid = _bind(user)
    if not delete_item(uid, item_id):
        raise HTTPException(status_code=404, detail="知识条目不存在")
    return {"ok": True}


class AgentChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    session_id: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)


class AgentStrategyApplyBody(BaseModel):
    config_patch: dict[str, Any]
    confirm: bool = True
    notes: str | None = None


@router.get("/agent/sessions")
def agent_sessions_list(
    limit: int = Query(default=20, ge=1, le=50),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    uid = _bind(user)
    return {"sessions": list_sessions(uid, limit=limit)}


@router.get("/agent/sessions/{session_id}/messages")
def agent_session_messages(
    session_id: str,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    uid = _bind(user)
    return {
        "session_id": session_id,
        "messages": get_messages(uid, session_id),
    }


@router.post("/agent/sessions")
def agent_session_create(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    uid = _bind(user)
    sid = ensure_session(uid, None)
    return {"session_id": sid}


@router.delete("/agent/sessions/{session_id}")
def agent_session_delete(
    session_id: str, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    uid = _bind(user)
    delete_session(uid, session_id)
    return {"ok": True, "session_id": session_id}


@router.post("/agent/chat")
def agent_chat(
    body: AgentChatBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    """投研助手（非流式）。需已配置 API Key。"""
    uid = _bind(user)
    settings = public_llm_settings(uid)
    if not settings.get("configured"):
        raise HTTPException(status_code=403, detail="请先配置 DeepSeek API Key")
    try:
        return run_agent_chat(
            uid, body.message, session_id=body.session_id, history=body.history
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Agent 调用失败: {type(exc).__name__}"
        ) from exc


@router.post("/agent/chat/stream")
def agent_chat_stream(
    body: AgentChatBody,
    user: dict[str, Any] = Depends(_user),
):
    """SSE：meta → tool* → token* → done。聊天写入 Mongo，带滑动窗口上下文。

    使用 POST body 传 message，避免长中文塞进 query 触发 URL 长度限制。
    """
    uid = user["id"]
    message = body.message
    session_id = body.session_id

    def gen():
        try:
            context.bind_user(uid)
            settings = public_llm_settings(uid)
            if not settings.get("configured"):
                yield _sse("error", {"detail": "请先配置 DeepSeek API Key"})
                return
            for ev in iter_agent_chat_events(
                uid, message, session_id=session_id
            ):
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/agent/strategy/apply")
def agent_strategy_apply(
    body: AgentStrategyApplyBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    """策略副驾：确认后写入用户策略（source=agent）。"""
    uid = _bind(user)
    settings = public_llm_settings(uid)
    if not settings.get("configured"):
        raise HTTPException(status_code=403, detail="请先配置 DeepSeek API Key")
    if not body.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true")
    allowed = set(STRATEGY_EDITABLE_KEYS)
    clean = {k: v for k, v in (body.config_patch or {}).items() if k in allowed}
    if not clean:
        raise HTTPException(
            status_code=400,
            detail=f"无允许字段，允许: {sorted(allowed)}",
        )
    try:
        doc = update_user_strategy(
            uid,
            config_patch=clean,
            source="agent",
            notes=body.notes or "Agent 策略副驾确认写入",
        )
        return {
            **strategy_public_view(doc),
            "hint": "已写入。请到基础面板「今日关注」刷新候选池使新策略生效。",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/agent/recommendations")
def agent_recommendations(
    top: int = Query(default=10, ge=1, le=30),
    board: str | None = Query(default="all"),
    as_of: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    """读取 Agent 今日关注归档（独立表 agent_rec_snapshots）。无缓存则提示刷新。"""
    uid = _bind(user)
    settings = public_llm_settings(uid)
    if not settings.get("configured"):
        raise HTTPException(status_code=403, detail="请先配置 DeepSeek API Key")
    trade_date = effective_rec_date(as_of)
    board_key = None if board in (None, "", "all") else board
    if has_agent_snapshot(trade_date, user_id=uid):
        cached = agent_snapshot_as_recommendations(
            trade_date, board=board_key, top=top, user_id=uid
        )
        if cached:
            return cached
    return {
        "trade_date": trade_date,
        "kind": "agent",
        "boards": {},
        "message": "暂无 Agent 今日关注归档，请点击「生成/刷新」",
        "from_cache": False,
    }


@router.get("/agent/recommendations/dates")
def agent_recommendation_dates(
    limit: int = Query(default=60, ge=1, le=365),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    uid = _bind(user)
    return {"dates": list_agent_snapshot_dates(limit, user_id=uid)}


@router.get("/agent/recommendations/stream")
def agent_recommendations_stream(
    top: int = Query(default=10, ge=1, le=30),
    force: bool = Query(default=True),
    as_of: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
):
    """SSE：按策略评分 + 新闻/宏观，Agent 总结后写入独立归档。"""
    uid = user["id"]

    def gen():
        try:
            context.bind_user(uid)
            settings = public_llm_settings(uid)
            if not settings.get("configured"):
                yield _sse("error", {"detail": "请先配置 DeepSeek API Key"})
                return
            for ev in iter_agent_recommendation_events(
                uid, top=top, force=force, as_of=as_of
            ):
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/recommendations")
def recommendations(
    top: int = Query(default=15, ge=1, le=50),
    board: str | None = Query(
        default="all",
        description="etf | hs | star | all",
    ),
    as_of: str | None = Query(default=None),
    refresh_universe: bool = Query(
        default=False,
        description="手动刷新候选池：重算并覆盖有效交易日归档",
    ),
    persist: bool = Query(default=True, description="是否写入/覆盖有效交易日快照"),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    uid = _bind(user)
    trade_date = effective_rec_date(as_of)
    board_key = None if board in (None, "", "all") else board

    if not refresh_universe and has_snapshot(trade_date, user_id=uid):
        cached = snapshot_as_recommendations(
            trade_date, board=board_key, top=top, user_id=uid
        )
        if cached:
            return cached

    try:
        result = get_recommendations(
            top=top,
            as_of=as_of or trade_date,
            board=board_key,
            force_universe=refresh_universe,
            user_id=uid,
        )
        if persist:
            snap = save_snapshot(result, trade_date=trade_date, user_id=uid)
            result["snapshot"] = snap
            result["trade_date"] = trade_date
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"推荐生成失败: {type(exc).__name__}"
        ) from exc


@router.get("/recommendations/refresh/stream")
def recommendations_refresh_stream(
    top: int = Query(default=10, ge=1, le=50),
    board: str | None = Query(
        default="all",
        description="etf | hs | star | all",
    ),
    as_of: str | None = Query(default=None),
    persist: bool = Query(default=True),
    user: dict[str, Any] = Depends(_user),
) -> StreamingResponse:
    """兼容旧前端：内部改为后台任务 + 订阅进度（断线后任务仍继续）。"""
    from .rec_refresh_jobs import iter_job_sse_events, start_refresh_job

    uid = _bind(user)
    trade_date = effective_rec_date(as_of)
    board_key = board if board not in (None, "") else "all"
    job = start_refresh_job(
        uid,
        trade_date=trade_date,
        top=top,
        board=board_key or "all",
        as_of=as_of or trade_date,
        persist=persist,
    )
    job_id = str(job["job_id"])

    def gen():
        try:
            yield _sse(
                "meta",
                {
                    "job_id": job_id,
                    "trade_date": trade_date,
                    "status": job.get("status"),
                    "phase": "queued",
                },
            )
            for ev in iter_job_sse_events(uid, job_id):
                # skip duplicate meta from poller
                if ev.get("event") == "meta":
                    continue
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse(
                "error",
                {"detail": f"推荐生成失败: {type(exc).__name__}: {exc}"},
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/recommendations/refresh")
def recommendations_refresh_start(
    top: int = Query(default=10, ge=1, le=50),
    board: str | None = Query(default="all"),
    as_of: str | None = Query(default=None),
    persist: bool = Query(default=True),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    """启动（或复用）后台刷新任务；关页面后任务仍继续。"""
    from .rec_refresh_jobs import start_refresh_job

    uid = _bind(user)
    trade_date = effective_rec_date(as_of)
    job = start_refresh_job(
        uid,
        trade_date=trade_date,
        top=top,
        board=board if board not in (None, "") else "all",
        as_of=as_of or trade_date,
        persist=persist,
    )
    return {"job": job}


@router.get("/recommendations/refresh/active")
def recommendations_refresh_active(
    as_of: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    """查询当前用户进行中的刷新任务（用于回页续订）。"""
    from .rec_refresh_jobs import find_active_job

    uid = _bind(user)
    trade_date = effective_rec_date(as_of)
    return {"job": find_active_job(uid, trade_date)}


@router.get("/recommendations/refresh/{job_id}")
def recommendations_refresh_get(
    job_id: str,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    from .rec_refresh_jobs import get_job

    uid = _bind(user)
    job = get_job(uid, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="刷新任务不存在")
    return {"job": job}


@router.get("/recommendations/refresh/{job_id}/stream")
def recommendations_refresh_job_stream(
    job_id: str,
    user: dict[str, Any] = Depends(_user),
) -> StreamingResponse:
    """订阅已有后台刷新任务的进度（可重连）。"""
    from .rec_refresh_jobs import get_job, iter_job_sse_events

    uid = _bind(user)
    job = get_job(uid, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="刷新任务不存在")

    def gen():
        try:
            yield _sse(
                "meta",
                {
                    "job_id": job_id,
                    "trade_date": job.get("trade_date"),
                    "status": job.get("status"),
                    "phase": (job.get("progress") or {}).get("phase"),
                },
            )
            if job.get("status") == "completed":
                yield _sse(
                    "done",
                    {
                        "job_id": job_id,
                        "status": "completed",
                        "trade_date": job.get("trade_date"),
                        "progress": job.get("progress") or {},
                    },
                )
                return
            if job.get("status") == "failed":
                yield _sse(
                    "error",
                    {
                        "job_id": job_id,
                        "detail": job.get("error") or "刷新失败",
                        "status": "failed",
                    },
                )
                return
            for ev in iter_job_sse_events(uid, job_id):
                if ev.get("event") == "meta":
                    continue
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse(
                "error",
                {"detail": f"{type(exc).__name__}: {exc}"},
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/recommendations/dates")
def recommendation_dates(
    limit: int = Query(default=60, ge=1, le=365),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    uid = _bind(user)
    return {"dates": list_snapshot_dates(limit, user_id=uid)}


@router.get("/recommendations/quotes/stream")
def recommendation_quotes_stream(
    trade_date: str | None = Query(default=None),
    board: str = Query(default="all"),
    user: dict[str, Any] = Depends(_user),
):
    """SSE：为今日关注逐只加载收盘价与当日/最近交易日涨幅。"""
    uid = user["id"]

    def gen():
        try:
            context.bind_user(uid)
            for ev in iter_rec_quote_events(trade_date, board, user_id=uid):
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/recommendations/history")
def recommendation_history(
    trade_date: str = Query(..., description="归档交易日 YYYY-MM-DD"),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    """只返回归档名单，不计算涨跌幅。"""
    uid = _bind(user)
    try:
        return load_history_plain(trade_date, user_id=uid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/recommendations/history/stream")
def recommendation_history_stream(
    trade_date: str = Query(..., description="归档交易日 YYYY-MM-DD"),
    vs_date: str | None = Query(default=None, description="对比日，默认最近交易日"),
    user: dict[str, Any] = Depends(_user),
):
    """SSE：逐只计算相对涨跌幅，算完一只推送一只。"""
    uid = user["id"]

    def gen():
        try:
            context.bind_user(uid)
            for ev in iter_snapshot_return_events(
                trade_date, vs_date, user_id=uid
            ):
                yield _sse(ev["event"], ev["data"])
        except ValueError as exc:
            yield _sse("error", {"detail": str(exc)})
        except Exception as exc:
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/recommendations/history/returns")
def recommendation_history_returns(
    trade_date: str = Query(...),
    vs_date: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    """一次性算完全部涨跌幅（非流式备用）。"""
    uid = _bind(user)
    try:
        return enrich_snapshot_returns(trade_date, vs_date, user_id=uid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"历史涨跌幅失败: {type(exc).__name__}"
        ) from exc


@router.get("/recommendations/accuracy")
def recommendation_accuracy(
    limit_days: int = Query(default=30, ge=1, le=120),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    uid = _bind(user)
    return accuracy_summary(limit_days, user_id=uid)


@router.get("/advice")
def advice(
    symbol: str = Query(...),
    as_of: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    _bind(user)
    try:
        return get_advice(symbol=symbol, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"诊断失败: {type(exc).__name__}"
        ) from exc


@router.get("/portfolio")
def portfolio_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    _bind(user)
    return load_portfolio(user["id"])


@router.get("/portfolio/marks")
def portfolio_marks_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    """真实持仓市值/盈亏快照（含交易时段信息，供前端轮询）。"""
    from .portfolio import portfolio_marks

    _bind(user)
    try:
        return portfolio_marks(user["id"])
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"持仓行情失败: {type(exc).__name__}"
        ) from exc


@router.post("/portfolio")
def portfolio_set(
    body: PortfolioPayload, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    _bind(user)
    return save_portfolio(body, user["id"])


@router.get("/portfolio/advice")
def portfolio_advice(
    as_of: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    uid = _bind(user)
    try:
        return get_portfolio_advice(as_of=as_of, user_id=uid)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"持仓诊断失败: {type(exc).__name__}"
        ) from exc


@router.get("/paper")
def paper_get(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    """默认读库缓存价，不实时拉行情。"""
    try:
        return get_account(user["id"], mark_to_market=False)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/paper/mark-to-market/stream")
def paper_mark_to_market_stream(user: dict[str, Any] = Depends(_user)):
    """SSE：逐只刷新现价/市值/浮盈亏并写回数据库。"""

    def gen():
        try:
            for ev in iter_mark_to_market_events(user["id"]):
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/paper/reset")
def paper_reset(
    body: PaperResetBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    return reset_account(user["id"], body.cash)


@router.post("/paper/order")
def paper_order(
    body: PaperOrderBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    try:
        return place_order(user["id"], body, source="manual")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class PaperSellBody(BaseModel):
    qty: float | None = Field(default=None, gt=0, description="缺省为全部持仓")
    price: float | None = Field(default=None, gt=0)


@router.delete("/paper/positions/{symbol}")
def paper_delete_position(
    symbol: str,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    """删除持仓：当作从未买过，作废相关成交且不计入收益。"""
    try:
        return delete_position(user["id"], symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/paper/positions/{symbol}/sell")
def paper_sell_position(
    symbol: str,
    body: PaperSellBody | None = None,
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    """卖出持仓：qty 缺省为全部，计入已实现收益。"""
    body = body or PaperSellBody()
    try:
        acc = get_account(user["id"], mark_to_market=False)
        pos = next((p for p in acc["positions"] if p["symbol"] == symbol), None)
        if not pos:
            raise ValueError(f"无持仓: {symbol}")
        qty = float(body.qty) if body.qty is not None else float(pos["qty"])
        return place_order(
            user["id"],
            PaperOrderBody(
                symbol=symbol,
                side="sell",
                qty=qty,
                price=body.price,
                name=pos.get("name"),
            ),
            source="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/paper/sell-all")
def paper_sell_all(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    """一键卖出全部持仓，计入已实现（历史）收益。"""
    try:
        return sell_all_positions(user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/paper/trades")
def paper_trades(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    source: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    result = list_trades(
        user["id"], source=source, page=page, page_size=page_size
    )
    assert isinstance(result, dict)
    return result

class OneClickBody(BaseModel):
    top: int = Field(default=10, ge=1, le=50)
    board: str = Field(default="all")
    mode: str = Field(default="balanced", description="balanced=按评分分配；full=尽量满仓")
    max_count: int | None = Field(
        default=None, ge=1, le=200, description="最多买入标的数；空=不限制"
    )


def _one_click_boards(board: str | None) -> tuple[str, ...]:
    """一键买入：仅 etf / 沪深；all 或其它 → etf+hs，永不含科创。"""
    if board in ("etf", "hs"):
        return (board,)
    return ONE_CLICK_BOARDS


def _one_click_mode(mode: str | None) -> str:
    return "full" if mode == "full" else "balanced"


@router.post("/paper/one-click-buy")
def paper_one_click(
    body: OneClickBody, user: dict[str, Any] = Depends(_user)
) -> dict[str, Any]:
    uid = _bind(user)
    try:
        trade_date = effective_rec_date()
        boards = _one_click_boards(body.board)
        mode = _one_click_mode(body.mode)
        recs = None
        if has_snapshot(trade_date, user_id=uid):
            # 用完整归档，不要按 top 截断，否则漏买
            recs = snapshot_as_recommendations(trade_date, board=None, user_id=uid)
        if not recs:
            recs = get_recommendations(
                top=body.top,
                board=None,
                force_universe=False,
                user_id=uid,
            )
            save_snapshot(recs, trade_date=trade_date, user_id=uid)
        return one_click_buy_from_recs(
            uid,
            recs,
            trade_date=trade_date or recs.get("as_of") or last_trading_day(),
            boards=boards,
            mode=mode,
            max_count=body.max_count,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"一键买入失败: {type(exc).__name__}"
        ) from exc


@router.get("/paper/one-click-buy/stream")
def paper_one_click_stream(
    top: int = Query(default=10, ge=1, le=50),
    board: str = Query(default="all"),
    mode: str = Query(default="balanced", description="balanced | full"),
    max_count: int | None = Query(
        default=None, ge=1, le=200, description="最多买入标的数；空=不限制"
    ),
    user: dict[str, Any] = Depends(_user),
):
    """SSE：一键买入进度（meta → trade/skip* → done）。仅 ETF/沪深。"""
    uid = user["id"]

    def gen():
        try:
            context.bind_user(uid)
            trade_date = effective_rec_date()
            boards = _one_click_boards(board)
            buy_mode = _one_click_mode(mode)
            recs = None
            if has_snapshot(trade_date, user_id=uid):
                recs = snapshot_as_recommendations(
                    trade_date, board=None, user_id=uid
                )
            if not recs:
                recs = get_recommendations(
                    top=top,
                    board=None,
                    force_universe=False,
                    user_id=uid,
                )
                save_snapshot(recs, trade_date=trade_date, user_id=uid)
            for ev in iter_one_click_buy_events(
                uid,
                recs,
                trade_date=trade_date or recs.get("as_of") or last_trading_day(),
                boards=boards,
                mode=buy_mode,
                max_count=max_count,
            ):
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/paper/pnl")
def paper_pnl(user: dict[str, Any] = Depends(_user)) -> dict[str, Any]:
    """一键买入 / 手动 / 总收益汇总。"""
    return paper_pnl_summary(user["id"])


@router.get("/paper/one-click-perf")
def paper_one_click_perf(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    return rec_one_click_performance(user["id"], page=page, page_size=page_size)


@router.get("/backtest/summary")
def backtest_summary(
    force: bool = Query(default=False),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    _bind(user)
    try:
        return run_backtest_summary(force=force)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"回测失败: {type(exc).__name__}"
        ) from exc


@router.get("/backtest/summary/stream")
def backtest_summary_stream(
    force: bool = Query(default=False),
    user: dict[str, Any] = Depends(_user),
):
    """SSE：meta → progress* → done。命中缓存时直接 done。"""
    uid = user["id"]

    def gen():
        try:
            context.bind_user(uid)
            for ev in iter_backtest_summary_events(force=force):
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/leaderboard")
def leaderboard_get(
    trade_date: str | None = Query(default=None, description="交易日，默认最近交易日"),
    user: dict[str, Any] = Depends(_user),
) -> dict[str, Any]:
    """读取当日龙虎榜缓存（不拉行情）。"""
    del user
    day = trade_date or last_trading_day()
    doc = load_leaderboard(day)
    if not doc:
        return {
            "trade_date": day,
            "boards": None,
            "from_cache": False,
            "message": "暂无缓存，请点击「拉取榜单」",
        }
    return {**doc, "from_cache": True}


@router.get("/leaderboard/stream")
def leaderboard_stream(
    force: bool = Query(default=True, description="是否强制重拉并覆盖当日缓存"),
    trade_date: str | None = Query(default=None),
    user: dict[str, Any] = Depends(_user),
):
    """SSE：meta → progress* → done。拉取涨跌幅/资金流榜并按日缓存。"""
    del user

    def gen():
        try:
            for ev in iter_leaderboard_events(force=force, trade_date=trade_date):
                yield _sse(ev["event"], ev["data"])
        except Exception as exc:
            yield _sse("error", {"detail": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
