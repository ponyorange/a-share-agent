"""Daily SignalGraph self-evolution: settle due predictions, then generate new ones."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ...quote import trading_session
from .service import generate_signals_batch, settle_due, signal_graph_config
from . import store as graph_store

logger = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")
_CLOSE = time(15, 5)
_LOCK = threading.Lock()


def completed_trade_date(
    now: datetime | None = None,
    *,
    session: dict[str, Any] | None = None,
) -> str | None:
    """Return the session date whose close is available, or None if still trading."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    sh = now.astimezone(SH)
    session = session if session is not None else trading_session(now)
    if session.get("is_trading"):
        return None
    is_td = session.get("is_trading_day")
    if is_td is None:
        from ..calendar_util import is_trading_day

        is_td = is_trading_day(sh.date())
    if is_td and sh.time() >= _CLOSE:
        return sh.date().isoformat()
    from ..calendar_util import last_trading_day

    return last_trading_day(sh.date() - timedelta(days=1))


def collect_evolve_symbols(trade_date: str, limit: int) -> list[str]:
    """Build a capped symbol list without blocking on a cold AKShare pool when possible."""
    cap = max(1, min(int(limit or 40), 80))
    seen: list[str] = []
    bag: set[str] = set()

    def _add(raw: Any) -> bool:
        digits = "".join(ch for ch in str(raw or "").strip() if ch.isdigit())
        if len(digits) != 6 or digits in bag:
            return False
        bag.add(digits)
        seen.append(digits)
        return len(seen) >= cap

    try:
        from ...db import get_db

        cur = (
            get_db()
            .rec_snapshots.find({"trade_date": trade_date[:10]}, {"boards": 1})
            .limit(30)
        )
        for doc in cur:
            for block in (doc.get("boards") or {}).values():
                for item in block.get("items") or []:
                    if _add(item.get("symbol")):
                        return seen
    except Exception:
        logger.debug("evolve rec_snapshots skipped", exc_info=True)

    try:
        from ..universe import peek_board_candidates

        for bid in ("etf", "hs", "star"):
            for row in peek_board_candidates(bid):  # type: ignore[arg-type]
                if _add(row.get("symbol")):
                    return seen
    except Exception:
        logger.debug("evolve peek universe skipped", exc_info=True)

    try:
        owner = signal_graph_config()["owner"]
        _graph, ledger, _meta = graph_store.load_runtime(owner)
        for pred in list(ledger.pending.values()) + list(ledger.unresolved.values()):
            ticker = str(pred.context.ticker).split(".", 1)[0]
            if _add(ticker):
                return seen
    except Exception:
        logger.debug("evolve pending tickers skipped", exc_info=True)

    if not seen:
        try:
            from ..universe import list_board_candidates

            for bid in ("etf", "hs", "star"):
                for row in list_board_candidates(bid, force=False):  # type: ignore[arg-type]
                    if _add(row.get("symbol")):
                        return seen
        except Exception:
            logger.warning("evolve universe build failed", exc_info=True)

    return seen[:cap]


def _last_evolve_date(owner: str) -> str | None:
    _graph, _ledger, meta = graph_store.load_runtime(owner)
    raw = meta.get("last_evolve_date")
    return str(raw)[:10] if raw else None


def _mark_evolved(owner: str, day: str) -> None:
    from .service import _WRITE_LOCK

    with _WRITE_LOCK:
        graph, ledger, meta = graph_store.load_runtime(owner)
        meta = {
            **meta,
            "last_evolve_date": day[:10],
            "last_evolve_at": datetime.now(timezone.utc).isoformat(),
        }
        graph_store.save_runtime(owner, graph, ledger, meta)


def run_daily_evolve(*, now: datetime | None = None) -> dict[str, Any]:
    """Settle matured predictions and register today's signals. Idempotent per trade date."""
    cfg = signal_graph_config()
    if not cfg.get("enabled") or not cfg.get("auto_evolve"):
        return {"ok": True, "skipped": "disabled"}

    now = now or datetime.now(timezone.utc)
    session = trading_session(now)
    day = completed_trade_date(now, session=session)
    if day is None:
        return {"ok": True, "skipped": "trading"}

    owner = str(cfg.get("owner") or "default")
    if _last_evolve_date(owner) == day:
        return {"ok": True, "skipped": "already", "trade_date": day}

    if not _LOCK.acquire(blocking=False):
        return {"ok": True, "skipped": "in_progress", "trade_date": day}
    try:
        settle = settle_due(
            trade_date=day,
            limit=int(cfg.get("evolve_settle_limit") or 200),
        )
        generated_count = 0
        generate_errors: list[Any] = []
        if cfg.get("evolve_generate", True):
            symbols = collect_evolve_symbols(
                day, int(cfg.get("evolve_generate_limit") or 40)
            )
            if symbols:
                batch = generate_signals_batch(
                    symbols, trade_date=day, persist=True
                )
                generated_count = int(batch.get("count") or 0)
                generate_errors = list(batch.get("errors") or [])
        _mark_evolved(owner, day)
        return {
            "ok": True,
            "trade_date": day,
            "settled_count": len(settle.get("settled") or []),
            "unresolved_count": len(settle.get("unresolved") or []),
            "generated_count": generated_count,
            "generate_errors": generate_errors[:10],
            "summary": settle.get("summary"),
        }
    except Exception as exc:
        logger.exception("signal graph evolve failed")
        return {
            "ok": False,
            "trade_date": day,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        _LOCK.release()
