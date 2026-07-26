"""DeepSeek Anthropic-compatible web_research (server tool web_search_20250305)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .web_limits import get_agent_web_config


def _post_messages(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/v1/messages"
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
                "tools": tools,
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("DeepSeek 响应格式无效")
        return data


def _collect_urls(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        url = node.get("url")
        if isinstance(url, str) and url.startswith("http") and url not in out:
            out.append(url)
        for value in node.values():
            _collect_urls(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_urls(item, out)


def _extract_answer_and_sources(payload: dict[str, Any]) -> tuple[str, list[str]]:
    content = payload.get("content")
    answer_parts: list[str] = []
    sources: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    answer_parts.append(text)
            _collect_urls(block, sources)
    elif isinstance(content, str):
        answer_parts.append(content)
    return "\n".join(answer_parts).strip(), sources


def run_web_research(
    api_key: str, query: str, *, cfg: dict[str, Any] | None = None
) -> str:
    section = (
        cfg if cfg is not None else get_agent_web_config().get("web_research") or {}
    )
    max_chars = int(section.get("max_query_chars") or 500)
    q = (query or "").strip()
    if not q:
        return "错误：查询不能为空"
    if len(q) > max_chars:
        q = q[:max_chars]
    model = str(section.get("model") or "deepseek-v4-flash")
    base_url = str(
        section.get("anthropic_base_url") or "https://api.deepseek.com/anthropic"
    )
    tool_type = str(section.get("server_tool_type") or "web_search_20250305")
    max_tokens = int(section.get("max_tokens") or 8192)
    timeout = float(section.get("timeout_seconds") or 120)
    try:
        payload = _post_messages(
            api_key=api_key.strip(),
            base_url=base_url,
            model=model,
            messages=[{"role": "user", "content": q}],
            tools=[{"type": tool_type, "name": "web_search"}],
            max_tokens=max_tokens,
            timeout=timeout,
        )
        answer, sources = _extract_answer_and_sources(payload)
        if not answer:
            return "错误：未返回研究内容"
        return json.dumps(
            {"answer": answer, "sources": sources},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return f"错误：DeepSeek web_research 失败: {type(exc).__name__}"
