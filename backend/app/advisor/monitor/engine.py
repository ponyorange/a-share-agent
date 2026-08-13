"""One tick of monitor job evaluation (schedule + rules + optional LLM watch)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .alerts import send_monitor_alert
from .flow import get_flow_snapshot
from .llm_watch import run_llm_watch, should_run_llm_watch
from .logs import append_job_log
from .rules import (
    FLOW_TYPES,
    cooldown_key,
    evaluate_flow_rule,
    evaluate_rule,
    is_cooled_down,
    mark_cooldown,
)
from .run_at import execute_run_at_job
from .schedule import (
    DEFAULT_WATCH_END,
    SH,
    compute_next_run_at,
    ensure_utc,
    format_shanghai,
    in_watch_window,
    shanghai_hhmm_on,
)
from ..calendar_util import is_trading_day
from .store import (
    list_due_scheduled_jobs,
    list_running_jobs,
    resolve_symbols,
    touch_job_run,
)

logger = logging.getLogger(__name__)


def _parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return ensure_utc(raw)
    if isinstance(raw, str):
        try:
            text = raw.strip().replace("Z", "+00:00")
            return ensure_utc(datetime.fromisoformat(text))
        except ValueError:
            return None
    return None


def activate_due_jobs(*, now: datetime | None = None) -> dict[str, int]:
    """Activate scheduled jobs whose next_run_at is due."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    stats = {"activated": 0, "run_at": 0, "completed_early": 0, "missed": 0}
    for job in list_due_scheduled_jobs(current):
        job_id = str(job.get("id") or "")
        user_id = str(job.get("user_id") or "")
        kind = str(job.get("kind") or "watch")
        if not job_id:
            continue
        if kind == "run_at":
            execute_run_at_job(job, now=current)
            stats["run_at"] += 1
            continue
        # watch
        end_at = _parse_dt(job.get("end_at"))
        if end_at is not None and current >= end_at:
            touch_job_run(
                job_id,
                status="completed",
                completed_at=current,
                next_run_at=None,
            )
            append_job_log(
                user_id,
                job_id,
                level="info",
                event="completed",
                message="已过结束时间，未进入盯盘窗口",
            )
            stats["completed_early"] += 1
            continue
        # 盘后/非窗口补跑：勿先 activate 再被 finalize 跳过整天
        if not in_watch_window(job, now=current):
            nxt = compute_next_run_at(job, now=current)
            touch_job_run(
                job_id,
                status="scheduled",
                next_run_at=ensure_utc(nxt),
                started_at=None,
            )
            append_job_log(
                user_id,
                job_id,
                level="warn",
                event="missed",
                message=f"已错过盯盘窗口，下次 {format_shanghai(nxt)}",
            )
            stats["missed"] += 1
            continue
        touch_job_run(
            job_id,
            status="running",
            started_at=current,
            next_run_at=None,
            last_error=None,
        )
        append_job_log(
            user_id,
            job_id,
            level="info",
            event="activated",
            message="盯盘窗口已激活",
        )
        stats["activated"] += 1
    return stats


def finalize_watch_windows(*, now: datetime | None = None) -> dict[str, int]:
    """Close watch windows that have ended (once→completed, recurring→scheduled)."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    sh_now = current.astimezone(SH)
    stats = {"finalized": 0}
    for job in list_running_jobs():
        job_id = str(job.get("id") or "")
        user_id = str(job.get("user_id") or "")
        if not job_id:
            continue
        kind = str(job.get("kind") or "watch")
        if kind != "watch":
            continue
        repeat = str(job.get("repeat") or "recurring")
        end_hhmm = str(job.get("end_time") or DEFAULT_WATCH_END)
        done = False
        if repeat == "once":
            end_at = _parse_dt(job.get("end_at"))
            if end_at is None:
                anchor = str(job.get("anchor_date") or "")[:10]
                if anchor:
                    end_at = ensure_utc(shanghai_hhmm_on(anchor, end_hhmm))
            if end_at is not None and current >= end_at:
                done = True
                touch_job_run(
                    job_id,
                    status="completed",
                    completed_at=current,
                    next_run_at=None,
                    started_at=None,
                )
                append_job_log(
                    user_id,
                    job_id,
                    level="info",
                    event="completed",
                    message="一次性盯盘已结束",
                )
        else:
            cal = str(job.get("calendar") or "trading_days")
            day_ok = cal == "everyday" or bool(is_trading_day(sh_now.date()))
            if day_ok:
                today_end = shanghai_hhmm_on(sh_now.date().isoformat(), end_hhmm)
                if sh_now >= today_end:
                    done = True
            elif sh_now.hour >= 15:
                done = True
            if done:
                nxt = compute_next_run_at(job, now=current)
                touch_job_run(
                    job_id,
                    status="scheduled",
                    next_run_at=ensure_utc(nxt),
                    started_at=None,
                )
                append_job_log(
                    user_id,
                    job_id,
                    level="info",
                    event="completed",
                    message=f"今日盯盘结束，下次 {format_shanghai(nxt)}",
                )
        if done:
            stats["finalized"] += 1
    return stats


def _evaluate_running_watches(
    *,
    now: datetime,
    quote_limit: int,
    stats: dict[str, int],
) -> None:
    from ...quote import get_last_quote

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


def run_monitor_tick(*, quote_limit: int = 200) -> dict[str, int]:
    from ...quote import trading_session

    stats = {
        "jobs": 0,
        "quotes": 0,
        "alerts": 0,
        "errors": 0,
        "llm_runs": 0,
        "llm_notified": 0,
        "activated": 0,
        "missed": 0,
        "run_at": 0,
        "finalized": 0,
    }
    now = datetime.now(timezone.utc)

    act = activate_due_jobs(now=now)
    stats["activated"] = act.get("activated", 0)
    stats["missed"] = act.get("missed", 0)
    stats["run_at"] = act.get("run_at", 0)

    fin = finalize_watch_windows(now=now)
    stats["finalized"] = fin.get("finalized", 0)

    session = trading_session()
    if session.get("is_trading"):
        _evaluate_running_watches(now=now, quote_limit=quote_limit, stats=stats)

    stats.setdefault("paper_trader_runs", 0)
    stats.setdefault("paper_trader_errors", 0)
    stats.setdefault("paper_trader_day_end", 0)
    try:
        from ..paper_trader.scheduler import (
            finalize_paper_trader_day_ends,
            run_due_paper_traders,
        )

        pt = run_due_paper_traders(now=now)
        stats["paper_trader_runs"] = int(pt.get("ran") or 0)
        stats["paper_trader_errors"] = int(pt.get("errors") or 0)
        stats["paper_trader_day_end"] = int(finalize_paper_trader_day_ends(now=now))
    except Exception:
        logger.exception("paper trader tick failed")
        stats["paper_trader_errors"] = int(stats.get("paper_trader_errors") or 0) + 1

    stats.setdefault("signal_graph_evolve", 0)
    try:
        from ..signal_graph.evolve import run_daily_evolve

        evolved = run_daily_evolve(now=now)
        if evolved.get("ok") and not evolved.get("skipped"):
            stats["signal_graph_evolve"] = 1
        elif evolved.get("ok") is False:
            stats["errors"] = int(stats.get("errors") or 0) + 1
    except Exception:
        logger.exception("signal graph evolve failed")
        stats["errors"] = int(stats.get("errors") or 0) + 1

    return stats
