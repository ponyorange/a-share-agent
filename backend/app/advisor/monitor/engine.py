"""One tick of monitor job evaluation (rules + optional LLM watch)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .alerts import send_monitor_alert
from .flow import get_flow_snapshot
from .llm_watch import run_llm_watch, should_run_llm_watch
from .rules import (
    FLOW_TYPES,
    cooldown_key,
    evaluate_flow_rule,
    evaluate_rule,
    is_cooled_down,
    mark_cooldown,
)
from .store import list_running_jobs, resolve_symbols, touch_job_run

logger = logging.getLogger(__name__)


def run_monitor_tick(*, quote_limit: int = 200) -> dict[str, int]:
    from ...quote import get_last_quote, trading_session

    stats = {
        "jobs": 0,
        "quotes": 0,
        "alerts": 0,
        "errors": 0,
        "llm_runs": 0,
        "llm_notified": 0,
    }
    session = trading_session()
    if not session.get("is_trading"):
        return stats

    now = datetime.now(timezone.utc)
    quotes_used = 0
    llm_users_done: set[str] = set()

    for job in list_running_jobs():
        stats["jobs"] += 1
        job_id = str(job.get("id") or "")
        user_id = str(job.get("user_id") or "")
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
        quotes_by_symbol: dict[str, dict[str, Any]] = {}
        llm_fields: dict[str, Any] = {}

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
                quotes_by_symbol[symbol] = quote

                name = str(quote.get("name") or symbol)
                for rule in job.get("rules") or []:
                    if not isinstance(rule, dict):
                        continue
                    rtype = str(rule.get("type") or "")
                    flow: dict[str, Any] | None = None
                    if rtype in FLOW_TYPES:
                        try:
                            wd = int(rule.get("window_days") or 5)
                        except (TypeError, ValueError):
                            wd = 5
                        flow = get_flow_snapshot(symbol, window_days=wd)
                        hit = evaluate_flow_rule(rule, flow)
                    else:
                        hit = evaluate_rule(rule, quote)
                    if not hit:
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
                            flow=flow,
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

            # Channel B: LLM watch (at most once per user per tick)
            if (
                job.get("llm_enabled")
                and user_id
                and user_id not in llm_users_done
                and quotes_by_symbol
            ):
                run, pick = should_run_llm_watch(job, quotes_by_symbol, now)
                if run and pick:
                    out = run_llm_watch(
                        user_id,
                        job,
                        pick,
                        quotes_by_symbol,
                        now=now,
                        cooldowns=cooldowns,
                    )
                    llm_users_done.add(user_id)
                    stats["llm_runs"] += 1
                    stats["llm_notified"] += int(out.get("notified") or 0)
                    if out.get("alert_cooldowns") is not None:
                        cooldowns = dict(out["alert_cooldowns"])
                    baselines = dict(job.get("llm_symbol_baselines") or {})
                    for s in pick:
                        chg = (quotes_by_symbol.get(s) or {}).get("day_chg_pct")
                        if chg is not None:
                            try:
                                baselines[s] = float(chg)
                            except (TypeError, ValueError):
                                pass
                    llm_fields = {
                        "last_llm_at": now,
                        "llm_symbol_baselines": baselines,
                        "last_llm_error": out.get("error"),
                    }
                    if out.get("notified"):
                        last_alert_at = now
                    if out.get("error"):
                        stats["errors"] += 1

            fields: dict[str, Any] = {
                "last_run_at": now,
                "alert_cooldowns": cooldowns,
                "last_error": last_error,
                **llm_fields,
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
