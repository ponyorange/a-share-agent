"""One policy-watch tick: discover, interpret, fan out."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from .config import policy_watch_config
from .discover import collect_due_source_keys, ingest_source
from .fanout import fanout_due_users
from .interpret import interpret_pending

logger = logging.getLogger(__name__)


def run_policy_watch_tick(
    *, now: datetime | None = None, started: float | None = None
) -> dict[str, int]:
    current = now or datetime.now(timezone.utc)
    t0 = started if started is not None else time.monotonic()
    cfg = policy_watch_config()
    budget = float(cfg.get("max_tick_seconds") or 8)
    stats = {
        "sources": 0,
        "articles": 0,
        "interpreted": 0,
        "items": 0,
        "emailed": 0,
        "errors": 0,
    }

    def _over() -> bool:
        return time.monotonic() - t0 > budget

    try:
        specs = collect_due_source_keys(now=current)
    except Exception:
        logger.exception("policy watch collect sources failed")
        stats["errors"] += 1
        specs = []
    for spec in specs:
        if _over():
            break
        try:
            result = ingest_source(spec, now=current)
            stats["sources"] += 1
            stats["articles"] += int(result.get("new_articles") or 0)
            if result.get("error"):
                stats["errors"] += 1
        except Exception:
            logger.exception("policy watch ingest failed")
            stats["errors"] += 1

    if not _over():
        try:
            interpreted = interpret_pending()
            stats["interpreted"] = int(interpreted.get("ok") or 0)
            stats["errors"] += int(interpreted.get("failed") or 0)
        except Exception:
            logger.exception("policy watch interpret failed")
            stats["errors"] += 1

    if not _over():
        try:
            fanout = fanout_due_users(now=current)
            stats["items"] = int(fanout.get("items") or 0)
            stats["emailed"] = int(fanout.get("emailed") or 0)
            stats["errors"] += int(fanout.get("errors") or 0)
        except Exception:
            logger.exception("policy watch fanout failed")
            stats["errors"] += 1
    return stats
