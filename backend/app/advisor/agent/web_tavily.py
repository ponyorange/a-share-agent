"""Tavily Search API helpers for agent web_search."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .web_limits import get_agent_web_config

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _request_search(
    api_key: str, payload: dict[str, Any], *, timeout: float
) -> dict[str, Any]:
    body = dict(payload)
    body["api_key"] = api_key
    with httpx.Client(timeout=timeout) as client:
        response = client.post(TAVILY_SEARCH_URL, json=body)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Tavily 响应格式无效")
        return data


def validate_tavily_key(api_key: str, *, cfg: dict[str, Any] | None = None) -> None:
    section = cfg if cfg is not None else get_agent_web_config().get("web_search") or {}
    query = str(section.get("validate_query") or "ping")
    timeout = 20.0
    try:
        _request_search(
            api_key.strip(),
            {"query": query, "max_results": 1, "search_depth": "basic"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Tavily API Key 无效或不可用") from exc


def tavily_search(
    api_key: str,
    query: str,
    *,
    max_results: int = 5,
    cfg: dict[str, Any] | None = None,
) -> str:
    section = cfg if cfg is not None else get_agent_web_config().get("web_search") or {}
    cap = int(section.get("max_results_cap") or 10)
    default = int(section.get("max_results_default") or 5)
    n = int(max_results) if max_results is not None else default
    n = max(1, min(n, cap))
    q = (query or "").strip()
    if not q:
        return "错误：查询不能为空"
    try:
        data = _request_search(
            api_key.strip(),
            {"query": q, "max_results": n, "search_depth": "basic"},
            timeout=30.0,
        )
        results = data.get("results") or []
        rows: list[dict[str, Any]] = []
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                row: dict[str, Any] = {
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "content": str(item.get("content") or item.get("snippet") or ""),
                }
                if "score" in item:
                    row["score"] = item.get("score")
                rows.append(row)
        return json.dumps(rows, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return f"错误：Tavily 搜索失败: {type(exc).__name__}"
