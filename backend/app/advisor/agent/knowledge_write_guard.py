"""Detect hallucinated knowledge persist claims vs tool_trace evidence."""

from __future__ import annotations

import json
import re
from typing import Any

KNOWLEDGE_WRITE_CORRECTION = (
    "⚠️ 系统校验：本轮工具轨迹中未发现 `save_knowledge` / `delete_knowledge` "
    "返回 `ok: true` 的落库结果。上文若声称「已写入/已删除」可能不准确——"
    "请用 `list_knowledge` 核对，或再次明确确认后让我调用 "
    "`save_knowledge(confirm=true)` / `delete_knowledge(confirm=true)`。"
)

# Positive claims that imply DB mutation already happened.
_CLAIM_RE = re.compile(
    r"("
    r"已写入成功|写入成功|已正式写入|已写入完毕|写入完毕|"
    r"补写入库成功|入库成功|已成功存入|已存入知识库|已写入知识库|"
    r"已删除成功|删除成功|已从知识库删除|"
    r"全部\s*\d+\s*章节已写入|全部搞定"
    r")"
)

def claims_knowledge_persisted(text: str) -> bool:
    return bool(_CLAIM_RE.search(text or ""))


def _parse_tool_payload(content: str) -> dict[str, Any] | None:
    raw = (content or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def successful_knowledge_mutations(
    tool_trace: list[dict[str, Any]] | None,
) -> dict[str, int]:
    save_n = 0
    delete_n = 0
    for row in tool_trace or []:
        name = str(row.get("tool") or "")
        if name not in ("save_knowledge", "delete_knowledge"):
            continue
        data = _parse_tool_payload(str(row.get("content") or ""))
        if not data or data.get("ok") is not True:
            continue
        if name == "save_knowledge":
            save_n += 1
        else:
            delete_n += 1
    return {"save": save_n, "delete": delete_n}


def apply_knowledge_write_guard(
    text: str,
    *,
    tool_trace: list[dict[str, Any]] | None,
) -> str:
    body = text or ""
    if not body.strip():
        return body
    if not claims_knowledge_persisted(body):
        return body
    mut = successful_knowledge_mutations(tool_trace)
    if mut["save"] > 0 or mut["delete"] > 0:
        return body
    if KNOWLEDGE_WRITE_CORRECTION in body:
        return body
    return f"{body.rstrip()}\n\n{KNOWLEDGE_WRITE_CORRECTION}"
