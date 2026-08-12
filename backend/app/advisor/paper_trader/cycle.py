"""One paper-trader decision/execution cycle."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from ...quote import trading_session
from ..paper import PaperOrderBody, get_account, place_order
from .candidates import build_candidates
from .decide import run_llm_decide
from .defaults import default_paper_trader_config
from .risk import filter_intents, should_halt_for_daily_loss
from .store import insert_decision, touch_session

logger = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")

_DEFAULT_RISK = {
    "max_single_position": 0.25,
    "max_total_exposure": 0.90,
    "max_positions": 10,
    "max_trades_per_day": 30,
    "max_daily_loss_pct": 0.05,
    "lot_size": 100,
    "block_limit_board": True,
}


def _quotes_for(symbols: list[str]) -> dict[str, dict[str, Any]]:
    from ...quote import get_last_quote

    out: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        try:
            q = get_last_quote(sym)
            if isinstance(q, dict):
                out[sym] = q
        except Exception as exc:
            out[sym] = {"symbol": sym, "error": f"{type(exc).__name__}: {exc}"}
    return out


def _shanghai_day(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(SH).date().isoformat()


def _ensure_day_fields(
    session: dict[str, Any], account: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Return session field updates for day rollover."""
    day = _shanghai_day(now)
    updates: dict[str, Any] = {}
    if session.get("day_anchor") != day:
        updates["day_anchor"] = day
        try:
            updates["equity_day_open"] = float(account.get("equity") or 0)
        except (TypeError, ValueError):
            updates["equity_day_open"] = None
        updates["stats_today"] = {
            "trades": 0,
            "buys": 0,
            "sells": 0,
            "blocked": 0,
            "llm_calls": 0,
            "rounds": 0,
        }
        updates["day_end_sent_for"] = None
    return updates


