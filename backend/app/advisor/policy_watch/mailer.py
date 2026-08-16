"""Assemble policy-watch alert emails."""

from __future__ import annotations

from typing import Any

from .sensitivity import direction_label
from .urls import article_open_url

DISCLAIMER = "研究参考，不构成投资建议。"


def _short_title(title: str) -> str:
    text = " ".join((title or "").split())
    return text[:40] + ("…" if len(text) > 40 else "")


def _format_sectors(sectors: list[Any]) -> str:
    names = []
    for item in sectors or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return "、".join(names) if names else "—"


def _format_symbols(symbols: list[Any]) -> str:
    parts = []
    for item in symbols or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip()
        label = " ".join(x for x in (code, name) if x) or "—"
        if item.get("verified") is False:
            label = f"{label}（待核实）"
        parts.append(label)
    return "、".join(parts) if parts else "—"


def build_policy_watch_email(rows: list[dict[str, Any]]) -> tuple[str, str]:
    if not rows:
        return "[政策雷达]", DISCLAIMER
    first = rows[0]
    first_dir = direction_label(str(first.get("direction") or ""))
    first_title = _short_title(str(first.get("title") or "未命名"))
    if len(rows) == 1:
        subject = f"[政策雷达] {first_dir} · {first_title}"
    else:
        subject = f"[政策雷达] {len(rows)}条可能影响市场 · {first_title}"
    blocks: list[str] = []
    for row in rows:
        direction = direction_label(str(row.get("direction") or ""))
        lines = [
            f"来源：{row.get('source_label') or '—'}",
            f"原文：{article_open_url(str(row.get('url') or '')) or '—'}",
            "",
            str(row.get("summary") or row.get("title") or "").strip(),
            "",
            f"可能方向：{direction}",
            f"相关板块：{_format_sectors(list(row.get('sectors') or []))}",
            f"相关个股：{_format_symbols(list(row.get('symbols') or []))}",
        ]
        if row.get("body_ok") is False:
            lines.append("仅依据标题。")
        blocks.append("\n".join(lines).strip())
    body = "\n\n----\n\n".join(blocks) + f"\n\n{DISCLAIMER}"
    return subject, body
