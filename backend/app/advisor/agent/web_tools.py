"""Conditionally mount web_research / web_search / fetch_url tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from ..llm_settings import (
    resolve_deepseek_api_key,
    resolve_tavily_api_key,
    web_tool_flags,
)
from .progress import emit_progress
from .web_fetch_escalation import fetch_url_with_escalation
from .web_limits import consume_web_quota
from .web_research import run_web_research
from .web_tavily import tavily_search


def build_web_tools(user_id: str) -> list[BaseTool]:
    flags = web_tool_flags(user_id)
    tools: list[BaseTool] = []

    if flags.get("web_research"):

        @tool
        def web_research(query: str) -> str:
            """使用 DeepSeek 服务端联网搜索做综合调研，返回带引用的回答。
            适合政策/新闻等需要综述的问题；引用须使用返回的 sources。"""
            quota = consume_web_quota("web_research")
            if quota:
                return quota
            emit_progress(
                step="web_research", status="started", phase="main_agent"
            )
            try:
                key = resolve_deepseek_api_key(user_id)
                if not key:
                    emit_progress(
                        step="web_research",
                        status="failed",
                        phase="main_agent",
                        error_code="web_research_failed",
                    )
                    return "错误：未配置 DeepSeek API Key"
                out = run_web_research(key, query)
                failed = out.startswith("错误：")
                emit_progress(
                    step="web_research",
                    status="failed" if failed else "completed",
                    phase="main_agent",
                    error_code="web_research_failed" if failed else None,
                )
                return out
            except Exception as exc:  # noqa: BLE001
                emit_progress(
                    step="web_research",
                    status="failed",
                    phase="main_agent",
                    error_code="web_research_failed",
                )
                return f"错误：web_research 失败: {type(exc).__name__}"

        tools.append(web_research)

    if flags.get("tavily"):

        @tool
        def web_search(query: str, max_results: int = 5) -> str:
            """使用 Tavily 搜索网页，返回 title/url/content 列表。
            需要精读某页时再调用 fetch_url。"""
            quota = consume_web_quota("web_search")
            if quota:
                return quota
            emit_progress(step="web_search", status="started", phase="main_agent")
            try:
                key = resolve_tavily_api_key(user_id)
                if not key:
                    emit_progress(
                        step="web_search",
                        status="failed",
                        phase="main_agent",
                        error_code="tavily_key_missing",
                    )
                    return "错误：未配置 Tavily API Key"
                out = tavily_search(key, query, max_results=max_results)
                failed = out.startswith("错误：")
                emit_progress(
                    step="web_search",
                    status="failed" if failed else "completed",
                    phase="main_agent",
                    error_code="web_search_failed" if failed else None,
                )
                return out
            except Exception as exc:  # noqa: BLE001
                emit_progress(
                    step="web_search",
                    status="failed",
                    phase="main_agent",
                    error_code="web_search_failed",
                )
                return f"错误：web_search 失败: {type(exc).__name__}"

        tools.append(web_search)

    if flags.get("web_research") or flags.get("tavily"):

        @tool
        def fetch_url(url: str) -> str:
            """抓取公网网页正文（http/https）。禁止内网/本机地址。
            用户给出链接、或 web_research/web_search 得到候选 URL 后可调用。
            困难页面会自动增强抓取。"""
            quota = consume_web_quota("fetch_url")
            if quota:
                return quota
            emit_progress(step="fetch_url", status="started", phase="main_agent")
            try:

                def on_level(via: str) -> None:
                    if via == "scrapling":
                        emit_progress(
                            step="fetch_url_l2",
                            status="started",
                            phase="main_agent",
                        )
                    elif via == "stealth":
                        emit_progress(
                            step="fetch_url_l3",
                            status="started",
                            phase="main_agent",
                        )

                out = fetch_url_with_escalation(url, on_level=on_level)
                failed = out.startswith("错误：")
                emit_progress(
                    step="fetch_url",
                    status="failed" if failed else "completed",
                    phase="main_agent",
                    error_code="fetch_url_failed" if failed else None,
                )
                return out
            except Exception as exc:  # noqa: BLE001
                emit_progress(
                    step="fetch_url",
                    status="failed",
                    phase="main_agent",
                    error_code="fetch_url_failed",
                )
                return f"错误：fetch_url 失败: {type(exc).__name__}"

        tools.append(fetch_url)

    return tools


def tool_names(tools: list[Any]) -> set[str]:
    return {getattr(t, "name", "") for t in tools}
