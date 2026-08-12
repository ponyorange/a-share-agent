"""Halt and day-end emails for paper trader."""

from __future__ import annotations

from typing import Any

from ...mail import send_email


def send_halt_email(session: dict[str, Any], reason: str) -> None:
    to_addr = str(session.get("notify_email") or "").strip()
    if not to_addr:
        return
    subject = "[模拟盘交易员] 已熔断暂停"
    body = "\n".join(
        [
            "模拟盘全自动交易员已进入 halted 状态，不再自动下单。",
            f"原因: {reason}",
            "可在确认后调用 resume（confirm_halt_resume）恢复。",
            "本邮件由系统自动发送，仅限模拟盘。",
        ]
    )
    send_email(to_addr, subject, body)


def send_day_end_email(session: dict[str, Any], summary: dict[str, Any]) -> None:
    to_addr = str(session.get("notify_email") or "").strip()
    if not to_addr:
        return
    stats = summary.get("stats_today") or session.get("stats_today") or {}
    subject = "[模拟盘交易员] 日终简报"
    body = "\n".join(
        [
            f"交易日: {summary.get('day') or session.get('day_anchor') or '—'}",
            f"轮次: {stats.get('rounds', 0)}",
            f"成交: {stats.get('trades', 0)}（买 {stats.get('buys', 0)} / 卖 {stats.get('sells', 0)}）",
            f"风控拦截: {stats.get('blocked', 0)}",
            f"净值变化: {summary.get('equity_change', '—')}",
            f"状态: {session.get('status')}",
            "本邮件由系统自动发送，仅限模拟盘，不构成投资建议。",
        ]
    )
    send_email(to_addr, subject, body)
