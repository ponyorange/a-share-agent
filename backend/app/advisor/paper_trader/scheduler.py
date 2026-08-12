"""Schedule due paper trader cycles from monitor-worker ticks."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ...db import get_db
from ...quote import trading_session
from .cycle import run_paper_trader_cycle
from .defaults import default_paper_trader_config
from .mailer import send_day_end_email, send_halt_email
from .store import list_due_sessions, touch_session

logger = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")


def trading_is_open(now: datetime | None = None) -> bool:
    return bool(trading_session(now).get("is_trading"))


def run_due_paper_traders(*, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stats = {"due": 0, "ran": 0, "errors": 0, "halted": 0, "day_end": 0}
    if not trading_is_open(now):
        return stats

    cfg = default_paper_trader_config()
    limit = int(cfg.get("max_sessions_per_tick") or 3)
    timeout = float(cfg.get("cycle_timeout_sec") or 120)
    due = list_due_sessions(now, limit=limit)
    stats["due"] = len(due)

    for session in due:
        user_id = str(session.get("user_id") or "")
        if not user_id:
            continue
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(run_paper_trader_cycle, session, now=now)
                result = fut.result(timeout=timeout)
            stats["ran"] += 1
            if result.get("halted"):
                stats["halted"] += 1
                try:
                    send_halt_email(
                        session, str(result.get("halt_reason") or "halted")
                    )
                except Exception:
                    logger.exception("halt email failed user=%s", user_id)
        except FuturesTimeout:
            stats["errors"] += 1
            interval = int(session.get("interval_sec") or cfg.get("interval_sec") or 600)
            from datetime import timedelta

            try:
                touch_session(
                    user_id,
                    last_error="cycle_timeout",
                    last_run_at=now,
                    next_run_at=now + timedelta(seconds=max(300, min(900, interval))),
                )
            except Exception:
                logger.exception("timeout touch failed user=%s", user_id)
        except Exception as exc:
            stats["errors"] += 1
            logger.exception("paper trader cycle failed user=%s", user_id)
            try:
                touch_session(user_id, last_error=f"{type(exc).__name__}: {exc}")
            except Exception:
                pass
    return stats


def _shanghai_day(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(SH).date().isoformat()


def finalize_paper_trader_day_ends(*, now: datetime | None = None) -> int:
    """Send day-end emails after the session closes on a trading day."""
    now = now or datetime.now(timezone.utc)
    session = trading_session(now)
    if session.get("is_trading"):
        return 0
    if not session.get("is_trading_day"):
        return 0

    day = _shanghai_day(now)
    sent = 0
    cur = get_db().paper_trader_sessions.find(
        {
            "day_anchor": day,
            "day_end_sent_for": {"$ne": day},
            "status": {"$in": ["running", "paused", "halted", "stopped"]},
        }
    )
    for doc in cur:
        # only if there was activity today
        stats = doc.get("stats_today") or {}
        if int(stats.get("rounds") or 0) <= 0 and doc.get("status") != "halted":
            continue
        pub = {
            "notify_email": doc.get("notify_email"),
            "status": doc.get("status"),
            "day_anchor": doc.get("day_anchor"),
            "stats_today": stats,
        }
        equity_open = doc.get("equity_day_open")
        equity_change = "—"
        try:
            from ..paper import get_account

            acc = get_account(str(doc.get("user_id")), mark_to_market=False)
            cur_eq = float(acc.get("equity") or 0)
            if equity_open is not None:
                equity_change = f"{cur_eq - float(equity_open):+.2f}"
        except Exception:
            pass
        try:
            send_day_end_email(pub, {"day": day, "equity_change": equity_change, "stats_today": stats})
            touch_session(str(doc.get("user_id")), day_end_sent_for=day)
            sent += 1
        except Exception:
            logger.exception("day-end email failed user=%s", doc.get("user_id"))
    return sent
