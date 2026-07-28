"""One tick of monitor job evaluation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .alerts import send_monitor_alert
from .rules import cooldown_key, evaluate_rule, is_cooled_down, mark_cooldown
from .store import list_running_jobs, resolve_symbols, touch_job_run

logger = logging.getLogger(__name__)


def run_monitor_tick(*, quote_limit: int = 200) -> dict[str, int]:
    from ...quote import get_last_quote, trading_session

    stats = {"jobs": 0, "quotes": 0, "alerts": 0, "errors": 0}
    session = trading_session()
    if not session.get("is_trading"):
        return stats

    now = datetime.now(timezone.utc)
    quotes_used = 0

    for job in list_running_jobs():
        stats["jobs"] += 1
        job_id = str(job.get("id") or "")
        if not job_id:
            stats["errors"] += 1
            continue
        cooldowns = dict(job.get("alert_cooldowns") or {})
        try:
            cooldown_sec = int(job.get("cooldown_sec") or 1800)
        except (TypeError, ValueError):
            cooldown_sec = 1800
        last_alert_at: datetime | None = None
        last_error: str | None = None
        to_addr = str(job.get("notify_email") or "").strip()
        title = str(job.get("title") or "盯盘任务")

        try:
            symbols = resolve_symbols(job)[:50]
            for symbol in symbols:
                if quotes_used >= quote_limit:
                    break
                try:
                    quote = get_last_quote(symbol)
                except Exception as exc:
                    stats["errors"] += 1
                    last_error = f"{symbol}: {type(exc).__name__}: {exc}"
                    logger.warning("monitor quote failed %s: %s", symbol, exc)
                    continue
                quotes_used += 1
                stats["quotes"] += 1

                name = str(quote.get("name") or symbol)
                for rule in job.get("rules") or []:
                    if not isinstance(rule, dict):
                        continue
                    if not evaluate_rule(rule, quote):
                        continue
                    rid = str(rule.get("id") or "")
                    key = cooldown_key(symbol, rid)
                    if not is_cooled_down(cooldowns, key, now, cooldown_sec):
                        continue
                    if not to_addr:
                        last_error = "notify_email 为空"
                        stats["errors"] += 1
                        continue
                    try:
                        send_monitor_alert(
                            to=to_addr,
                            title=title,
                            symbol=symbol,
                            name=name,
                            quote=quote,
                            rule=rule,
                            job_id=job_id,
                        )
                    except Exception as exc:
                        stats["errors"] += 1
                        last_error = f"mail: {type(exc).__name__}: {exc}"
                        logger.warning(
                            "monitor alert failed job=%s symbol=%s: %s",
                            job_id,
                            symbol,
                            exc,
                        )
                        continue
                    cooldowns = mark_cooldown(cooldowns, key, now)
                    stats["alerts"] += 1
                    last_alert_at = now

            fields: dict[str, Any] = {
                "last_run_at": now,
                "alert_cooldowns": cooldowns,
                "last_error": last_error,
            }
            if last_alert_at is not None:
                fields["last_alert_at"] = last_alert_at
            touch_job_run(job_id, **fields)
        except Exception as exc:
            stats["errors"] += 1
            logger.exception("monitor job failed %s", job_id)
            touch_job_run(
                job_id,
                last_run_at=now,
                last_error=f"{type(exc).__name__}: {exc}",
            )

    return stats
