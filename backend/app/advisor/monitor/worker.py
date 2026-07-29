"""Long-running worker: poll monitor schedules and evaluate during trading.

Run: python -m app.advisor.monitor.worker

Must keep running off-hours so scheduled / run_at jobs still activate at next_run_at.
"""

from __future__ import annotations

import logging
import time

from ...quote import trading_session
from .engine import run_monitor_tick

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [monitor-worker] %(message)s",
)
logger = logging.getLogger(__name__)

SLEEP_TRADING_SEC = 25
SLEEP_IDLE_SEC = 60


def main() -> None:
    logger.info("monitor worker started")
    while True:
        try:
            stats = run_monitor_tick()
            is_trading = bool(trading_session().get("is_trading"))
            sleep_sec = SLEEP_TRADING_SEC if is_trading else SLEEP_IDLE_SEC
            logger.info(
                "tick jobs=%s quotes=%s alerts=%s errors=%s "
                "activated=%s run_at=%s finalized=%s sleep=%ss trading=%s",
                stats.get("jobs"),
                stats.get("quotes"),
                stats.get("alerts"),
                stats.get("errors"),
                stats.get("activated"),
                stats.get("run_at"),
                stats.get("finalized"),
                sleep_sec,
                is_trading,
            )
            time.sleep(sleep_sec)
        except Exception:
            logger.exception("monitor tick crashed; sleeping %ss", SLEEP_IDLE_SEC)
            time.sleep(SLEEP_IDLE_SEC)


if __name__ == "__main__":
    main()
