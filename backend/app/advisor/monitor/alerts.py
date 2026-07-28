"""Assemble and send monitor alert emails (no confirm)."""

from __future__ import annotations

from typing import Any

from ...mail import send_email

_RULE_LABELS = {
    "price_below": "现价 ≤",
    "price_above": "现价 ≥",
    "day_chg_below": "涨跌幅 ≤",
    "day_chg_above": "涨跌幅 ≥",
}


def _fmt_rule(rule: dict[str, Any]) -> str:
    rtype = str(rule.get("type") or "")
    label = _RULE_LABELS.get(rtype, rtype)
    value = rule.get("value")
    hint = rule.get("hint")
    if rtype.startswith("day_chg"):
        try:
            pct = float(value) * 100.0
            body = f"{label} {pct:.2f}%"
        except (TypeError, ValueError):
            body = f"{label} {value}"
    else:
        body = f"{label} {value}"
    if hint:
        body = f"{body}（{hint}）"
    return body


def send_monitor_alert(
    *,
    to: str,
    title: str,
    symbol: str,
    name: str,
    quote: dict[str, Any],
    rule: dict[str, Any],
    job_id: str,
) -> None:
    price = quote.get("price")
    chg = quote.get("day_chg_pct")
    try:
        chg_txt = f"{float(chg) * 100:.2f}%" if chg is not None else "—"
    except (TypeError, ValueError):
        chg_txt = "—"
    try:
        price_txt = f"{float(price):.3f}" if price is not None else "—"
    except (TypeError, ValueError):
        price_txt = "—"

    subject = f"[盯盘] {title} · {name or symbol}({symbol})"
    body = "\n".join(
        [
            f"任务：{title}",
            f"标的：{name or symbol}（{symbol}）",
            f"现价：{price_txt}",
            f"涨跌幅：{chg_txt}",
            f"触发规则：{_fmt_rule(rule)}",
            f"任务 ID：{job_id}",
            "",
            "本邮件由盯盘定时任务自动发送，不会自动下单。",
            "可在顾问前端「定时任务」页暂停或删除该任务。",
        ]
    )
    send_email(to, subject, body)