def run_paper_trader_cycle(
    session: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    user_id = str(session.get("user_id") or "")
    session_id = str(session.get("id") or session.get("_id") or "")
    run_id = str(uuid4())
    cfg = default_paper_trader_config()
    interval = int(session.get("interval_sec") or cfg.get("interval_sec") or 600)
    interval = max(300, min(900, interval))
    risk = dict(session.get("risk") or cfg.get("risk") or _DEFAULT_RISK)
    mode = str(session.get("mode") or "signal_first")
    next_run = now + timedelta(seconds=interval)

    ts = trading_session(now)
    if not ts.get("is_trading"):
        doc = insert_decision(
            {
                "user_id": user_id,
                "session_id": session_id,
                "run_id": run_id,
                "started_at": now,
                "finished_at": datetime.now(timezone.utc),
                "mode": mode,
                "candidate_symbols": [],
                "signals_summary": {},
                "llm_actions": [],
                "risk_blocked": [],
                "orders_placed": [],
                "skip_reason": "not_trading",
                "error": None,
            }
        )
        touch_session(
            user_id,
            last_run_at=now,
            next_run_at=next_run,
        )
        return {
            "decision_id": doc.get("id"),
            "orders_placed": [],
            "skip_reason": "not_trading",
            "halted": False,
        }

    account = get_account(user_id, mark_to_market=True)
    day_updates = _ensure_day_fields(session, account, now)
    if day_updates:
        touch_session(user_id, **day_updates)
        session = {**session, **day_updates}

    try:
        equity = float(account.get("equity") or 0)
    except (TypeError, ValueError):
        equity = 0.0
    equity_day_open = session.get("equity_day_open")
    if should_halt_for_daily_loss(
        equity=equity,
        equity_day_open=equity_day_open,
        max_daily_loss_pct=float(risk.get("max_daily_loss_pct") or 0.05),
    ):
        touch_session(
            user_id,
            status="halted",
            halt_reason="max_daily_loss_pct",
            next_run_at=None,
            last_run_at=now,
        )
        doc = insert_decision(
            {
                "user_id": user_id,
                "session_id": session_id,
                "run_id": run_id,
                "started_at": now,
                "finished_at": datetime.now(timezone.utc),
                "mode": mode,
                "candidate_symbols": [],
                "signals_summary": {},
                "llm_actions": [],
                "risk_blocked": [],
                "orders_placed": [],
                "skip_reason": "halted_daily_loss",
                "error": None,
            }
        )
        return {
            "decision_id": doc.get("id"),
            "orders_placed": [],
            "skip_reason": "halted_daily_loss",
            "halted": True,
            "halt_reason": "max_daily_loss_pct",
        }

    candidates = build_candidates(user_id, limit=int(cfg.get("candidate_limit") or 40))
    symbols = [str(c["symbol"]) for c in candidates]
    quotes = _quotes_for(symbols)

    stats = dict(session.get("stats_today") or {})
    for k in ("trades", "buys", "sells", "blocked", "llm_calls", "rounds"):
        stats.setdefault(k, 0)

    nudge_n = int(cfg.get("zero_fill_nudge_rounds") or 3)
    consecutive_zero = int(session.get("consecutive_zero_fill") or 0)
    has_directional = any(c.get("direction") in ("buy", "sell") for c in candidates)
    nudge = consecutive_zero >= nudge_n and has_directional

    llm_out = run_llm_decide(
        user_id,
        mode=mode,
        candidates=candidates,
        account=account,
        quotes=quotes,
        nudge=nudge,
    )
    stats["llm_calls"] = int(stats.get("llm_calls") or 0) + 1
    stats["rounds"] = int(stats.get("rounds") or 0) + 1

    llm_fail = int(session.get("consecutive_llm_fail") or 0)
    llm_error = llm_out.get("error")
    if llm_error:
        llm_fail += 1
    else:
        llm_fail = 0

    fail_halt = int(cfg.get("llm_fail_halt_threshold") or 5)
    if llm_fail >= fail_halt:
        touch_session(
            user_id,
            status="halted",
            halt_reason="llm_fail_threshold",
            consecutive_llm_fail=llm_fail,
            stats_today=stats,
            next_run_at=None,
            last_run_at=now,
            last_error=str(llm_error),
        )
        doc = insert_decision(
            {
                "user_id": user_id,
                "session_id": session_id,
                "run_id": run_id,
                "started_at": now,
                "finished_at": datetime.now(timezone.utc),
                "mode": mode,
                "candidate_symbols": symbols,
                "signals_summary": {
                    c["symbol"]: {
                        "direction": c.get("direction"),
                        "rule_score": c.get("rule_score"),
                        "graph_action": c.get("graph_action"),
                    }
                    for c in candidates
                },
                "llm_actions": [],
                "risk_blocked": [],
                "orders_placed": [],
                "skip_reason": "llm_fail_halt",
                "error": llm_error,
            }
        )
        return {
            "decision_id": doc.get("id"),
            "orders_placed": [],
            "skip_reason": "llm_fail_halt",
            "halted": True,
            "halt_reason": "llm_fail_threshold",
        }

    intents = list(llm_out.get("actions") or [])
    trades_today = int(stats.get("trades") or 0)
    allowed, blocked = filter_intents(
        intents,
        account=account,
        quotes_by_symbol=quotes,
        risk=risk,
        trades_today=trades_today,
        equity_day_open=equity_day_open,
    )
    stats["blocked"] = int(stats.get("blocked") or 0) + len(blocked)

    orders_placed: list[dict[str, Any]] = []
    errors: list[str] = []
    for intent in allowed:
        body = PaperOrderBody(
            symbol=intent["symbol"],
            side=intent["side"],  # type: ignore[arg-type]
            qty=float(intent["qty"]),
            price=float((quotes.get(intent["symbol"]) or {}).get("price") or 0)
            or None,
            name=(quotes.get(intent["symbol"]) or {}).get("name"),
        )
        try:
            result = place_order(
                user_id,
                body,
                source="paper_trader",
                external_idempotency_key=(
                    f"paper_trader:{run_id}:{intent['symbol']}:{intent['side']}"
                ),
            )
            trade = result.get("trade") or {}
            orders_placed.append(
                {
                    "trade_id": str(trade.get("_id") or trade.get("id") or ""),
                    "symbol": trade.get("symbol") or intent["symbol"],
                    "side": trade.get("side") or intent["side"],
                    "qty": trade.get("qty") or intent["qty"],
                    "price": trade.get("price"),
                }
            )
            stats["trades"] = int(stats.get("trades") or 0) + 1
            if intent["side"] == "buy":
                stats["buys"] = int(stats.get("buys") or 0) + 1
            else:
                stats["sells"] = int(stats.get("sells") or 0) + 1
        except Exception as exc:
            errors.append(f"{intent['symbol']}:{type(exc).__name__}: {exc}")

    if orders_placed:
        consecutive_zero = 0
    elif has_directional:
        consecutive_zero += 1
    else:
        consecutive_zero = 0

    skip_reason = None
    if not candidates:
        skip_reason = "no_candidates"
    elif llm_error:
        skip_reason = "llm_error"
    elif not intents and not orders_placed:
        skip_reason = "no_actions"
    elif intents and not orders_placed and blocked:
        skip_reason = "all_blocked"

    finished = datetime.now(timezone.utc)
    doc = insert_decision(
        {
            "user_id": user_id,
            "session_id": session_id,
            "run_id": run_id,
            "started_at": now,
            "finished_at": finished,
            "mode": mode,
            "candidate_symbols": symbols,
            "signals_summary": {
                c["symbol"]: {
                    "direction": c.get("direction"),
                    "rule_score": c.get("rule_score"),
                    "graph_action": c.get("graph_action"),
                }
                for c in candidates
            },
            "llm_actions": intents,
            "risk_blocked": blocked,
            "orders_placed": orders_placed,
            "skip_reason": skip_reason,
            "error": "; ".join(errors) if errors else llm_error,
        }
    )
    touch_session(
        user_id,
        last_run_at=now,
        next_run_at=next_run,
        stats_today=stats,
        consecutive_zero_fill=consecutive_zero,
        consecutive_llm_fail=llm_fail,
        last_error=errors[0] if errors else (str(llm_error) if llm_error else None),
    )
    return {
        "decision_id": doc.get("id"),
        "orders_placed": orders_placed,
        "skip_reason": skip_reason,
        "halted": False,
        "risk_blocked": blocked,
    }
