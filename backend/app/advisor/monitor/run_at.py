"""Execute run_at monitor jobs via main Agent + email."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ...mail import send_email
from .logs import append_job_log
from .schedule import compute_next_run_at, ensure_utc
from .store import touch_job_run

logger = logging.getLogger(__name__)

RUN_AT_GRACE = timedelta(minutes=10)

# Conversational email-confirm junk often leaked into run_at replies.
_CONFIRM_CUT_PATTERNS = (
    re.compile(r"(?m)^[^\n]*邮件预览[^\n]*$"),
    re.compile(r"(?m)^[^\n]*等您点头[^\n]*$"),
    re.compile(r"(?m)^[^\n]*要发出去吗[^\n]*$"),
    re.compile(r"(?m)^[^\n]*等您确认[^\n]*$"),
    re.compile(r"(?m)^[^\n]*点头我就发[^\n]*$"),
    re.compile(r"📧\s*\*?\*?邮件预览\*?\*?[：:].*", re.DOTALL),
)


def scrub_run_at_email_body(text: str) -> str:
    """Remove chat-style email confirmation / preview wrapper from Agent reply."""
    body = (text or "").strip()
    if not body:
        return body
    # Cut from the earliest confirmation marker to end (keep report above it).
    cut_at: int | None = None
    markers = (
        "📧",
        "邮件预览",
        "等您点头",
        "要发出去吗",
        "这封邮件要发出去吗",
        "点头我就发",
        "邮件已生成预览",
    )
    for marker in markers:
        idx = body.find(marker)
        if idx >= 0 and (cut_at is None or idx < cut_at):
            cut_at = idx
    if cut_at is not None and cut_at > 40:
        body = body[:cut_at].rstrip()
    for pat in _CONFIRM_CUT_PATTERNS:
        body = pat.sub("", body)
    lines = [
        ln
        for ln in body.splitlines()
        if not re.search(r"(要发出去吗|等您点头|等您确认|点头我就发)", ln)
    ]
    body = "\n".join(lines).strip()
    return body or (text or "").strip()


def build_run_at_user_message(title: str, prompt: str) -> str:
    return (
        "【系统：定点定时任务自动执行】\n"
        f"任务名：{title}\n"
        "系统会把你的完整回复直接作为邮件正文发出。"
        "禁止调用发信工具，禁止写邮件预览/确认话术，禁止问是否发送。\n"
        "请按下列要求直接输出报告正文：\n\n"
        f"{prompt.strip()}"
    )


def execute_run_at_job(job: dict[str, Any], *, now: datetime | None = None) -> None:
    """Run one due run_at job: Agent reply → email → advance schedule."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    job_id = str(job.get("id") or "")
    user_id = str(job.get("user_id") or "")
    title = str(job.get("title") or "定点任务")
    prompt = str(job.get("prompt") or "").strip()
    to_addr = str(job.get("notify_email") or "").strip()

    if not job_id or not user_id:
        return

    due = job.get("next_run_at")
    if isinstance(due, str):
        try:
            text = due.replace("Z", "+00:00")
            due = datetime.fromisoformat(text)
        except ValueError:
            due = None
    due_utc = ensure_utc(due) if isinstance(due, datetime) else None
    if due_utc is not None and current - due_utc > RUN_AT_GRACE:
        append_job_log(
            user_id,
            job_id,
            level="warn",
            event="missed",
            message=f"错过定点窗口（宽限 {int(RUN_AT_GRACE.total_seconds())}s）",
        )
        _advance_after_run(job, current, ok=False, error="missed")
        return

    touch_job_run(job_id, status="running", started_at=current, last_error=None)
    append_job_log(
        user_id,
        job_id,
        level="info",
        event="activated",
        message="定点任务开始执行",
    )

    if not prompt:
        append_job_log(
            user_id, job_id, level="error", event="run_failed", message="缺少 prompt"
        )
        _advance_after_run(job, current, ok=False, error="缺少 prompt")
        return
    if not to_addr:
        append_job_log(
            user_id,
            job_id,
            level="error",
            event="run_failed",
            message="notify_email 为空",
        )
        _advance_after_run(job, current, ok=False, error="notify_email 为空")
        return

    try:
        from ..agent.graph import run_agent_chat

        message = build_run_at_user_message(title, prompt)
        result = run_agent_chat(
            user_id, message, run_at_mode=True, persist=False
        )
        reply = scrub_run_at_email_body(
            str(result.get("reply") or "").strip() or "（无正文）"
        )
        subject = f"[定点任务] {title}"
        send_email(to_addr, subject, reply)
        append_job_log(
            user_id,
            job_id,
            level="info",
            event="run_ok",
            message="Agent 已执行并发送邮件",
            detail={"chars": len(reply)},
        )
        _advance_after_run(job, current, ok=True, error=None)
    except Exception as exc:
        logger.exception("run_at failed job=%s", job_id)
        msg = f"{type(exc).__name__}: {exc}"
        append_job_log(
            user_id, job_id, level="error", event="run_failed", message=msg[:500]
        )
        _advance_after_run(job, current, ok=False, error=msg)


def _advance_after_run(
    job: dict[str, Any],
    now: datetime,
    *,
    ok: bool,
    error: str | None,
) -> None:
    job_id = str(job.get("id") or "")
    repeat = str(job.get("repeat") or "once")
    fields: dict[str, Any] = {
        "last_run_at": now,
        "last_error": error,
        "started_at": None,
    }
    if repeat == "once":
        fields["status"] = "completed" if ok or error == "missed" else "failed"
        fields["completed_at"] = now
        fields["next_run_at"] = None
        if fields["status"] == "completed":
            append_job_log(
                str(job.get("user_id") or ""),
                job_id,
                level="info",
                event="completed",
                message="一次性定点任务结束",
            )
    else:
        nxt = compute_next_run_at(job, now=now + timedelta(seconds=1))
        fields["status"] = "scheduled"
        fields["next_run_at"] = ensure_utc(nxt)
        fields["completed_at"] = None
    touch_job_run(job_id, **fields)
